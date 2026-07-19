"""
backend/playbooks/returning_user.py

Structured "welcome back" flow for returning users. Replaces the previous
behavior where the returning-user greeting asked an open question but any
reply (including chip clicks) fell through to normal classify() — which
usually misrouted into `recommend` with the stale profile, producing
confusing answers ("these niacinamide serums..." with no acknowledgement
of what the user actually said).

Flow (entry chips set by session.py's init_session() for homepage
returning users):

  awaiting_choice
    "Same as before"                       -> awaiting_same_choice
    "Something has changed"                -> awaiting_change_factors_selection
    "Have concerns about a previous..."    -> awaiting_concern_details

  awaiting_same_choice
    "Continue where I left off"  -> recommend.run() with the existing profile
    "Browse something new"       -> category cleared, hand off to intake_profile

  awaiting_change_factors_selection (multi-select chips — one per ranking
  factor: category, skin type, concerns, texture, allergies/sensitivities,
  budget). Selected fields are cleared, then asked one at a time via the
  same chips_for_field() chip data intake_profile already uses, so the
  experience matches normal intake. Once every selected factor has an
  answer, falls through to recommend.run() with the updated profile.

  awaiting_concern_details (free text: "what happened, with which
  product?"). Runs the SAME sensitivity detection intake_profile uses
  (so "I broke out" persists eczema_prone/reactive_skin like normal), and
  feeds the message's extracted tokens into behavioral_learning as a
  "reject" event — teaching the ranker to avoid that ingredient/texture/
  attribute combination going forward, without needing to look up which
  exact SKU the user means.

Flow state lives in the SESSION hash (session:{profile_id}), not the
profile hash — it's ephemeral conversation state, not a durable
preference, and rides the same 30-min TTL refreshed by append_turn().
"""

import json
from typing import AsyncGenerator

from backend.profile import get_redis, load_profile, parse_profile, save_profile
from backend.playbooks.base import emit_ui_data
from backend.chip_options import chips_for_field
from backend.sensitivity_memory import detect_sensitivity_flags
from backend.behavioral_learning import record_behavior
from backend.query_intent import extract_query_tokens
from backend.router import _PRODUCT_CATEGORIES, _RECOMMEND_TRIGGERS

# ---------------------------------------------------------------------------
# Session-scoped flow state
# ---------------------------------------------------------------------------

def _session_key(profile_id: str) -> str:
    return f"session:{profile_id}"


def get_returning_step(profile_id: str) -> str:
    val = get_redis().hget(_session_key(profile_id), "returning_step")
    return val or ""


def set_returning_step(profile_id: str, step: str) -> None:
    get_redis().hset(_session_key(profile_id), "returning_step", step)


def clear_returning_step(profile_id: str) -> None:
    r = get_redis()
    key = _session_key(profile_id)
    r.hdel(key, "returning_step")
    r.hdel(key, "returning_pending_factors")


def _get_pending_factors(profile_id: str) -> list[str]:
    raw = get_redis().hget(_session_key(profile_id), "returning_pending_factors")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


def _set_pending_factors(profile_id: str, factors: list[str]) -> None:
    get_redis().hset(_session_key(profile_id), "returning_pending_factors", json.dumps(factors))


# ---------------------------------------------------------------------------
# Chip definitions
# ---------------------------------------------------------------------------

ENTRY_CHIPS = {
    "field": "returning_check",
    "multi_select": False,
    "options": [
        {"value": "same",     "label": "Same as before"},
        {"value": "changed",  "label": "Something has changed"},
        {"value": "concerns", "label": "Have concerns with a previous purchase"},
    ],
}

SAME_SUBCHIPS = {
    "field": "returning_same_choice",
    "multi_select": False,
    "options": [
        {"value": "continue", "label": "Continue where I left off"},
        {"value": "new",      "label": "Browse something new"},
    ],
}

