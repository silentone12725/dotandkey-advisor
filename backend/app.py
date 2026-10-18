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
_log = logging.getLogger(__name__)

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from backend.auth import issue_token, resolve_profile_id
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
    allow_headers=["Content-Type", "X-Profile-Id", "X-Profile-Token"],
    allow_credentials=False,
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

# Trust-boundary caps. Every one of these values reaches Redis, the LLM prompt,
# or the graph, so they are bounded here rather than at each call site.
MAX_MESSAGE_CHARS = 2000
MAX_PAGE_CONTEXT_CHARS = 500
MAX_WEBHOOK_LINE_ITEMS = 100


class ProductContext(BaseModel):
    """Product page context, as produced by /context/product.

    The widget receives this from us and echoes it back on each chat turn, so
    by the time it returns it is client-controlled. It gets json.dumps'd into
    the system prompt (playbooks/other.py), which makes an unconstrained dict a
    direct prompt-injection channel — hence the explicit field list and caps.
    Unknown keys are dropped rather than forwarded.
    """
    model_config = {"extra": "ignore"}

    found: bool = False
    sku: str = Field("", max_length=128)
    title: str = Field("", max_length=300)
    price: float = 0.0
    description: str = Field("", max_length=500)
    texture: str = Field("", max_length=64)
    category: str = Field("", max_length=64)
    skin_types: list[str] = Field(default_factory=list, max_length=20)
    concerns: list[str] = Field(default_factory=list, max_length=30)
    ingredients: list[str] = Field(default_factory=list, max_length=50)
    allergen_free: list[str] = Field(default_factory=list, max_length=20)
    seasons: list[str] = Field(default_factory=list, max_length=8)


class SessionInitRequest(BaseModel):
    page_context: str = Field("homepage", max_length=MAX_PAGE_CONTEXT_CHARS)


class ChatRequest(BaseModel):
    message: str = Field(..., max_length=MAX_MESSAGE_CHARS)
    product_context: ProductContext | None = None   # injected by widget on product pages


class ProductContextRequest(BaseModel):
    handle: str = Field(..., max_length=200)
    title: str = Field("", max_length=300)
    tags: list[str] = Field(default_factory=list, max_length=50)


class OrderWebhookRequest(BaseModel):
    line_items: list[dict]


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

async def current_profile_id(
    x_profile_token: str | None = Header(None, alias="X-Profile-Token"),
    x_profile_id: str | None = Header(None, alias="X-Profile-Id"),
) -> str:
    """Resolve the caller's profile id from their signed token.

    Every endpoint that reads or writes profile-scoped data depends on this,
    so that ownership is proven in exactly one place. A bare X-Profile-Id is
    accepted only under ALLOW_UNSIGNED_PROFILE_ID (migration escape hatch).
    """
    profile_id = resolve_profile_id(x_profile_token, x_profile_id)
    if not profile_id:
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid profile token — call /session/init first.",
        )
    return profile_id


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
    x_profile_token: str | None = Header(None, alias="X-Profile-Token"),
):
    """Start a session and mint the caller's profile token.

    This is the only endpoint that accepts a bare X-Profile-Id, because it is
    what issues the token. A returning caller presents their existing token and
    keeps their profile; a caller without one gets their claimed id bound to a
    fresh token. Claiming someone else's id here yields a token for it, but
    that is no worse than today and is the price of a no-login widget — the
    protection is that ids are unguessable UUIDs and every *other* endpoint now
    demands a signature.
    """
    existing = resolve_profile_id(x_profile_token, None)
    profile_id = existing or x_profile_id

    # X-Forwarded-For is client-settable unless a trusted proxy overwrites it.
    # Honouring it unconditionally lets anyone spoof their location and mint
    # unlimited distinct ip2city:{ip} cache keys / ip-api.com lookups (free
    # tier: 45 req/min), which would push every real user to the fallback city.
    client_ip = ""
    if os.getenv("TRUST_PROXY_HEADERS", "").lower() in ("1", "true", "yes"):
        # Rightmost entry is the one our own proxy appended; earlier hops are
        # attacker-supplied.
        xff = request.headers.get("X-Forwarded-For", "")
        client_ip = xff.split(",")[-1].strip() if xff else ""
    if not client_ip:
        client_ip = request.client.host if request.client else "127.0.0.1"

    result = await init_session(
        profile_id=profile_id,
        client_ip=client_ip,
        page_context=body.page_context,
    )
    # The widget stores this and sends it as X-Profile-Token on every
    # subsequent request; the bare id is no longer sufficient anywhere else.
    result["profile_token"] = issue_token(profile_id)
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
    profile_id: str = Depends(current_profile_id),
):
    return StreamingResponse(
        _event_stream(
            profile_id,
            body.message,
            body.product_context.model_dump() if body.product_context else None,
        ),
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
    profile_id: str = Depends(current_profile_id),
):
    from backend.profile import load_profile
    profile = load_profile(profile_id)
    season = profile.get("season", "summer")
    ctx = get_product_context(body.title, season)
    return ctx


# ---------------------------------------------------------------------------
# Shopify order webhook (records P events in history)
# ---------------------------------------------------------------------------

