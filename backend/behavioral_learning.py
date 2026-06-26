"""
backend/behavioral_learning.py

Behavioral preference learning and adaptive ranking.

Observes product interaction events (clicks, purchases, shortlists, rejects,
skips) and converts them into persistent ingredient/texture/claim/attribute
preference signals that boost or penalise products in future recommendations.

Storage: Redis hash at behavioral:{profile_id}
  Field       Content
  ─────────── ────────────────────────────────────────────────────────────
  ingredients '{"niacinamide": {"score": 16.0, "ts": 1751000000}}'
  textures    '{"gel": {"score": 4.0, "ts": 1750000000}}'
  claims      '{"fragrance free": {"score": 6.0, "ts": 1751000000}}'
  attributes  '{"tinted": {"score": 3.0, "ts": 1751000000}}'

Score semantics: positive = preferred, negative = avoided, ±MAX_SCORE clamp.

Ranking priority (guaranteed by score magnitudes):
  explicit query intent (qi × 80 ≈ 8 000+)
  > sensitivity memory (±100–200 direct)
  > behavioral preferences (±15–200 direct)
  > skin/concern/allergen baseline
"""

import json
import logging
import time

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event weights
# ---------------------------------------------------------------------------

EVENT_WEIGHTS: dict[str, float] = {
    "purchase":       10.0,
    "shortlist":       5.0,
    "repeated_click":  4.0,
    "click":           2.0,
    "skip":           -3.0,
    "reject":         -8.0,
}

# ---------------------------------------------------------------------------
# Trackable signals
# ---------------------------------------------------------------------------

# Ingredient/active names worth learning (coarser than the full ingredient list)
_TRACKABLE_INGREDIENTS: frozenset[str] = frozenset({
    "niacinamide", "ceramide", "retinol", "peptide", "caffeine", "squalane",
    "vitamin c", "salicylic acid", "hyaluronic acid", "glycolic acid",
    "alpha arbutin", "kojic acid", "tranexamic acid", "benzoyl peroxide",
    "azelaic acid", "lactic acid",
})

# Product attributes extracted from title / variant
_TRACKABLE_ATTRIBUTES: list[str] = [
    "tinted", "matte", "dewy", "lightweight", "brightening", "hydrating",
    "non comedogenic",
]

# Allergen-class names that appear in free_from lists
_CLAIM_MARKERS: list[str] = [
    "fragrance", "alcohol", "paraben", "sulfate", "silicone",
]

# Phrases that mean "reset all behavioral learning"
_RESET_PATTERNS: list[str] = [
    "forget my learned preferences", "forget my preferences",
    "reset my preferences", "reset recommendations",
    "clear my history", "forget what i like",
    "start fresh", "start over recommendations",
    "recommendation history", "reset recommendation",
    "forget behavioral", "clear preferences",
]

# ---------------------------------------------------------------------------
# Storage constants
# ---------------------------------------------------------------------------

MAX_SCORE          = 20.0            # clamp raw score to ±20
TTL_SECONDS        = 365 * 24 * 3600  # preferences expire after 1 year
_KEY_PREFIX        = "behavioral:"

# Ranking boost: effective_score × multiplier → direct addition to final_score
BOOST_PER_UNIT     = 15.0
PENALTY_PER_UNIT   = 12.0
MAX_BOOST_TOTAL    = 200.0
MAX_PENALTY_TOTAL  = 100.0

