"""
backend/router.py

Two-layer router:

Layer 1 — fast keyword pre-classification (no LLM, <1ms).
Handles unambiguous cases deterministically:
  - product-category words + profile has skin data   → intake_profile
  - "recommend/show me/find me" + profile ready      → recommend
  - ingredient/safety keywords                        → allergen_check
  - routine/layering keywords                         → routine_build
  - order/return/shipping keywords                    → handoff

Layer 2 — LLM tool-calling router (~4s).
Only called when Layer 1 returns None (genuinely ambiguous).
Profile state is injected into the system prompt so the LLM
can make context-aware decisions.
"""

import json
import re
from pathlib import Path

from backend.llm_adapter import route

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_ROUTER_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "router.md"
_ROUTER_SYSTEM: str = ""


def _get_router_system() -> str:
    global _ROUTER_SYSTEM
    if not _ROUTER_SYSTEM:
        _ROUTER_SYSTEM = _ROUTER_PROMPT_PATH.read_text()
    return _ROUTER_SYSTEM


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

ROUTER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "intake_profile",
            "description": (
                "User shared skin type, concerns, preferences, budget, texture, "
                "allergen sensitivities, OR named a product category they want "
                "(e.g. 'looking for a sunscreen', 'need a moisturizer'). "
                "Also use when user typed a free-text 'something else' response."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "raw_input": {
                        "type": "string",
                        "description": "The user's message verbatim",
                    }
                },
                "required": ["raw_input"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recommend",
            "description": (
                "User explicitly wants to see product options — "
                "'show me', 'recommend something', 'what should I use', "
                "'find me something', 'what are my options', 'suggest'. "
                "Only use when user is explicitly asking to be shown products, "
                "not when they are still sharing preferences."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "allergen_check",
            "description": (
                "User asked whether a product contains an ingredient, "
                "or is free of a substance (fragrance-free, alcohol-free, etc.)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ingredient": {
                        "type": "string",
                        "description": "The ingredient or allergen class asked about",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "routine_build",
            "description": "User asked for a full skincare routine or product layering order.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "general_qa",
            "description": (
                "User asked a skincare education question — what an ingredient does, "
                "how to use a product, SPF vs PA+++ differences, etc. "
                "Do NOT use if the user is sharing their own skin info or asking for products."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "handoff",
            "description": (
                "User wants human support — asked about orders, returns, "
                "exchanges, shipping, or expressed frustration wanting escalation."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


# ---------------------------------------------------------------------------
# Layer 1 — fast keyword pre-classification
# ---------------------------------------------------------------------------

_PRODUCT_CATEGORIES = [
    # sunscreen
    "sunscreen", "spf", "sunblock", "sun protection", "sun cream", "sunscrean",
    # moisturiser
    "moisturizer", "moisturiser", "hydrating cream", "face cream",
    # face wash
    "face wash", "cleanser", "foaming wash", "gel wash",
    # serum
    "serum", "essence", "ampoule",
    # toner / mask
    "toner", "mask",
    # lip care — every synonym (including typos/British spellings) so they all
    # route through intake and trigger keyword_extract
    "lip balm", "lip care", "lip mask", "lip gloss", "lip color",
    "lip colour", "lipbalm", "lipcare",
    # eye / body / hair
    "eye cream", "eye care", "under eye",
    "body lotion", "body wash",
    "shampoo", "conditioner", "hair oil",
]

_RECOMMEND_TRIGGERS = [
    "show me", "recommend", "suggest", "what should i", "find me",
    "what are my options", "what works", "best for me", "which one",
    "help me choose", "what do you recommend",
]

_ALLERGEN_TRIGGERS = [
    "contain", "ingredient", "fragrance", "alcohol", "paraben",
    "sulfate", "silicone", "free of", "free from", "check if",
    "does this have", "is this safe",
]

_ROUTINE_TRIGGERS = [
    "routine", "layering", "layer", "what goes first",
    "step", "am pm", "morning night", "sequence",
    "what order", "apply order", "layering order",   # more specific "order" uses
]

_HANDOFF_TRIGGERS = [
    "return", "exchange", "shipping", "delivery", "refund",
    "talk to someone", "speak to", "human", "customer care", "support",
    "where is my order", "track my order", "my order", "track order",
    "where is my", "order status",
]


def _fast_classify(message: str, profile: dict) -> str | None:
    """
    Return playbook name if classification is unambiguous, else None.
    profile is the parsed profile dict from Redis.
    """
    t = message.lower()
    has_skin_data = bool(profile.get("skin_types") or profile.get("concerns"))
    profile_ready  = bool(
        profile.get("category") and
        (profile.get("skin_types") or profile.get("concerns"))
    )

    # Handoff — check first, highest priority
    if any(kw in t for kw in _HANDOFF_TRIGGERS):
        return "handoff"

    # Allergen check — only genuine product queries (always have "?")
    # Preference statements ("Fragrance-free, No sulfates") fall through to intake_profile
    if any(kw in t for kw in _ALLERGEN_TRIGGERS):
        if "?" in message:
            return "allergen_check"

    # Allergen preference mention → intake (user is stating what they want to avoid)
    _ALLERGEN_PREF_KEYWORDS = [
        "fragrance free", "fragrance-free", "no fragrance", "unscented",
        "alcohol free", "alcohol-free", "no alcohol",
        "sulfate free", "sulphate free", "no sulfate", "sulfate-free", "sulphate-free",
        "paraben free", "paraben-free", "no paraben",
        "silicone free", "silicone-free", "no silicone",
        "none / not sure", "no preference", "no allergen",
    ]
    if any(kw in t for kw in _ALLERGEN_PREF_KEYWORDS):
        return "intake_profile"

    # Routine
    if any(kw in t for kw in _ROUTINE_TRIGGERS):
        return "routine_build"

    # Product category mention → ALWAYS intake, even when recommend triggers fire.
    # Category must come before recommend so "suggest lip balms" after a sunscreen
    # session updates the category rather than retrieving stale sunscreen results.
    # Category is the strongest constraint in the system and must never be skipped.
    if any(kw in t for kw in _PRODUCT_CATEGORIES):
        return "intake_profile"

    # Explicit recommend request + profile ready (no new category in message)
    if any(kw in t for kw in _RECOMMEND_TRIGGERS) and profile_ready:
        return "recommend"

    # Skin / profile info shared
    skin_keywords = [
        "skin", "oily", "dry", "combination", "sensitive", "normal",
        "acne", "breakout", "dark spot", "dull", "pigment",
        "pore", "wrinkle", "aging", "ageing", "redness", "dehydrat",
        "tanning", "tan removal", "detan",
    ]
    if any(kw in t for kw in skin_keywords):
        return "intake_profile"

    # Budget / price / size mention → always intake (collecting profile fields)
    profile_field_keywords = [
        "budget", "under ₹", "₹", "rs.", "inr ", "rupees", "price",
        "travel size", "mini size", "standard size", "value pack",
        "no limit", "affordable", "no budget",
    ]
    if any(kw in t for kw in profile_field_keywords):
        return "intake_profile"

    # Bare budget phrase without currency symbol: "under 300", "below 600" etc.
    # keyword_extract handles the number parsing; the router just needs to
    # ensure these reach intake_profile rather than falling to the LLM router.
    if re.search(r'\b(?:under|below|less\s+than|within|upto|up\s+to)\s+\d{3,5}\b', t):
        return "intake_profile"

    return None   # ambiguous → LLM router


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

async def classify(user_message: str, profile: dict | None = None) -> dict:
    """Classify user_message into a playbook.

    Args:
        user_message: The raw user message text.
        profile:      Parsed profile dict from Redis (optional).
                      Passed to both fast-path and LLM router for context.

    Returns:
        {"playbook": "playbook_name", "args": {...}}
    """
    p = profile or {}

    # Layer 1: fast path
    fast = _fast_classify(user_message, p)
    if fast:
        args = {"raw_input": user_message} if fast == "intake_profile" else {}
        return {"playbook": fast, "args": args}

    # Layer 2: LLM router with profile context injected
    from backend.profile import compact_profile_for_prompt
    profile_line = ""
    if p:
        parts = []
        if p.get("skin_types"):
            parts.append("skin:" + "+".join(p["skin_types"] if isinstance(p["skin_types"], list) else p["skin_types"].split(",")))
        if p.get("concerns"):
            parts.append("concerns:" + "+".join(p["concerns"] if isinstance(p["concerns"], list) else p["concerns"].split(",")))
        if p.get("category"):
            parts.append("cat:" + p["category"])
        if parts:
            profile_line = "\n\nCurrent user profile: " + " ".join(parts)

    system = _get_router_system() + profile_line

    result = await route(
        system=system,
        user_message=user_message,
        tools=ROUTER_TOOLS,
    )
    return {
        "playbook": result["tool"],
        "args":     result.get("args", {}),
    }