"""
backend/playbooks/intake_profile.py

Handles messages where the user is sharing skin type, concerns,
preferences, or typing free-text "something else" input.

Steps:
  1. Extract structured fields via fast keyword scan (no LLM call)
  2. Merge extracted fields into the profile store
  3. Determine next missing field
  4. Stream a warm acknowledgement + next question via LLM

Keyword extraction replaces the previous LLM-based _extract_fields()
call, cutting the pipeline from 3 LLM calls → 2 LLM calls per turn.
The keyword scan covers the same vocabulary as the taxonomy and handles
natural-language synonyms (breakouts→acne, spots→dark_spots, etc.).
"""

import json
import re
from typing import AsyncGenerator

from backend.profile import (
    compact_profile_for_prompt,
    load_profile,
    parse_profile,
    profile_missing_fields,
    save_profile,
)
from backend.playbooks.base import build_system_prompt, load_prompt, stream_response, emit_ui_data

# ---------------------------------------------------------------------------
# Keyword extraction — no LLM call
# ---------------------------------------------------------------------------

_SKIN_TYPE_KEYWORDS: dict[str, list[str]] = {
    "oily":        ["oily", "greasy", "shiny", "excess oil", "sebum"],
    "dry":         ["dry", "flaky", "tight", "rough", "dehydrated skin"],
    "combination": ["combination", "t-zone", "mixed"],
    "sensitive":   ["sensitive", "reactive", "redness", "easily irritated"],
    "normal":      ["normal skin", "balanced skin", "normal"],  # "normal" matches chip label
}

_CONCERN_KEYWORDS: dict[str, list[str]] = {
    "acne":              ["acne", "breakout", "breaking out", "pimple", "zit",
                          "blemish", "acne spot", "clogged pore",
                          "whitehead", "blackhead"],
    "dark_spots":        ["dark spot", "dark patch", "hyperpigmentation",
                          "marks", "post acne mark", "pimple mark", "scar"],
    "dullness":          ["dull", "no glow", "tired looking", "lack lustre",
                          "lack luster", "no radiance", "uneven glow"],
    "dryness":           ["dry", "flaky", "tight", "rough", "moisturize",
                          "moisture", "hydration", "dehydrated"],
    "excess_oil":        ["oily", "greasy", "excess oil", "shiny",
                          "oil control", "get shiny", "looks shiny"],
    "pigmentation":      ["pigmentation", "uneven tone", "discoloration",
                          "uneven skin", "blotchy"],
    "ageing":            ["ageing", "aging", "wrinkle", "fine line",
                          "anti-age", "collagen", "firm"],
    # " tan" (space before) matches "I tan", "sun tan", "remove sun tan"
    # but NOT "standard" (where 'tan' is preceded by 's', not a space).
    "tanning":           ["tanning", " tan", "tan removal",
                          "remove tan", "sun damage", "dark from sun",
                          "detan", "de-tan", "anti tan", "sun tan"],
    "clogged_pores":     ["pore", "blackhead", "clogged", "open pore"],
    "redness_irritation":["redness", "irritation", "inflamed", "calm",
                          "soothe"],
}

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    # "sun protection" removed — it is a product attribute, not a product name,
    # and causes false-positive matches on "lip balm with sun protection".
    # "sunscreen", "spf", "sunblock" are sufficient to identify intent.
    "sunscreen":   ["sunscreen", "spf", "sunblock", "uv sunscreen", "sun care",
                    "sunscrean"],   # common typo
    "moisturizer": ["moisturizer", "moisturiser", "cream", "lotion",
                    "hydrating cream", "face cream"],
    "face_wash":   ["face wash", "cleanser", "foaming wash", "gel wash",
                    "cleanse"],
    "serum":       ["serum", "essence", "ampoule"],
    "toner":       ["toner", "toning", "mist"],
    "mask":        ["mask", "clay mask", "sheet mask", "face mask"],
    "lip_care":    ["lip balm", "lip care", "lips", "lip mask",
                    "lip gloss", "lip color", "lip colour", "lipbalm", "lipcare"],
    "eye_care":    ["eye cream", "eye care", "under eye", "eye patch"],
    "body_care":   ["body lotion", "body wash", "body care", "body cream"],
    "hair_care":   ["shampoo", "conditioner", "hair oil", "hair care"],
}

_TEXTURE_KEYWORDS: dict[str, list[str]] = {
    "lightweight": ["lightweight", "light", "water-based", "gel",
                    "non-greasy", "oil-free", "quick absorbing",
                    "matte", "matte finish"],   # matte = non-shiny = lightweight
    "rich":        ["rich", "thick", "heavy", "nourishing", "cream",
                    "butter", "dewy", "dewy finish"],   # dewy = hydrating = richer
    "gel":         ["gel", "gel texture", "aqua gel"],
}

