"""
graph/capability_schema.py

Canonical capability axes for the Dot & Key product knowledge graph.

A capability axis represents a measurable product outcome (0.0–10.0).
Scores are stored as cap_<axis> float properties on Product nodes and
computed by scripts/generate_capability_scores.py.

These constants are the single source of truth shared by:
  - graph/capability_scorer.py          (score computation)
  - scripts/generate_capability_scores.py (ingest)
  - backend/retrieval.py                 (RETURN clause + row parsing)
  - backend/explainability.py            (explanation generation)
"""

# ---------------------------------------------------------------------------
# Capability axes
# ---------------------------------------------------------------------------

CAPABILITY_AXES: list[str] = [
    "oil_control",     # Sebum regulation, mattifying
    "hydration",       # Moisture delivery, humectancy
    "barrier_repair",  # Ceramide/lipid replenishment, protective film
    "brightening",     # Luminosity, glow, radiance
    "pigmentation",    # Dark spot / hyperpigmentation correction
    "acne",            # Breakout prevention and treatment
    "pore_care",       # Pore minimizing, blackhead clearing
    "sensitivity",     # Soothing, anti-inflammatory, redness relief
    "sun_protection",  # UV protection (SPF-bearing products only)
    "lip_repair",      # Lip hydration and barrier restoration
]


def cap_prop(axis: str) -> str:
    """Return the FalkorDB property name for a capability axis.

    e.g. cap_prop("oil_control") → "cap_oil_control"
    """
    return f"cap_{axis}"


# All property names as a list — used in RETURN / SET clauses
CAP_PROPS: list[str] = [cap_prop(a) for a in CAPABILITY_AXES]


# Human-readable display labels for each axis
CAPABILITY_LABELS: dict[str, str] = {
    "oil_control":    "Oil Control",
    "hydration":      "Hydration",
    "barrier_repair": "Barrier Repair",
    "brightening":    "Brightening",
    "pigmentation":   "Pigmentation",
    "acne":           "Acne",
    "pore_care":      "Pore Care",
    "sensitivity":    "Sensitivity",
    "sun_protection": "Sun Protection",
    "lip_repair":     "Lip Repair",
}


# Concern → capability axis mapping (primary axis only; one concern can
# map to multiple axes but one is dominant for ranking explanation)
CONCERN_TO_CAPABILITY: dict[str, list[str]] = {
    "excess_oil":              ["oil_control", "pore_care"],
    "acne":                    ["acne", "oil_control"],
    "clogged_pores":           ["pore_care", "acne"],
    "open_pores":              ["pore_care"],
    "dullness":                ["brightening"],
    "pigmentation":            ["pigmentation", "brightening"],
    "dark_spots":              ["pigmentation", "brightening"],
    "tanning":                 ["brightening", "pigmentation"],
    "dryness":                 ["hydration", "barrier_repair"],
    "dehydration":             ["hydration"],
    "damaged_skin_barrier":    ["barrier_repair", "sensitivity"],
    "redness_irritation":      ["sensitivity"],
    "ageing":                  ["barrier_repair", "brightening"],
    "fine_lines":              ["barrier_repair", "hydration"],
    "dry_lips":                ["lip_repair"],
}
