"""
backend/sensitivity_memory.py

Persistent sensitivity & allergen preference memory.

Two public responsibilities:

  detect_sensitivity_flags(user_message)
      Deterministic keyword scan. Returns a dict of profile field updates
      to persist when the user expresses sensitivity/allergy/eczema/reactive-
      skin concerns, or explicitly revokes a previous preference.

  apply_sensitivity_ranking(products, sensitivity)
      Post-ranking adjustment. Adds memory-based boosts and penalties on top
      of the query-intent final_score already computed by rerank_by_query_intent.
      Called by retrieve() so it runs BEFORE dedupe_top_picks.

Priority rule (enforced by design, not by code):
  Query-intent score (qi × 80) dwarfs memory boosts for precise ingredient
  queries. Memory only dominates when qi = 0 (generic query like
  "recommend a moisturizer"), which is exactly when it should matter.
"""

import logging

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sensitivity profile fields managed by this module
# ---------------------------------------------------------------------------

SENSITIVITY_FLAG_FIELDS: tuple[str, ...] = (
    "avoid_fragrance",
    "avoid_essential_oils",
    "avoid_known_allergens",
    "fragrance_sensitive",
    "allergy_prone",
    "reactive_skin",
    "eczema_prone",
)

# ---------------------------------------------------------------------------
# Detection patterns — each key is a detection phrase; value = flag updates
# "false" strings signal an explicit override/forget (the flag is cleared).
# ---------------------------------------------------------------------------

_FRAGRANCE_SENSITIVITY_PATTERNS: list[str] = [
    "react to fragrance", "react badly to fragrance",
    "fragrance irritates", "fragrance bothers me", "fragrance breaks me out",
    "fragrance gives me", "fragrance causes", "fragrance rash",
    "sensitive to fragrance", "allergic to fragrance",
    "fragrance allerg", "fragrance sensitive",
    "cannot use fragrance", "can't use fragrance", "can not use fragrance",
    "i react to fragrance", "i am sensitive to fragrance",
    "i am allergic to fragrance",
]

_ECZEMA_PATTERNS: list[str] = [
    "eczema", "atopic dermatitis", "atopic skin",
]

_ALLERGY_PATTERNS: list[str] = [
    "have allergies", "have allergy", "skin allergy", "product allergy",
    "i am allergic", "i'm allergic", "allergic to skincare",
    "get rashes", "skin rash from", "rash from skincare",
    "break out from products", "many products break me out",
]

_REACTIVE_SKIN_PATTERNS: list[str] = [
    "reactive skin", "skin reacts easily", "my skin reacts",
    "easily irritated skin", "my skin is reactive",
    "sensitive to most products", "most products irritate",
    "skin gets irritated", "i get rashes",
]

_ESSENTIAL_OIL_PATTERNS: list[str] = [
    "essential oil irritates", "react to essential oils",
    "sensitive to essential oils", "essential oil rash",
    "essential oil allerg",
]

# Forget / override patterns — set flags back to False
_FORGET_FRAGRANCE_PATTERNS: list[str] = [
    "forget my fragrance preference", "fragrance doesn't bother",
    "fragrance does not bother", "no longer sensitive to fragrance",
    "fragrance is fine", "fragrance ok now", "fragrance preference removed",
    "remove fragrance preference", "fragrance is not a problem",
    "fragrance doesn't irritate", "fragrance does not irritate",
]

_FORGET_ALL_PATTERNS: list[str] = [
    "forget my sensitivity", "forget my allergies", "forget my allergy",
    "sensitivity is gone", "not sensitive anymore", "my skin is fine now",
]


def detect_sensitivity_flags(user_message: str) -> dict:
    """Return profile field updates derived from sensitivity/allergy statements.

    Returns an empty dict when no sensitivity signal is detected — callers
    should skip save_profile in that case to avoid unnecessary writes.

    Forget / override patterns return fields set to False (strings "false" for
    Redis compatibility — save_profile handles the serialisation).
    """
    t = user_message.lower()
    flags: dict = {}

    # ── Explicit forget / override ──────────────────────────────────────────
    if any(p in t for p in _FORGET_FRAGRANCE_PATTERNS):
        flags["avoid_fragrance"]    = False
        flags["fragrance_sensitive"] = False

    if any(p in t for p in _FORGET_ALL_PATTERNS):
        for field in SENSITIVITY_FLAG_FIELDS:
            flags[field] = False

    # ── Fragrance sensitivity ────────────────────────────────────────────────
    if any(p in t for p in _FRAGRANCE_SENSITIVITY_PATTERNS):
        flags["fragrance_sensitive"] = True
        flags["avoid_fragrance"]     = True

    # ── Eczema ──────────────────────────────────────────────────────────────
    if any(p in t for p in _ECZEMA_PATTERNS):
        flags["eczema_prone"]    = True
        flags["reactive_skin"]   = True
        flags["avoid_fragrance"] = True

    # ── Allergy ─────────────────────────────────────────────────────────────
    if any(p in t for p in _ALLERGY_PATTERNS):
        flags["allergy_prone"]         = True
        flags["avoid_known_allergens"] = True

    # ── Reactive / easily irritated skin ────────────────────────────────────
    if any(p in t for p in _REACTIVE_SKIN_PATTERNS):
        flags["reactive_skin"]   = True
        flags["avoid_fragrance"] = True

    # ── Essential oils ───────────────────────────────────────────────────────
    if any(p in t for p in _ESSENTIAL_OIL_PATTERNS):
        flags["avoid_essential_oils"] = True

    if flags:
        _log.debug("SENSITIVITY_DETECT | %s", flags)
    return flags