_ALLERGEN_KEYWORDS: dict[str, list[str]] = {
    "fragrance":  ["fragrance free", "fragrance-free", "no fragrance",
                   "unscented", "no perfume",
                   # context phrases: user reacts to fragrance
                   "fragrance gives", "fragrance irritat", "fragrance rash",
                   "react to fragrance", "allergic to fragrance",
                   "sensitive to fragrance", "fragrance allerg",
                   "can't use fragrance", "cannot use fragrance"],
    "alcohol":    ["alcohol free", "alcohol-free", "no alcohol"],
    "sulfate":    ["sulfate free", "sulphate free", "no sulfate",
                   "sulfate-free", "sulphate-free"],
    "paraben":    ["paraben free", "paraben-free", "no paraben"],
    "silicone":   ["silicone free", "silicone-free", "no silicone"],
    # "none" = user explicitly has no allergen preferences — marks the field
    # as answered so the question is not asked again (filtered from retrieval
    # in recommend.py since "none" is not a real AllergenClass in the graph).
    "none":       ["none / not sure", "no preference", "no allergen",
                   "no allergies", "no allergy", "not allergic",
                   "doesn't matter", "no restrictions"],
}

# price_tier: map natural-language price mentions to canonical tier values
_PRICE_TIER_KEYWORDS: dict[str, list[str]] = {
    "under_300":  ["under 300", "below 300", "less than 300", "within 300",
                   "₹300", "rs 300", "inr 300", "300 budget",
                   "under 250", "below 250", "very cheap", "cheapest"],
    "under_600":  ["under 600", "below 600", "less than 600", "within 600",
                   "₹600", "rs 600", "inr 600", "under 500", "below 500",
                   "500 budget", "affordable"],
    "under_1000": ["under 1000", "below 1000", "under 800", "below 800",
                   "₹1000", "₹1,000", "₹800", "rs 1000", "rs 800", "inr 1000",
                   "premium", "mid range", "mid-range"],
    "any":        ["no limit", "no budget", "any price", "doesn't matter",
                   "don't mind price", "whatever", "any", "luxury", "high end"],
}

# size_pref: keywords for travel / standard / value sizes
_SIZE_KEYWORDS: dict[str, list[str]] = {
    "travel":   ["travel", "mini", "trial", "sample", "small size",
                 "small pack", "travel size", "travel-size", "on the go"],
    "standard": ["standard", "regular", "normal size", "everyday",
                 "standard size"],
    "value":    ["large", "big", "jumbo", "value", "value pack",
                 "economy", "multi pack", "multipack", "bulk",
                 "pack of 2", "pack of 3", "set of 2", "set of 3"],
}

# Numeric price pattern — maps ₹XXX mentions to the appropriate tier.
# Two-alternative pattern:
#   alt 1: currency symbol prefix (₹/rs/rupees) → allows comma-formatted numbers
#   alt 2: word prefix (under/not more than/budget/…) → no-comma form
_PRICE_NUM_RE = re.compile(
    r"(?:₹|rs\.?|rupees?)\s*([\d,]{3,7})"
    r"|(?:under|below|less than|not more than|within|max|budget|upto|up to)"
    r"\s*(?:₹|rs\.?|rupees?)?\s*(\d{3,5})",
    re.IGNORECASE,
)

_PRICE_NUM_TO_TIER = [
    (300,  "under_300"),
    (600,  "under_600"),
    (1000, "under_1000"),
]


def _price_tier_from_number(amount: int) -> str:
    for threshold, tier in _PRICE_NUM_TO_TIER:
        if amount <= threshold:
            return tier
    return "any"