@app.post("/webhook/order")
async def order_webhook(
    request: Request,
    body: OrderWebhookRequest,
    x_profile_id: str = Header(None, alias="X-Profile-Id"),
):
    """Called by Shopify when a purchase completes.
    Shopify doesn't send X-Profile-Id — match by note or customer tag.
    For now: accept profile_id from a custom order note attribute.

    Writes "P" (purchased) events, which feed behavioural ranking and the
    system prompt, so the request must be proven to come from Shopify.
    """
    if not x_profile_id:
        return {"status": "skipped", "reason": "no profile_id"}

    # Verify Shopify's HMAC. Without this, any unauthenticated POST can forge
    # purchase history for an arbitrary profile id.
    secret = os.getenv("SHOPIFY_WEBHOOK_SECRET", "")
    if secret:
        import base64
        import hashlib
        import hmac
        sent = request.headers.get("X-Shopify-Hmac-Sha256", "")
        digest = base64.b64encode(
            hmac.new(secret.encode(), await request.body(), hashlib.sha256).digest()
        ).decode()
        if not hmac.compare_digest(sent, digest):
            _log.warning("order webhook rejected: bad HMAC for profile %s", x_profile_id)
            return Response('{"status":"unauthorized"}', status_code=401,
                            media_type="application/json")
    else:
        _log.error(
            "SHOPIFY_WEBHOOK_SECRET is unset — /webhook/order is refusing "
            "requests rather than accepting unauthenticated purchase events"
        )
        return Response('{"status":"unconfigured"}', status_code=503,
                        media_type="application/json")

    recorded = 0
    for item in body.line_items[:MAX_WEBHOOK_LINE_ITEMS]:
        sku = str(item.get("sku") or "")[:128]
        if not sku:
            continue
        try:
            price = float(item.get("price", 0))
        except (TypeError, ValueError):
            price = 0.0          # malformed price must not 500 the webhook
        category = str(item.get("product_type") or "").lower().replace(" ", "_")[:64]
        record_event(x_profile_id, sku, category, "P", price)
        recorded += 1

    return {"status": "recorded", "items": recorded}


# ---------------------------------------------------------------------------
# Explainability API
# ---------------------------------------------------------------------------

@app.get("/explain/{sku}")
async def explain_product(
    sku: str,
    profile_id: str = Depends(current_profile_id),
):
    # profile_id used to be a query parameter, so any URL naming someone else's
    # id returned their personalised explanation — and landed in logs and
    # Referer headers on the way. It now comes from the signed token only.
    from backend.explain_api import build_explain_payload
    from backend.retrieval import get_graph

    # Use the shared singleton — constructing FalkorDB() per request opened a
    # new TCP connection each time and exhausted file descriptors under load.
    return build_explain_payload(sku, profile_id, get_graph())


# ---------------------------------------------------------------------------
# Frontend — widget static file + dotandkey.com reverse proxy
# ---------------------------------------------------------------------------

_DOTANDKEY = "https://www.dotandkey.com"
_STRIP_RESP_HEADERS = {
    "content-encoding", "transfer-encoding", "connection",
    "content-security-policy", "x-frame-options",
}

# Allowlist rather than denylist: the proxy talks to a third party, so anything
# not named here (Cookie, Authorization, X-Profile-Id, ...) must not leave our
# origin. A denylist silently forwards every new header a client invents.
_FORWARD_REQ_HEADERS = {
    "accept", "accept-language", "cache-control", "content-type",
    "user-agent", "referer", "pragma",
}


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
           if k.lower() in _FORWARD_REQ_HEADERS}
    fwd["host"] = "www.dotandkey.com"
    fwd["accept-encoding"] = "gzip"  # brotli needs extra dep; gzip is enough

    body = await request.body()
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.request(
                request.method, target, headers=fwd, content=body, timeout=10.0
            )
    except Exception as exc:
        # httpx exception text carries the resolved upstream URL and connection
        # details — log it, don't hand it to the client.
        _log.warning("proxy request to %s failed: %s", target, exc)
        return Response("Upstream unavailable", status_code=502, media_type="text/plain")

    content_type = resp.headers.get("content-type", "")

    # Pass non-HTML responses (images, CSS, JS, fonts) straight through.
    # Also skip Shopify pixel sandbox iframes — they are isolated tracking
    # contexts that don't need the widget and produce noisy duplicate sessions.
    if "text/html" not in content_type or "web-pixels" in path:
        headers = {k: v for k, v in resp.headers.items()
                   if k.lower() not in _STRIP_RESP_HEADERS}
        return Response(resp.content, status_code=resp.status_code,
                        headers=headers, media_type=content_type)

    # api_base is interpolated into the injected <script src> and into every
    # rewritten link, so it must never come from a request header — a spoofed
    # Host would point the widget (and its profile id + chat stream) at an
    # attacker, and a quote in the value would break out of the attribute.
    # PUBLIC_BASE_URL is the deployed origin; the header fallback is dev-only
    # and is scrubbed of anything that isn't host:port.
    api_base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    if not api_base:
        host = request.headers.get("host", "localhost:8000")
        if not re.fullmatch(r"[A-Za-z0-9.\-]+(?::\d{1,5})?", host):
            host = "localhost:8000"
        scheme = "https" if request.url.scheme == "https" else "http"
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


# NOTE: there is deliberately no StaticFiles mount for /static.
# It would be dead code here (the /{path:path} proxy above is registered first
# and wins), and if it were ever moved above the proxy it would publish the
# whole frontend/ directory — node_modules, test_widget.js, demo.html included.
# widget.js is served explicitly by serve_widget_js() instead.