# Every factor the ranking pipeline uses, mapped to the profile field it edits.
# size_pref deliberately excluded — it's a pack-size/fulfillment preference,
# not one of the documented ranking factors (CLAUDE.md's "Ranking pipeline").
CHANGE_FACTOR_CHIPS = {
    "field": "returning_change_factors",
    "multi_select": True,
    "options": [
        {"value": "category",      "label": "Product category"},
        {"value": "skin_types",    "label": "Skin type"},
        {"value": "concerns",      "label": "Skin concerns"},
        {"value": "texture",       "label": "Texture preference"},
        {"value": "allergen_free", "label": "Allergies / sensitivities"},
        {"value": "price_tier",    "label": "Budget"},
    ],
}

_FACTOR_LABEL_TO_FIELD: dict[str, str] = {
    o["label"].lower(): o["value"] for o in CHANGE_FACTOR_CHIPS["options"]
}

_FACTOR_PROMPTS: dict[str, str] = {
    "category":      "What kind of product are you looking for now?",
    "skin_types":    "What's your skin type now?",
    "concerns":      "What are your main skin concerns now?",
    "texture":       "Any texture preference?",
    "allergen_free": "Anything you'd like the product to be free from?",
    "price_tier":    "What's your budget?",
}


# ---------------------------------------------------------------------------
# Intent escape hatch
# ---------------------------------------------------------------------------