def keyword_extract(text: str) -> dict:
    """Extract skincare profile fields from text using keyword matching.
    Returns the same dict shape as the old LLM extractor.
    No network call — runs in <1ms.
    """
    t = text.lower()
    result: dict = {}

    skin_types = [k for k, kws in _SKIN_TYPE_KEYWORDS.items()
                  if any(kw in t for kw in kws)]
    if skin_types:
        result["skin_types"] = skin_types

    concerns = [k for k, kws in _CONCERN_KEYWORDS.items()
                if any(kw in t for kw in kws)]
    if concerns:
        result["concerns"] = concerns

    # Pick the category whose LONGEST keyword matched — "eye cream" beats "cream",
    # "face wash" beats "wash", avoiding spurious moisturizer matches.
    # Negation guard: skip a keyword if it is immediately preceded by a negation
    # word (not, no, don't, without, avoid) so "I want lip balm not sunscreen"
    # correctly yields lip_care rather than sunscreen.
    _NEGATION_WORDS = ["not ", "no ", "don't ", "dont ", "without ", "avoid "]

    def _is_negated(text: str, kw: str) -> bool:
        idx = text.find(kw)
        if idx == -1:
            return False
        preceding = text[max(0, idx - 20): idx]
        return any(neg in preceding for neg in _NEGATION_WORDS)

    best_cat, best_len = None, 0
    for cat, kws in _CATEGORY_KEYWORDS.items():
        for kw in kws:
            if kw in t and len(kw) > best_len and not _is_negated(t, kw):
                best_cat, best_len = cat, len(kw)
    if best_cat:
        result["category"] = best_cat

    textures = [k for k, kws in _TEXTURE_KEYWORDS.items()
                if any(kw in t for kw in kws)]
    if textures:
        result["texture"] = textures[0]

    allergens = [k for k, kws in _ALLERGEN_KEYWORDS.items()
                 if any(kw in t for kw in kws)]
    if allergens:
        result["allergen_free"] = allergens

    # price_tier: try keyword phrases first, then numeric amount
    price_tier_hits = [k for k, kws in _PRICE_TIER_KEYWORDS.items()
                       if any(kw in t for kw in kws)]
    if price_tier_hits:
        result["price_tier"] = price_tier_hits[0]
    else:
        num_match = _PRICE_NUM_RE.search(t)
        if num_match:
            # group(1) = ₹-prefixed number (may have commas), group(2) = keyword-prefixed
            raw = (num_match.group(1) or num_match.group(2) or "").replace(",", "")
            try:
                result["price_tier"] = _price_tier_from_number(int(raw))
            except ValueError:
                pass

    # size preference
    size_hits = [k for k, kws in _SIZE_KEYWORDS.items()
                 if any(kw in t for kw in kws)]
    if size_hits:
        result["size_pref"] = size_hits[0]

    return result


# ---------------------------------------------------------------------------
# Main playbook entry point
# ---------------------------------------------------------------------------

async def run(
    profile_id: str,
    user_message: str,
    router_args: dict,
) -> AsyncGenerator[str, None]:
    """Extract profile info via keyword scan, save it, stream next question."""

    # 1. Fast keyword extraction — zero LLM calls
    extracted = keyword_extract(user_message)

    # 1b. Detect persistent sensitivity/allergy flags.
    from backend.sensitivity_memory import detect_sensitivity_flags
    from backend.behavioral_learning import detect_reset_request, reset_behavioral_preferences
    sensitivity_updates = detect_sensitivity_flags(user_message)

    # 1c. Handle "forget my learned preferences" reset.
    if detect_reset_request(user_message):
        reset_behavioral_preferences(profile_id)

    # 2. Update profile (merge both extractions in one write)
    all_updates = {**extracted, **sensitivity_updates}
    if all_updates:
        save_profile(profile_id, all_updates)

    # 3. Reload and check what's still missing
    profile = load_profile(profile_id)
    missing = profile_missing_fields(profile)
    next_field = missing[0] if missing else ""

    # 3b. Once the four required intake fields are filled, hand off to recommend.
    # Budget (price_tier) must be collected before recommendations so it can
    # influence retrieval and ranking — not just hide results afterward.
    # size_pref is no longer asked; it is an optional later refinement.
    parsed_now = parse_profile(profile)
    if (parsed_now.get("category")
            and parsed_now.get("skin_types")
            and parsed_now.get("allergen_free")
            and parsed_now.get("price_tier")):
        from backend.playbooks.recommend import run as recommend_run
        async for token in recommend_run(profile_id, user_message, router_args):
            yield token
        return

    # 4. Build prompt with extracted context
    intake_prompt = load_prompt("intake.md")
    extra = (
        intake_prompt
        .replace("{user_message}", user_message)
        .replace("{extracted_json}", json.dumps(extracted, indent=2))
        .replace("{profile_line}", compact_profile_for_prompt(profile_id))
        .replace("{next_field}", next_field)
    )

    system = build_system_prompt(profile_id, extra_context=extra)

    # 5. Stream response (only LLM call in this playbook)
    async for token in stream_response(system, profile_id, user_message):
        yield token

    # 6. Attach chip suggestions for the next question — lets the widget
    # render tappable options instead of forcing the user to type.
    from backend.chip_options import chips_for_field
    yield emit_ui_data({"suggested_chips": chips_for_field(next_field, profile)})