# ---------------------------------------------------------------------------
# Memory-based ranking constants
# ---------------------------------------------------------------------------

# Added directly to final_score (not multiplied by 80, so they are meaningful
# for generic queries (qi≈0) without overwhelming specific ingredient queries).
MEMORY_BOOST_FRAGRANCE_FREE = 200   # FF product when avoid_fragrance=True
MEMORY_BOOST_CERAMIDE       = 80    # ceramide/barrier when reactive/eczema
MEMORY_BOOST_ALLERGEN_FREE  = 80    # any allergen-free product when allergy_prone
MEMORY_PENALTY_FRAGRANCED   = 100   # confirmed-fragranced product when avoid_fragrance

_FRAGRANCED_MARKERS: frozenset[str] = frozenset({"fragrance", "parfum", "perfume"})


def _is_fragrance_free(free_from: list[str]) -> bool:
    return any("fragrance" in ff for ff in free_from)


def _is_confirmed_fragranced(ingredients: list[str]) -> bool:
    """True only when ingredient data is present and confirms fragrance."""
    if not ingredients:
        return False   # no data → don't penalise (conservative)
    joined = " ".join(ingredients)
    return any(m in joined for m in _FRAGRANCED_MARKERS)


def _has_ceramide_or_barrier(title: str, ingredients: list[str]) -> bool:
    markers = {"ceramide", "barrier"}
    return any(m in title for m in markers) or \
           any(any(m in ing for m in markers) for ing in ingredients)


def apply_sensitivity_ranking(
    products: list[dict],
    sensitivity: dict,
) -> list[dict]:
    """Apply persistent memory-based boosts and penalties to final_score.

    Modifies products in place (adds memory_boost, fragrance_penalty,
    allergen_penalty debug fields) and re-sorts by updated final_score.

    Idempotent for empty/all-False sensitivity dicts.
    """
    avoid_fragrance = bool(
        sensitivity.get("avoid_fragrance") or sensitivity.get("fragrance_sensitive")
    )
    reactive = bool(
        sensitivity.get("reactive_skin") or sensitivity.get("eczema_prone")
    )
    allergy_prone = bool(
        sensitivity.get("allergy_prone") or sensitivity.get("avoid_known_allergens")
    )

    if not (avoid_fragrance or reactive or allergy_prone):
        # Ensure debug fields exist even when no-op
        for p in products:
            p.setdefault("memory_boost", 0)
            p.setdefault("fragrance_penalty", 0)
            p.setdefault("allergen_penalty", 0)
        return products

    for p in products:
        free_from   = [f.lower() for f in (p.get("free_from") or [])]
        ingredients = [i.lower() for i in (p.get("ingredients") or [])]
        title       = (p.get("title") or "").lower()

        ff = _is_fragrance_free(free_from)
        fragranced = _is_confirmed_fragranced(ingredients)

        memory_boost     = 0
        fragrance_penalty = 0
        allergen_penalty  = 0

        if avoid_fragrance:
            if ff:
                memory_boost += MEMORY_BOOST_FRAGRANCE_FREE
            elif fragranced:
                fragrance_penalty += MEMORY_PENALTY_FRAGRANCED

        if reactive:
            if _has_ceramide_or_barrier(title, ingredients):
                memory_boost += MEMORY_BOOST_CERAMIDE
            if ff:
                memory_boost += 50   # soft additional boost for reactive skin

        if allergy_prone:
            if ff or len(free_from) >= 2:
                memory_boost += MEMORY_BOOST_ALLERGEN_FREE

        p["memory_boost"]      = memory_boost
        p["fragrance_penalty"] = fragrance_penalty
        p["allergen_penalty"]  = allergen_penalty
        p["final_score"]       = (
            p.get("final_score", 0) + memory_boost - fragrance_penalty - allergen_penalty
        )

        _log.debug(
            "MEMORY_RANK | %-50s | mem_boost=%d | frag_penalty=%d | final=%.0f",
            p.get("title", "?")[:50],
            memory_boost, fragrance_penalty, p["final_score"],
        )

    products.sort(key=lambda p: (-p["final_score"], p.get("price") or 0))
    return products


def sensitivity_from_profile(parsed_profile: dict) -> dict:
    """Extract just the sensitivity flags from a parsed profile dict."""
    return {k: bool(parsed_profile.get(k)) for k in SENSITIVITY_FLAG_FIELDS}