def is_product_intent(message: str) -> bool:
    """True if message looks like a direct product/recommendation request."""
    t = message.lower()
    return (
        any(kw in t for kw in _PRODUCT_CATEGORIES) or
        any(kw in t for kw in _RECOMMEND_TRIGGERS) or
        bool(extract_query_tokens(message))
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def run(profile_id: str, user_message: str, router_args: dict) -> AsyncGenerator[str, None]:
    step = (router_args or {}).get("step") or get_returning_step(profile_id)
    t = (user_message or "").lower()

    if step == "awaiting_choice":
        async for tok in _handle_entry_choice(profile_id, t):
            yield tok
        return

    if step == "awaiting_same_choice":
        async for tok in _handle_same_choice(profile_id, t):
            yield tok
        return

    if step == "awaiting_change_factors_selection":
        async for tok in _handle_factor_selection(profile_id, user_message):
            yield tok
        return

    if step.startswith("awaiting_factor_value:"):
        field = step.split(":", 1)[1]
        async for tok in _handle_factor_value(profile_id, field, user_message):
            yield tok
        return

    if step == "awaiting_concern_details":
        async for tok in _handle_concern_details(profile_id, user_message):
            yield tok
        return

    # No recognized step (e.g. flow state expired) — fall back to normal intake
    clear_returning_step(profile_id)
    from backend.playbooks.intake_profile import run as intake_run
    async for tok in intake_run(profile_id, user_message, router_args or {}):
        yield tok


# ---------------------------------------------------------------------------
# Step handlers
# ---------------------------------------------------------------------------

async def _handle_entry_choice(profile_id: str, t: str) -> AsyncGenerator[str, None]:
    if "concern" in t:
        set_returning_step(profile_id, "awaiting_concern_details")
        yield "I'm sorry to hear that. What happened, and with which product if you remember?"
        return

    if "changed" in t or "different" in t or "something" in t:
        set_returning_step(profile_id, "awaiting_change_factors_selection")
        yield "No problem — what would you like to update?"
        yield emit_ui_data({"suggested_chips": CHANGE_FACTOR_CHIPS})
        return

    if "same" in t:
        set_returning_step(profile_id, "awaiting_same_choice")
        yield "Got it — would you like to continue where you left off, or browse something new?"
        yield emit_ui_data({"suggested_chips": SAME_SUBCHIPS})
        return

    # Unrecognized free text — re-show the entry chips rather than guessing
    yield "Sorry, I didn't catch that — what would you like to do?"
    yield emit_ui_data({"suggested_chips": ENTRY_CHIPS})


async def _handle_same_choice(profile_id: str, t: str) -> AsyncGenerator[str, None]:
    if "new" in t or "browse" in t:
        clear_returning_step(profile_id)
        save_profile(profile_id, {"category": ""})
        from backend.playbooks.intake_profile import run as intake_run
        async for tok in intake_run(profile_id, "", {}):
            yield tok
        return

    if "continue" in t:
        clear_returning_step(profile_id)
        from backend.playbooks.recommend import run as recommend_run
        async for tok in recommend_run(profile_id, "", {}):
            yield tok
        return

    yield "Sorry, I didn't catch that — continue where you left off, or browse something new?"
    yield emit_ui_data({"suggested_chips": SAME_SUBCHIPS})


async def _handle_factor_selection(profile_id: str, user_message: str) -> AsyncGenerator[str, None]:
    t = user_message.lower()
    selected = [field for label, field in _FACTOR_LABEL_TO_FIELD.items() if label in t]

    if not selected:
        yield "Sorry, I didn't catch that — what would you like to update?"
        yield emit_ui_data({"suggested_chips": CHANGE_FACTOR_CHIPS})
        return

    # Clear the selected fields up front so the chips shown for each one
    # reflect a clean slate, not the stale prior value.
    save_profile(profile_id, {f: "" for f in selected})

    first, *rest = selected
    _set_pending_factors(profile_id, rest)
    set_returning_step(profile_id, f"awaiting_factor_value:{first}")
    yield _FACTOR_PROMPTS.get(first, "Let's update that.")
    profile = parse_profile(load_profile(profile_id))
    yield emit_ui_data({"suggested_chips": chips_for_field(first, profile)})


async def _handle_factor_value(profile_id: str, field: str, user_message: str) -> AsyncGenerator[str, None]:
    # Reuse intake_profile's own keyword extraction so a chip-click answer
    # (e.g. "Dry") and free-text answers are both interpreted the same way
    # the normal intake flow already handles them.
    from backend.playbooks.intake_profile import keyword_extract
    extracted = keyword_extract(user_message)

    if field in extracted:
        save_profile(profile_id, {field: extracted[field]})
    elif user_message.strip():
        # keyword_extract didn't resolve a canonical value (e.g. an
        # unmapped chip label) — fall back to storing the raw choice rather
        # than silently dropping the answer.
        value = user_message.strip()
        if field in ("skin_types", "concerns", "allergen_free"):
            value = [value.lower()]
        save_profile(profile_id, {field: value})

    pending = _get_pending_factors(profile_id)
    if pending:
        nxt, *rest = pending
        _set_pending_factors(profile_id, rest)
        set_returning_step(profile_id, f"awaiting_factor_value:{nxt}")
        yield _FACTOR_PROMPTS.get(nxt, "And this?")
        profile = parse_profile(load_profile(profile_id))
        yield emit_ui_data({"suggested_chips": chips_for_field(nxt, profile)})
        return

    # All selected factors updated — show fresh recommendations
    clear_returning_step(profile_id)
    from backend.playbooks.recommend import run as recommend_run
    async for tok in recommend_run(profile_id, "", {}):
        yield tok


async def _handle_concern_details(profile_id: str, user_message: str) -> AsyncGenerator[str, None]:
    clear_returning_step(profile_id)

    # Persist any sensitivity signal in the complaint ("I broke out", "it
    # irritated my skin") exactly like a normal intake message would.
    sensitivity_updates = detect_sensitivity_flags(user_message)
    if sensitivity_updates:
        save_profile(profile_id, sensitivity_updates)

    # Teach the behavioral learner to avoid whatever ingredient/attribute
    # the complaint names, without needing to resolve an exact SKU from
    # history — extract_query_tokens already isolates ingredient/attribute
    # words from free text (the same path query-intent ranking uses).
    tokens = extract_query_tokens(user_message)
    if tokens:
        synthetic_product = {
            "title":       user_message,
            "ingredients": tokens,
            "texture":     "",
            "free_from":   [],
            "variant":     "",
        }
        record_behavior(profile_id, synthetic_product, "reject")

    yield (
        "Thanks for letting me know — I'll factor that in for future recommendations. "
        "For refunds, exchanges, or order-specific issues, our support team can help directly."
    )
