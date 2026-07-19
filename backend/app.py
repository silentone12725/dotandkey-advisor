"""
backend/app.py

FastAPI application. All routes:
  POST /session/init      — widget load, returns greeting + season
  POST /chat              — main chat endpoint, SSE streaming
  POST /context/product   — product page context + questions
  POST /webhook/order     — Shopify purchase webhook (records P events)
  GET  /health            — readiness check
  GET  /static/widget.js  — widget JS served as static file
  GET  /{path:path}       — reverse proxy to dotandkey.com with widget injected
"""

import json
import logging
import os
import re
from typing import AsyncGenerator

from dotenv import load_dotenv
load_dotenv(override=False)  # override=False so Docker-injected env vars win over .env

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s %(message)s",
)

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.profile import append_turn, record_event
from backend.router import classify
from backend.session import init_session
from backend.context import get_product_context
from backend.playbooks.base import try_extract_ui_data

import backend.playbooks.intake_profile  as pb_intake
import backend.playbooks.recommend       as pb_recommend
import backend.playbooks.other           as pb_other
import backend.playbooks.returning_user  as pb_returning
import backend.comparison_queries        as pb_compare


# ---------------------------------------------------------------------------
# App + CORS
# ---------------------------------------------------------------------------

app = FastAPI(title="Dot & Key Skin Advisor API")

_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")

_origins = [o.strip() for o in
            os.getenv("CORS_ORIGINS",
                      "http://localhost:3000,https://www.dotandkey.com").split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Content-Type", "X-Profile-Id"],
    allow_credentials=False,
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class SessionInitRequest(BaseModel):
    page_context: str = "homepage"   # "homepage" | "product:{name}"


class ChatRequest(BaseModel):
    message: str
    product_context: dict | None = None   # injected by widget on product pages


class ProductContextRequest(BaseModel):
    handle: str
    title: str
    tags: list[str] = []


class OrderWebhookRequest(BaseModel):
    line_items: list[dict]


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Session init
# ---------------------------------------------------------------------------

@app.post("/session/init")
async def session_init(
    body: SessionInitRequest,
    request: Request,
    x_profile_id: str = Header(..., alias="X-Profile-Id"),
):
    client_ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    if not client_ip:
        client_ip = request.client.host if request.client else "127.0.0.1"

    result = await init_session(
        profile_id=x_profile_id,
        client_ip=client_ip,
        page_context=body.page_context,
    )
    return result


# ---------------------------------------------------------------------------
# Chat (SSE)
# ---------------------------------------------------------------------------

PLAYBOOK_MAP = {
    "intake_profile":  lambda pid, msg, args, pctx: pb_intake.run(pid, msg, args),
    "recommend":       lambda pid, msg, args, pctx: pb_recommend.run(pid, msg, args),
    "allergen_check":  lambda pid, msg, args, pctx: pb_other.allergen_check(pid, msg, args, pctx),
    "routine_build":   lambda pid, msg, args, pctx: pb_other.routine_build(pid, msg, args, pctx),
    "general_qa":      lambda pid, msg, args, pctx: pb_other.general_qa(pid, msg, args, pctx),
    "handoff":         lambda pid, msg, args, pctx: pb_other.handoff(pid, msg, args, pctx),
    "track_order":     lambda pid, msg, args, pctx: pb_other.track_order(pid, msg, args, pctx),
    "compare_products":lambda pid, msg, args, pctx: pb_compare.run(pid, msg, args),
    "returning_user":  lambda pid, msg, args, pctx: pb_returning.run(pid, msg, args),
}


async def _event_stream(
    profile_id: str,
    message: str,
    product_context: dict | None,
) -> AsyncGenerator[str, None]:
    """Route message → playbook → stream SSE events."""

    # 1. Load profile for router context (fast Redis read)
    from backend.profile import load_profile, parse_profile
    raw_profile = load_profile(profile_id)
    parsed_profile = parse_profile(raw_profile) if raw_profile else {}

    # 2. Stateful override: an in-progress returning-user flow (set by
    # session.py's init_session) bypasses normal classification entirely.
    # Without this, multi-step chip flows (e.g. "Something has changed" ->
    # pick which factors -> answer each one) would get misrouted by
    # keyword/LLM classification on every turn, since intermediate replies
    # like "Continue where I left off" don't match any router keyword.
    active_returning_step = pb_returning.get_returning_step(profile_id)
    if active_returning_step and not pb_returning.is_product_intent(message):
        playbook_name = "returning_user"
        router_args   = {"step": active_returning_step}
        generator = pb_returning.run(profile_id, message, router_args)
    else:
        if active_returning_step:
            pb_returning.clear_returning_step(profile_id)
        # 3. Classify with profile context
        route = await classify(message, parsed_profile)
        playbook_name = route["playbook"]
        router_args   = route["args"]

        # 4. Get playbook generator
        playbook_fn = PLAYBOOK_MAP.get(playbook_name, PLAYBOOK_MAP["general_qa"])
        generator = playbook_fn(profile_id, message, router_args, product_context)

    # 5. Stream tokens as SSE events. Tokens that are UI-data sentinels
    # (chip suggestions, product cards) are stripped from the visible
    # stream and merged into the done event instead — see
    # backend/playbooks/base.py: emit_ui_data / try_extract_ui_data.
    full_response = []
    done_extra = {}
    async for token in generator:
        ui_data = try_extract_ui_data(token)
        if ui_data is not None:
            done_extra.update(ui_data)
            continue
        full_response.append(token)
        yield f"data: {json.dumps({'token': token})}\n\n"

    # 6. Send done event with metadata + any structured UI data
    done_payload = {"done": True, "playbook": playbook_name, **done_extra}
    yield f"data: {json.dumps(done_payload)}\n\n"

    # 7. Persist turn to session history (after streaming, no latency impact)
    complete_response = "".join(full_response)
    append_turn(profile_id, "user",      message)
    append_turn(profile_id, "assistant", complete_response)


@app.post("/chat")
async def chat(
    body: ChatRequest,
    x_profile_id: str = Header(..., alias="X-Profile-Id"),
):
    return StreamingResponse(
        _event_stream(x_profile_id, body.message, body.product_context),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable Nginx buffering for SSE
        },
    )