# Time decay: (age_days_threshold, multiplier) — sorted ascending
_DECAY_TABLE: list[tuple[float, float]] = [
    (30,  1.0),
    (90,  0.9),
    (180, 0.7),
    (365, 0.5),
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _bkey(profile_id: str) -> str:
    return f"{_KEY_PREFIX}{profile_id}"


def _get_redis():
    from backend.profile import get_redis
    return get_redis()


def _load(profile_id: str, field: str) -> dict:
    raw = _get_redis().hget(_bkey(profile_id), field)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _save(profile_id: str, field: str, data: dict) -> None:
    r = _get_redis()
    key = _bkey(profile_id)
    r.hset(key, field, json.dumps(data))
    r.expire(key, TTL_SECONDS)


def _decay_factor(ts: float, now: float | None = None) -> float:
    age_days = ((now or time.time()) - ts) / 86400
    for cutoff, factor in _DECAY_TABLE:
        if age_days <= cutoff:
            return factor
    return 0.4   # beyond 365 days


def _accumulate(data: dict, signal: str, weight: float) -> None:
    """Add weight to signal's score, clamping to ±MAX_SCORE."""
    now = time.time()
    if signal in data:
        new_score = data[signal]["score"] + weight
        data[signal] = {
            "score": max(-MAX_SCORE, min(MAX_SCORE, new_score)),
            "ts":    now,
        }
    else:
        data[signal] = {
            "score": max(-MAX_SCORE, min(MAX_SCORE, weight)),
            "ts":    now,
        }


# ---------------------------------------------------------------------------
# Signal extraction — what can we learn from a product?
# ---------------------------------------------------------------------------

def _extract_signals(product: dict) -> dict[str, list[str]]:
    """Extract trackable ingredient/texture/claim/attribute signals from a product.

    Only recognises signals from the predefined trackable sets — this keeps
    the learning compact and prevents noisy signals from obscure ingredients.
    """
    ingredients_raw = [i.lower() for i in (product.get("ingredients") or [])]
    free_from       = [f.lower() for f in (product.get("free_from") or [])]
    texture         = (product.get("texture") or "").lower().strip()
    title           = (product.get("title") or "").lower().replace("-", " ")
    variant         = (product.get("variant") or "").lower()

    # Ingredients
    ingredients: list[str] = []
    for raw in ingredients_raw:
        for known in _TRACKABLE_INGREDIENTS:
            if known in raw or raw in known:
                ingredients.append(known)
                break

    # Textures: product.texture field + inferred from title
    textures: list[str] = []
    if texture:
        textures.append(texture)
    for attr in ("gel", "cream", "foam", "lightweight"):
        if attr in title and attr not in textures:
            textures.append(attr)

    # Claims (free_from list)
    claims: list[str] = []
    for ff in free_from:
        for marker in _CLAIM_MARKERS:
            if marker in ff:
                claims.append(f"{marker} free")
                break

    # Attributes from title and variant
    attributes: list[str] = []
    for attr in _TRACKABLE_ATTRIBUTES:
        if attr in title or attr in variant:
            attributes.append(attr)

    return {
        "ingredients": list(set(ingredients)),
        "textures":    list(set(textures)),
        "claims":      list(set(claims)),
        "attributes":  list(set(attributes)),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_behavior(profile_id: str, product: dict, event_type: str) -> None:
    """Record a product interaction event and update behavioral preferences.

    Args:
        profile_id: User identifier.
        product:    Product dict from a retrieval result (needs title,
                    ingredients, texture, free_from, variant fields).
        event_type: "click" | "purchase" | "shortlist" | "repeated_click"
                    | "skip" | "reject"
    """
    weight = EVENT_WEIGHTS.get(event_type)
    if weight is None:
        _log.debug("BEHAVIORAL | unknown event_type=%r — ignored", event_type)
        return

    signals = _extract_signals(product)
    _log.debug("BEHAVIORAL | event=%s product=%s signals=%s",
               event_type, product.get("sku"), signals)

    for field in ("ingredients", "textures", "claims", "attributes"):
        entries = signals.get(field, [])
        if not entries:
            continue
        data = _load(profile_id, field)
        for signal in entries:
            _accumulate(data, signal, weight)
        _save(profile_id, field, data)


def get_behavioral_prefs(profile_id: str) -> dict:
    """Return time-decayed behavioral preferences for a user.

    Returns:
        {
          "ingredients": {"niacinamide": 14.4, "ceramide": 3.5},
          "textures":    {"gel": 3.6},
          "claims":      {"fragrance free": 5.4},
          "attributes":  {"tinted": 2.7},
        }
    Positive values = preferred, negative = avoided.
    Values near 0 are filtered out (|effective| < 0.5).
    """
    prefs: dict[str, dict[str, float]] = {}
    for field in ("ingredients", "textures", "claims", "attributes"):
        data = _load(profile_id, field)
        effective: dict[str, float] = {}
        for signal, entry in data.items():
            eff = entry.get("score", 0.0) * _decay_factor(entry.get("ts", time.time()))
            if abs(eff) >= 0.5:
                effective[signal] = round(eff, 2)
        if effective:
            prefs[field] = effective
    return prefs


def reset_behavioral_preferences(profile_id: str) -> None:
    """Delete all behavioral learning data for this user."""
    _get_redis().delete(_bkey(profile_id))
    _log.debug("BEHAVIORAL | reset for profile_id=%s", profile_id)


def detect_reset_request(user_message: str) -> bool:
    """Return True when the user message asks to forget learned preferences."""
    t = user_message.lower()
    return any(p in t for p in _RESET_PATTERNS)


def apply_behavioral_ranking(
    products: list[dict],
    behavioral_prefs: dict,
) -> list[dict]:
    """Apply learned behavioral preferences as final_score adjustments.

    Called AFTER apply_sensitivity_ranking — query intent and sensitivity
    memory already dominate the score; behavioral learning is the weakest
    signal and only meaningfully affects ranking when qi ≈ 0 (generic query).

    Sets debug fields on every product:
      behavioral_boost   — points added from preferred signals
      behavioral_penalty — points subtracted from avoided signals
      behavior_source    — list of signal descriptions that fired
    """
    if not behavioral_prefs:
        for p in products:
            p.setdefault("behavioral_boost", 0)
            p.setdefault("behavioral_penalty", 0)
            p.setdefault("behavior_source", [])
        return products

    ing_prefs   = behavioral_prefs.get("ingredients", {})
    tex_prefs   = behavioral_prefs.get("textures", {})
    claim_prefs = behavioral_prefs.get("claims", {})
    attr_prefs  = behavioral_prefs.get("attributes", {})

    for p in products:
        signals = _extract_signals(p)
        boost   = 0.0
        penalty = 0.0
        sources: list[str] = []

        for pref_dict, sig_field, label in [
            (ing_prefs,   "ingredients", "preferred_ingredient"),
            (tex_prefs,   "textures",    "preferred_texture"),
            (claim_prefs, "claims",      "preferred_claim"),
            (attr_prefs,  "attributes",  "preferred_attribute"),
        ]:
            for sig in signals.get(sig_field, []):
                eff = pref_dict.get(sig, 0.0)
                if eff > 0.5:
                    b = min(eff * BOOST_PER_UNIT, 100.0)
                    boost += b
                    sources.append(f"{label}:{sig}")
                elif eff < -0.5:
                    pen = min(abs(eff) * PENALTY_PER_UNIT, 60.0)
                    penalty += pen
                    sources.append(f"avoided_{sig_field[:-1]}:{sig}")

        boost   = min(boost,   MAX_BOOST_TOTAL)
        penalty = min(penalty, MAX_PENALTY_TOTAL)

        p["behavioral_boost"]   = int(boost)
        p["behavioral_penalty"] = int(penalty)
        p["behavior_source"]    = sources
        p["final_score"]        = p.get("final_score", 0) + boost - penalty

        _log.debug(
            "BEHAVIORAL_RANK | %-50s | +%d -%d | src=%s",
            p.get("title", "?")[:50], int(boost), int(penalty), sources,
        )

    products.sort(key=lambda p: (-p["final_score"], p.get("price") or 0))
    return products
