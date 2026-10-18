"""
backend/playbooks/other.py

allergen_check, general_qa, handoff, routine_build, track_order playbooks.
allergen_check/general_qa/handoff/routine_build follow the same pattern:
build system prompt, stream response.

track_order is deliberately NOT LLM-streamed (see its docstring) — the
tracking URL must never be paraphrased, dropped, or hallucinated.
"""

import json
from typing import AsyncGenerator

from backend.playbooks.base import (
    build_system_prompt,
    emit_ui_data,
    load_prompt,
    stream_response,
)

from backend.order_tracking import CLICKPOST_BASE_URL

CLICKPOST_TRACKING_URL = f"{CLICKPOST_BASE_URL}/"
TRACK_ORDER_LINK_CHIP = {"label": "Track my order", "url": CLICKPOST_TRACKING_URL}


async def track_order(
    profile_id: str,
    user_message: str,
    router_args: dict,
    product_context: dict | None = None,
) -> AsyncGenerator[str, None]:
    """Order tracking — inline ClickPost lookup; asks for details if missing.

    If the user's message contains an order ID + phone/email (or an AWB),
    we call the ClickPost API directly and return the status inline.
    Otherwise we ask for the details in chat.
    """
    from backend.order_tracking import extract_tracking_info

    info = extract_tracking_info(user_message)
    if info:
        # Delegate the actual ClickPost fetch to the widget (runs in the user's
        # browser where Cloudflare is already cleared). Backend emits a
        # track_request payload; widget fetches and renders the result inline.
        yield "Looking up your order…"
        yield emit_ui_data({"track_request": info, "link_chips": [TRACK_ORDER_LINK_CHIP]})
        return

    # No usable details yet — ask in chat. Nothing is stored (see app.py).
    yield (
        "Happy to help! Please share your order ID and the mobile number "
        "or email used at checkout (or your courier AWB number), and I'll "
        "pull up the status right here. I don't save these details."
    )


async def allergen_check(
    profile_id: str,
    user_message: str,
    router_args: dict,
    product_context: dict | None = None,
) -> AsyncGenerator[str, None]:
    other_prompt = load_prompt("other_playbooks.md")
    # extract just the allergen_check section
    section = other_prompt.split("━━━ TASK: ALLERGEN CHECK ━━━")[1]
    section = section.split("━━━")[0].strip()

    ctx_json = json.dumps(product_context or {}, indent=2)
    extra = (
        "━━━ TASK: ALLERGEN CHECK ━━━\n" + section
        .replace("{user_message}", user_message)
        .replace("{product_context_json}", ctx_json)
    )
    system = build_system_prompt(profile_id, extra_context=extra)
    async for token in stream_response(system, profile_id, user_message):
        yield token


