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
    load_prompt,
    stream_response,
)

# Dot & Key's order tracking is handled by ClickPost, not a Shopify-native
# page — confirmed with the project owner (not discoverable from the repo).
CLICKPOST_TRACKING_URL = "https://dotandkey.clickpost.ai/"


async def track_order(
    profile_id: str,
    user_message: str,
    router_args: dict,
    product_context: dict | None = None,
) -> AsyncGenerator[str, None]:
    """Order tracking — fixed deterministic response, no LLM call.

    Previously "track my order" / "where is my order" fell into the
    generic `handoff` playbook, which just told the user to email support
    — a dead end for a question that already has a real answer (Dot & Key
    uses ClickPost for tracking). Kept deterministic rather than routed
    through stream_response() like the other playbooks here because an
    LLM has no reason to ever touch this URL — there's nothing to phrase.
    """
    yield (
        f"You can track your order here: {CLICKPOST_TRACKING_URL} — "
        "enter your order ID or the phone/email used at checkout to see its status."
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