# ---------------------------------------------------------------------------
# Product context (product page widget init)
# ---------------------------------------------------------------------------

@app.post("/context/product")
async def product_context(
    body: ProductContextRequest,
    x_profile_id: str = Header(..., alias="X-Profile-Id"),
):
    from backend.profile import load_profile
    profile = load_profile(x_profile_id)
    season = profile.get("season", "summer")
    ctx = get_product_context(body.title, season)
    return ctx


# ---------------------------------------------------------------------------
# Shopify order webhook (records P events in history)
# ---------------------------------------------------------------------------

@app.post("/webhook/order")
async def order_webhook(
    body: OrderWebhookRequest,
    x_profile_id: str = Header(None, alias="X-Profile-Id"),
):
    """Called by Shopify when a purchase completes.
    Shopify doesn't send X-Profile-Id — match by note or customer tag.
    For now: accept profile_id from a custom order note attribute.
    """
    if not x_profile_id:
        return {"status": "skipped", "reason": "no profile_id"}

    for item in body.line_items:
        sku      = item.get("sku", "")
        price    = float(item.get("price", 0))
        category = item.get("product_type", "").lower().replace(" ", "_")
        if sku:
            record_event(x_profile_id, sku, category, "P", price)

    return {"status": "recorded", "items": len(body.line_items)}


# ---------------------------------------------------------------------------
# Explainability API
# ---------------------------------------------------------------------------

