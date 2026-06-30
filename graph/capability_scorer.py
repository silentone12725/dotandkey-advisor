"""
graph/capability_scorer.py  (v2 — composite confidence model)

Computes normalized capability scores (0.0–10.0) WITH per-axis composite
confidence scores (0.0–1.0) and provenance sources for every Product.

Confidence formula (5 independent signals, user-specified weights):

  confidence = (
      0.45 × literature_evidence   (avg confidence from ingredient_knowledge.py)
    + 0.25 × ingredient_role       (primary=1.0, supporting=0.7, incidental=0.3)
    + 0.15 × synergy_bonus         (synergistic pairs present for this axis)
    + 0.10 × count_factor          (more contributing ingredients = more reliable)
    + 0.05 × claim_consistency     (product concern edges align with axis)
  ) - 0.03 × unknown_count         (unknown ingredients reduce confidence slightly)

Unknown ingredients → "unknown contribution", never zero confidence.
Result clamped to [0.10, 1.00].
"""

from __future__ import annotations

from graph.capability_schema import CAPABILITY_AXES, CONCERN_TO_CAPABILITY
from graph.ingredient_knowledge import INGREDIENT_CAPABILITY_EDGES, INGREDIENT_CONCERN_EDGES
from graph.ingredient_synergy import SYNERGY_EDGES
from graph.ingredient_importance import (
    ROLE_MULTIPLIERS, KNOWN_ACTIVES, classify_all_ingredients,
)

# ---------------------------------------------------------------------------
# Pre-indexes
# ---------------------------------------------------------------------------

# ingredient → [(axis, strength, confidence)]
# Confidence comes from INGREDIENT_CONCERN_EDGES via concern→axis mapping
_ING_CAP: dict[str, list[tuple[str, float, float]]] = {}
for _ing, _rel, _axis, _strength in INGREDIENT_CAPABILITY_EDGES:
    _ING_CAP.setdefault(_ing, []).append((_axis, _strength, 0.85))  # default conf

# Overlay literature confidence from concern edges (higher precision)
_ING_CONCERN_CONF: dict[tuple[str, str], float] = {}
for _ing, _rel, _concern, _strength, _conf, _ in INGREDIENT_CONCERN_EDGES:
    for _axis in CONCERN_TO_CAPABILITY.get(_concern, []):
        key = (_ing, _axis)
        if key not in _ING_CONCERN_CONF or _conf > _ING_CONCERN_CONF[key]:
            _ING_CONCERN_CONF[key] = _conf

# Update _ING_CAP with literature confidence where available
_ING_CAP_V2: dict[str, list[tuple[str, float, float]]] = {}
for ing, entries in _ING_CAP.items():
    updated = []
    for axis, strength, _ in entries:
        lit_conf = _ING_CONCERN_CONF.get((ing, axis), 0.80)
        updated.append((axis, strength, lit_conf))
    _ING_CAP_V2[ing] = updated

# frozenset({ing_a, ing_b}) → [concern, ...] → axis set
_SYNERGY_AXES: dict[frozenset, set[str]] = {}
for _a, _b, _ev, _conf, _concerns, _, _ in SYNERGY_EDGES:
    key = frozenset({_a, _b})
    axes: set[str] = set()
    for c in _concerns:
        axes.update(CONCERN_TO_CAPABILITY.get(c, []))
    _SYNERGY_AXES[key] = _SYNERGY_AXES.get(key, set()) | axes

# ---------------------------------------------------------------------------
# Axis ceilings (for 0-10 normalization)
# ---------------------------------------------------------------------------

AXIS_CEILING: dict[str, float] = {
    "oil_control":    4.5,
    "hydration":      4.5,
    "barrier_repair": 4.0,
    "brightening":    4.5,
    "pigmentation":   4.0,
    "acne":           4.0,
    "pore_care":      3.5,
    "sensitivity":    4.0,
    "sun_protection": 10.0,
    "lip_repair":     3.5,
}


def _normalize(raw: float, ceiling: float) -> float:
    if raw <= 0:
        return 0.0
    return round(min(10.0, raw / ceiling * 10.0), 2)


# ---------------------------------------------------------------------------
# Composite confidence per axis
# ---------------------------------------------------------------------------

