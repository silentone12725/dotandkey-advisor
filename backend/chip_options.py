"""
backend/chip_options.py

Defines the chip options the widget shows for each profile field the
intake_profile playbook asks about. Kept separate from questions.py
(which generates product-page FAQ chips) since these are intake-flow
chips, a different concern.

Free-text "Something else" options are intentionally omitted — the
widget's main text input is always visible and available for anything
not covered by the chips.

Availability filtering: chips_for_field(field, profile) drops options
for which no products exist in the graph given the current profile
selections (e.g. if there are no Normal-skin sunscreens, the Normal
chip is omitted when category=sunscreen).  The availability matrix is
loaded from the graph once and cached for the process lifetime.
"""

import os
from typing import Optional

CATEGORY_CHIPS = [
    {"value": "sunscreen",   "label": "Sunscreen"},
    {"value": "moisturizer", "label": "Moisturizer"},
    {"value": "face_wash",   "label": "Face wash"},
    {"value": "serum",       "label": "Serum"},
    {"value": "lip_care",    "label": "Lip care"},
    {"value": "eye_care",    "label": "Eye care"},
]

SKIN_TYPE_CHIPS = [
    {"value": "oily",        "label": "Oily"},
    {"value": "dry",         "label": "Dry"},
    {"value": "combination", "label": "Combination"},
    {"value": "sensitive",   "label": "Sensitive"},
    {"value": "normal",      "label": "Normal"},
]

CONCERN_CHIPS = [
    {"value": "acne",        "label": "Acne / breakouts"},
    {"value": "dark_spots",  "label": "Dark spots"},
    {"value": "dullness",    "label": "Dullness"},
    {"value": "dryness",     "label": "Dryness"},
    {"value": "pigmentation","label": "Uneven tone"},
    {"value": "ageing",      "label": "Fine lines"},
]

TEXTURE_CHIPS = [
    {"value": "lightweight", "label": "Light / gel"},
    {"value": "rich",        "label": "Rich / cream"},
    {"value": "no_preference","label": "No preference"},
]

ALLERGEN_CHIPS = [
    {"value": "fragrance",   "label": "Fragrance-free"},
    {"value": "alcohol",     "label": "No alcohol"},
    {"value": "sulfate",     "label": "No sulfates"},
    {"value": "none",        "label": "None / not sure"},
]

PRICE_TIER_CHIPS = [
    {"value": "under_300",  "label": "Under ₹300"},
    {"value": "under_600",  "label": "Under ₹600"},
    {"value": "under_1000", "label": "Under ₹1,000"},
    {"value": "any",        "label": "No budget preference"},
]

SIZE_CHIPS = [
    {"value": "travel",   "label": "Travel / mini size"},
    {"value": "standard", "label": "Standard size"},
    {"value": "value",    "label": "Large / value pack"},
]

# next_field name -> (chip set, multi-select?)
FIELD_CHIP_MAP = {
    "category":      (CATEGORY_CHIPS, False),
    "skin_types":    (SKIN_TYPE_CHIPS, False),
    "price_tier":    (PRICE_TIER_CHIPS, False),
    "size_pref":     (SIZE_CHIPS, False),
    "concerns":      (CONCERN_CHIPS, True),
    "texture":       (TEXTURE_CHIPS, False),
    "allergen_free": (ALLERGEN_CHIPS, True),
}


# ---------------------------------------------------------------------------
# Graph availability cache
# Populated on first call; never expires (products don't change at runtime).
# Shape: { "skin_types": { "sunscreen": {"oily","dry",...}, ... },
#          "concerns":   { "sunscreen+oily": {"acne","dark_spots",...}, ... } }
# ---------------------------------------------------------------------------

_AVAIL: Optional[dict] = None


def _get_availability() -> dict:
    global _AVAIL
    if _AVAIL is not None:
        return _AVAIL
    try:
        from falkordb import FalkorDB
        db = FalkorDB(
            host=os.getenv("FALKORDB_HOST", "localhost"),
            port=int(os.getenv("FALKORDB_PORT", 6379)),
        )
        graph = db.select_graph(os.getenv("FALKORDB_GRAPH", "dotandkey"))

        # skin types per category
        r1 = graph.query(
            "MATCH (p:Product)-[:IN_CATEGORY]->(c:Category) "
            "MATCH (p)-[:SUITS_SKIN_TYPE]->(st:SkinType) "
            "WHERE p.active = true "
            "RETURN c.name AS cat, collect(DISTINCT st.name) AS skins"
        )
        skin_by_cat: dict[str, set] = {}
        for row in r1.result_set:
            skin_by_cat[row[0]] = set(row[1] or [])

        # concerns per (category, skin_type)
        r2 = graph.query(
            "MATCH (p:Product)-[:IN_CATEGORY]->(c:Category) "
            "MATCH (p)-[:SUITS_SKIN_TYPE]->(st:SkinType) "
            "MATCH (p)-[:TARGETS_CONCERN]->(cn:Concern) "
            "WHERE p.active = true "
            "RETURN c.name AS cat, st.name AS skin, collect(DISTINCT cn.name) AS concerns"
        )
        concern_by_pair: dict[str, set] = {}
        for row in r2.result_set:
            key = f"{row[0]}+{row[1]}"
            concern_by_pair[key] = set(row[2] or [])

        _AVAIL = {
            "skin_types": skin_by_cat,
            "concerns":   concern_by_pair,
        }
    except Exception:
        # Graph not reachable (tests, cold start) — return permissive empty dict
        _AVAIL = {"skin_types": {}, "concerns": {}}
    return _AVAIL


def chips_for_field(field: str, profile: Optional[dict] = None) -> dict:
    """Return {field, multi_select, options} for the given intake field.

    When profile is provided, options that have NO matching products in the
    graph (given the already-collected preferences) are dropped so the user
    is never shown a chip that leads to an empty result.
    """
    if not field or field not in FIELD_CHIP_MAP:
        return {"field": field or "", "multi_select": False, "options": []}

    options, multi = FIELD_CHIP_MAP[field]
    profile = profile or {}

    avail = _get_availability()

    if field == "skin_types":
        category = profile.get("category", "")
        if category and avail["skin_types"].get(category):
            valid = avail["skin_types"][category]
            # "all" skin type means product suits all types — always valid
            options = [o for o in options if o["value"] in valid or o["value"] == "all"]

    elif field == "concerns":
        category = profile.get("category", "")
        skin_types = profile.get("skin_types") or []
        if isinstance(skin_types, str):
            skin_types = [s for s in skin_types.split(",") if s]
        if category and skin_types:
            valid: set = set()
            for st in skin_types:
                key = f"{category}+{st}"
                valid |= avail["concerns"].get(key, set())
            if valid:
                options = [o for o in options if o["value"] in valid]

    return {"field": field, "multi_select": multi, "options": options}