def _fetch_last_product_graph_data(profile_id: str) -> str:
    """Query graph for the last recommended product — injected into general QA
    so questions like 'what ingredients does this have / is it for dry skin'
    are answered from real graph data, not LLM training knowledge."""
    try:
        from backend.profile import load_profile
        from backend.retrieval import get_graph
        raw = load_profile(profile_id)
        skus_raw = raw.get("last_shown_skus", b"[]")
        if isinstance(skus_raw, bytes):
            skus_raw = skus_raw.decode()
        skus = json.loads(skus_raw) if skus_raw else []
        if not skus:
            return ""
        graph = get_graph()
        rows = graph.query(
            "MATCH (p:Product {sku: $sku}) "
            "OPTIONAL MATCH (p)-[:CONTAINS_INGREDIENT]->(i:Ingredient) "
            "OPTIONAL MATCH (p)-[:TARGETS_CONCERN]->(c:Concern) "
            "OPTIONAL MATCH (p)-[:SUITS_SKIN_TYPE]->(s:SkinType) "
            "OPTIONAL MATCH (p)-[:FREE_FROM]->(af:AllergenClass) "
            "OPTIONAL MATCH (p)-[:HAS_CLAIM]->(cl:Claim) "
            "OPTIONAL MATCH (p)-[:HAS_FAQ]->(f:FAQ) "
            "RETURN p.title, p.description, p.ingredients_text, p.benefits_text, "
            "       collect(DISTINCT i.name), collect(DISTINCT c.name), "
            "       collect(DISTINCT s.name), collect(DISTINCT af.name), "
            "       collect(DISTINCT cl.name), collect(DISTINCT f.question)",
            {"sku": skus[0]},
        ).result_set
        if not rows:
            return ""
        r = rows[0]
        parts = [f"Product: {r[0]}"]
        if r[1]: parts.append(f"Description: {r[1]}")
        if r[2]: parts.append(f"Ingredients: {r[2]}")
        elif r[4]: parts.append(f"Key ingredients: {', '.join(r[4])}")
        if r[3]: parts.append(f"Benefits: {r[3]}")
        if r[5]: parts.append(f"Targets: {', '.join(r[5])}")
        if r[6]: parts.append(f"Suitable for: {', '.join(r[6])} skin")
        if r[7]: parts.append(f"Free from: {', '.join(r[7])}")
        if r[8]: parts.append(f"Claims: {', '.join(r[8])}")
        if r[9]: parts.append(f"FAQs: {'; '.join(r[9][:5])}")
        return "\n".join(parts)
    except Exception:
        return ""


async def general_qa(
    profile_id: str,
    user_message: str,
    router_args: dict,
    product_context: dict | None = None,
) -> AsyncGenerator[str, None]:
    other_prompt = load_prompt("other_playbooks.md")
    section = other_prompt.split("━━━ TASK: GENERAL QA ━━━")[1]
    section = section.split("━━━")[0].strip()

    ctx_json = json.dumps(product_context or {}, indent=2)
    extra = (
        "━━━ TASK: GENERAL QA ━━━\n" + section
        .replace("{user_message}", user_message)
        .replace("{product_context_json}", ctx_json)
    )

    # Inject graph data for the last recommended product so product-specific
    # questions ("what ingredients", "is this for dry skin", "how do I use it")
    # are answered from the knowledge graph, not from LLM training data.
    product_graph_data = _fetch_last_product_graph_data(profile_id)
    if product_graph_data:
        extra += f"\n\nProduct knowledge graph data:\n<product_graph>\n{product_graph_data}\n</product_graph>"

    system = build_system_prompt(profile_id, extra_context=extra)
    async for token in stream_response(system, profile_id, user_message):
        yield token


async def handoff(
    profile_id: str,
    user_message: str,
    router_args: dict,
    product_context: dict | None = None,
) -> AsyncGenerator[str, None]:
    other_prompt = load_prompt("other_playbooks.md")
    section = other_prompt.split("━━━ TASK: HANDOFF ━━━")[1].strip()

    extra = (
        "━━━ TASK: HANDOFF ━━━\n" + section
        .replace("{user_message}", user_message)
    )
    system = build_system_prompt(profile_id, extra_context=extra)
    async for token in stream_response(system, profile_id, user_message):
        yield token


async def routine_build(
    profile_id: str,
    user_message: str,
    router_args: dict,
    product_context: dict | None = None,
) -> AsyncGenerator[str, None]:
    """Routine builder — currently deferred (no COMPATIBLE_WITH edges yet).
    Falls back to a helpful explanation."""
    extra = (
        "━━━ TASK: ROUTINE BUILD ━━━\n"
        "The user asked for a full routine. The routine builder is not yet "
        "available. Apologise briefly and naturally, then offer to recommend "
        "individual products in the right category order instead "
        "(cleanser → toner → serum → moisturizer → SPF). "
        "Offer to start with whichever category they need most."
    )
    system = build_system_prompt(profile_id, extra_context=extra)
    async for token in stream_response(system, profile_id, user_message):
        yield token