"""
backend/playbooks/other.py

allergen_check, general_qa, handoff, routine_build playbooks.
All follow the same pattern: build system prompt, stream response.
"""

import json
from typing import AsyncGenerator

from backend.playbooks.base import (
    build_system_prompt,
    load_prompt,
    stream_response,
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