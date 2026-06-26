"""
backend/playbooks/base.py

Shared helpers for all playbooks — prompt loading, system prompt
assembly, and the common run() interface.
"""

import json
from pathlib import Path
from typing import AsyncGenerator, Optional

from backend.llm_adapter import chat, one_shot
from backend.profile import (
    compact_profile_for_prompt,
    load_history,
    load_session,
)

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def load_prompt(filename: str) -> str:
    return (PROMPTS_DIR / filename).read_text()


def build_system_prompt(
    profile_id: str,
    extra_context: str = "",
) -> str:
    """Assemble the full system prompt for a playbook turn.

    Slots in profile, history, season, and any extra context the
    playbook wants to add (e.g. retrieved candidates).
    """
    base = load_prompt("system.md")
    profile_line = compact_profile_for_prompt(profile_id)
    history = load_history(profile_id)

    system = (
        base
        .replace("{profile_line}", profile_line)
        .replace("{history_block}", history["block"])
        .replace("{season_line}", "")   # filled by session.py at init
    )
    if extra_context:
        system += f"\n\n{extra_context}"
    return system


async def stream_response(
    system: str,
    profile_id: str,
    user_message: str,
    max_tokens: int = 300,
) -> AsyncGenerator[str, None]:
    """Stream tokens from the LLM using the session's conversation history."""
    history = load_session(profile_id)
    messages = history + [{"role": "user", "content": user_message}]
    async for token in chat(system, messages, max_tokens=max_tokens):
        yield token


async def single_response(
    system: str,
    profile_id: str,
    user_message: str,
    max_tokens: int = 300,
) -> str:
    """Single non-streaming LLM call (used for greetings, JSON extraction)."""
    history = load_session(profile_id)
    messages = history + [{"role": "user", "content": user_message}]
    return await one_shot(system, messages, max_tokens=max_tokens)


# -----------------------------------------------------------------------------
# Structured UI data channel
# -----------------------------------------------------------------------------
# Playbooks yield plain text for the visible chat stream. Some turns also
# need to hand the WIDGET structured data — e.g. which chip options to show
# next (intake_profile), or the actual product cards to render (recommend) —
# without that data ever appearing as visible chat text.
#
# Rather than changing every playbook's return type (AsyncGenerator[str, None]
# everywhere, simple to write and test), a playbook that has structured data
# yields ONE final sentinel-prefixed string. app.py's _event_stream detects
# this, strips it from the visible token stream, and merges its contents into
# the SSE "done" event instead.

_SENTINEL = "\u0000UI_DATA\u0000"


def emit_ui_data(data: dict) -> str:
    """Encode a structured payload as a sentinel-prefixed string. A playbook
    yields the result of this as its LAST token."""
    return _SENTINEL + json.dumps(data)


def try_extract_ui_data(token: str) -> Optional[dict]:
    """Return the decoded payload if `token` is a UI-data sentinel, else None.
    Used by app.py to separate structured data from visible chat text."""
    if not token.startswith(_SENTINEL):
        return None
    try:
        return json.loads(token[len(_SENTINEL):])
    except json.JSONDecodeError:
        return None