@app.get("/explain/{sku}")
async def explain_product(
    sku: str,
    profile_id: str,
):
    from falkordb import FalkorDB
    from backend.explain_api import build_explain_payload

    db = FalkorDB(
        host=os.getenv("FALKORDB_HOST", "localhost"),
        port=int(os.getenv("FALKORDB_PORT", 6379)),
    )
    graph = db.select_graph(os.getenv("FALKORDB_GRAPH", "dotandkey"))

    return build_explain_payload(sku, profile_id, graph)


# ---------------------------------------------------------------------------
# Frontend — widget static file + dotandkey.com reverse proxy
# ---------------------------------------------------------------------------

_DOTANDKEY = "https://www.dotandkey.com"
_STRIP_RESP_HEADERS = {
    "content-encoding", "transfer-encoding", "connection",
    "content-security-policy", "x-frame-options",
}
_STRIP_REQ_HEADERS = {"host", "content-encoding", "transfer-encoding", "connection"}


@app.get("/static/widget.js")
async def serve_widget_js():
    """Serve widget.js before the wildcard proxy route can intercept it."""
    from fastapi.responses import FileResponse
    return FileResponse(
        os.path.join(_FRONTEND_DIR, "widget.js"),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_dotandkey(path: str, request: Request):
    target = f"{_DOTANDKEY}/{path}"
    if request.url.query:
        target += f"?{request.url.query}"

    fwd = {k: v for k, v in request.headers.items()
           if k.lower() not in _STRIP_REQ_HEADERS}
    fwd["host"] = "www.dotandkey.com"
    fwd["accept-encoding"] = "gzip"  # brotli needs extra dep; gzip is enough

    body = await request.body()
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.request(
                request.method, target, headers=fwd, content=body, timeout=10.0
            )
    except Exception as exc:
        return Response(f"Proxy error: {exc}", status_code=502, media_type="text/plain")

    content_type = resp.headers.get("content-type", "")

    # Pass non-HTML responses (images, CSS, JS, fonts) straight through.
    # Also skip Shopify pixel sandbox iframes — they are isolated tracking
    # contexts that don't need the widget and produce noisy duplicate sessions.
    if "text/html" not in content_type or "web-pixels" in path:
        headers = {k: v for k, v in resp.headers.items()
                   if k.lower() not in _STRIP_RESP_HEADERS}
        return Response(resp.content, status_code=resp.status_code,
                        headers=headers, media_type=content_type)

    host = request.headers.get("host", "localhost:8000")
    scheme = request.headers.get("x-forwarded-proto", "http")
    api_base = f"{scheme}://{host}"

    html = resp.text
    # Relative URLs on dotandkey.com resolve against the real domain.
    html = re.sub(r"</head>",
                  f'<base href="{_DOTANDKEY}/">\n</head>',
                  html, count=1, flags=re.IGNORECASE)

    # Rewrite href/action attributes that point at dotandkey.com to full proxy
    # URLs so navigation stays within the injected proxy.  Must use full
    # absolute URLs (not root-relative paths) because <base href> makes even
    # root-relative /paths resolve against dotandkey.com's origin.
    def _proxy_link(m: re.Match) -> str:
        attr, path = m.group(1), m.group(2)
        return f'{attr}="{api_base}{path or "/"}"'

    html = re.sub(
        r'(href|action)="(?:https?:)?//www\.dotandkey\.com(/[^"]*|)"',
        _proxy_link,
        html,
    )

    # Inject widget before </body>.
    # ?v= uses the file's mtime so the browser always fetches the latest widget.js.
    import pathlib as _pl
    _widget_mtime = int(_pl.Path(_FRONTEND_DIR, "widget.js").stat().st_mtime)
    script = (f'<script src="{api_base}/static/widget.js?v={_widget_mtime}"'
              f' data-api-base="{api_base}"></script>')
    html = re.sub(r"</body>", f"{script}\n</body>",
                  html, count=1, flags=re.IGNORECASE)

    return HTMLResponse(html, status_code=resp.status_code)


import pathlib as _pathlib
if _pathlib.Path(_FRONTEND_DIR).is_dir():
    app.mount("/static", StaticFiles(directory=_FRONTEND_DIR), name="static")