def _compute_axis_confidence(
    axis: str,
    contributing: list[tuple[str, float, float]],  # (ingredient, strength, lit_conf)
    ingredient_roles: dict[str, tuple[str, str]],   # {ingredient: (role, reason)}
    ing_set: set[str],
    product_concerns: list[str],
    unknown_count: int,
) -> tuple[float, dict]:
    """Compute composite confidence for one capability axis.

    Returns (confidence_float, breakdown_dict).
    """
    if not contributing:
        return 0.0, {}

    # ── Signal 1: Literature evidence (0.45) ──────────────────────────────
    lit_confs = [c for _, _, c in contributing]
    literature_signal = sum(lit_confs) / len(lit_confs) if lit_confs else 0.0

    # ── Signal 2: Ingredient role (0.25) ──────────────────────────────────
    role_scores = []
    for ing, _, _ in contributing:
        role = ingredient_roles.get(ing, ("unknown", ""))[0]
        role_scores.append({"primary": 1.0, "supporting": 0.7, "incidental": 0.3}.get(role, 0.0))
    role_signal = max(role_scores) if role_scores else 0.0

    # ── Signal 3: Synergy bonus (0.15) ────────────────────────────────────
    synergy_count = 0
    for pair, axes in _SYNERGY_AXES.items():
        if axis in axes and pair.issubset(ing_set):
            synergy_count += 1
    synergy_signal = min(1.0, synergy_count * 0.5)

    # ── Signal 4: Count factor (0.10) ─────────────────────────────────────
    count_signal = min(1.0, len(contributing) / 3.0)

    # ── Signal 5: Claim consistency (0.05) ────────────────────────────────
    axes_from_concerns: set[str] = set()
    for concern in product_concerns:
        axes_from_concerns.update(CONCERN_TO_CAPABILITY.get(concern, []))
    claim_signal = 1.0 if axis in axes_from_concerns else 0.5

    # ── Composite ──────────────────────────────────────────────────────────
    composite = (
        0.45 * literature_signal
        + 0.25 * role_signal
        + 0.15 * synergy_signal
        + 0.10 * count_signal
        + 0.05 * claim_signal
    ) - 0.03 * unknown_count

    confidence = round(max(0.10, min(1.00, composite)), 3)

    breakdown = {
        "literature_evidence": round(literature_signal, 3),
        "ingredient_role":     round(role_signal, 3),
        "synergy_bonus":       round(synergy_signal, 3),
        "count_factor":        round(count_signal, 3),
        "claim_consistency":   round(claim_signal, 3),
        "unknown_penalty":     round(-0.03 * unknown_count, 3),
    }
    return confidence, breakdown


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_product(product: dict) -> dict[str, float]:
    """Backward-compatible: return {axis: score_0_to_10}."""
    full = score_product_v2(product)
    return {ax: full[ax]["score"] for ax in CAPABILITY_AXES}


def score_product_v2(product: dict) -> dict[str, dict]:
    """Return full capability profile with confidence and provenance.

    Returns:
        {
          axis: {
            "score":      float 0–10,
            "confidence": float 0–1,
            "sources":    ["niacinamide:0.90", "zinc_oxide:0.70"],
            "unknown_count": int,
            "confidence_breakdown": {...},
          }
        }
    """
    ingredients = [i.lower() for i in (product.get("ingredients") or [])]
    concerns    = [c.lower() for c in (product.get("matched_concerns") or [])]
    category    = (product.get("category_raw") or "").lower()
    title       = product.get("title", "")

    ing_set = set(ingredients)

    # Ingredient roles (use pre-computed if available, else classify inline)
    ingredient_roles: dict[str, tuple[str, str]] = product.get("_ingredient_roles") or {}
    if not ingredient_roles:
        ingredient_roles = classify_all_ingredients(ingredients, title)

    unknown_count = sum(
        1 for ing in ingredients if ing not in KNOWN_ACTIVES and ing not in ingredient_roles
    )

    # Accumulate raw scores and contributors per axis
    raw: dict[str, float] = {ax: 0.0 for ax in CAPABILITY_AXES}
    contributors: dict[str, list[tuple[str, float, float]]] = {ax: [] for ax in CAPABILITY_AXES}

    for ing in ingredients:
        role = ingredient_roles.get(ing, ("unknown", ""))[0]
        multiplier = ROLE_MULTIPLIERS.get(role, 0.0)
        if multiplier == 0.0:
            continue
        for axis, strength, lit_conf in _ING_CAP_V2.get(ing, []):
            contribution = strength * multiplier
            raw[axis] += contribution
            contributors[axis].append((ing, strength, lit_conf))

    # Concern → capability bonus
    for concern in concerns:
        for axis in CONCERN_TO_CAPABILITY.get(concern, []):
            raw[axis] += 0.3

    # Synergy bonus
    for pair, axes in _SYNERGY_AXES.items():
        if pair.issubset(ing_set):
            for axis in axes:
                raw[axis] += 0.4

    # Category base scores
    if "sunscreen" in category:
        raw["sun_protection"] = max(raw["sun_protection"], 7.0)
    if "lip" in category:
        raw["lip_repair"] = max(raw["lip_repair"], 3.0)

    # Build output
    result: dict[str, dict] = {}
    for ax in CAPABILITY_AXES:
        ceiling = AXIS_CEILING.get(ax, 3.5)
        score   = _normalize(raw[ax], ceiling)
        conf, breakdown = _compute_axis_confidence(
            ax, contributors[ax], ingredient_roles,
            ing_set, concerns, unknown_count,
        )
        sources = [f"{ing}:{strength:.2f}" for ing, strength, _ in contributors[ax]]
        result[ax] = {
            "score":                score,
            "confidence":           conf,
            "sources":              sources,
            "unknown_count":        unknown_count,
            "confidence_breakdown": breakdown,
        }

    return result


def explain_capability(product: dict, axis: str) -> list[str]:
    """Return ingredient-evidence strings for a given axis (unchanged API)."""
    ingredients = [i.lower() for i in (product.get("ingredients") or [])]
    evidence = []
    for ing in ingredients:
        for ax, strength, _ in _ING_CAP_V2.get(ing, []):
            if ax == axis:
                evidence.append(
                    f"{ing.replace('_', ' ').title()} → {axis.replace('_', ' ')} "
                    f"(strength: {strength})"
                )
    return evidence
