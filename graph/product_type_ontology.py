"""
graph/product_type_ontology.py

Product type hierarchy and routine-aware relationships for the knowledge graph.

Nodes: ProductType {name, description, parent}
Edges:
  (ProductType)-[:HAS_SUBTYPE]->
  (ProductType)-[:PREPARES_FOR]->
  (ProductType)-[:FOLLOWED_BY]->
  (ProductType)-[:PAIRS_WELL_WITH]->
  (ProductType)-[:NOT_RECOMMENDED_WITH]->
  (ProductType)-[:MORE_INTENSIVE_THAN]->
  (ProductType)-[:CAN_REPLACE]->
  (ProductType)-[:COMPLEMENTS]->

These relationships enable routine-building queries without hardcoded logic.
"""

# {parent_type: [subtypes]}
PRODUCT_TYPE_HIERARCHY: dict[str, list[str]] = {
    "sunscreen": [
        "tinted_sunscreen",
        "invisible_sunscreen",
        "gel_sunscreen",
        "stick_sunscreen",
        "body_sunscreen",
        "mineral_sunscreen",
        "chemical_sunscreen",
    ],
    "moisturizer": [
        "gel_moisturizer",
        "cream_moisturizer",
        "sleeping_mask",
        "barrier_cream",
        "oil_moisturizer",
    ],
    "face_wash": [
        "foam_cleanser",
        "gel_cleanser",
        "cream_cleanser",
        "micellar_water",
        "clay_cleanser",
        "exfoliating_cleanser",
    ],
    "serum": [
        "vitamin_c_serum",
        "niacinamide_serum",
        "hyaluronic_serum",
        "retinol_serum",
        "aha_bha_serum",
        "brightening_serum",
        "hydrating_serum",
        "acne_serum",
    ],
    "mask": [
        "sheet_mask",
        "clay_mask",
        "sleeping_mask",
        "exfoliating_mask",
        "brightening_mask",
    ],
    "lip_care": [
        "lip_balm",
        "lip_mask",
        "lip_oil",
        "lip_serum",
        "lip_scrub",
        "tinted_lip_balm",
    ],
    "eye_care": [
        "eye_cream",
        "eye_gel",
        "eye_serum",
    ],
    "toner": [
        "hydrating_toner",
        "exfoliating_toner",
        "brightening_toner",
        "clarifying_toner",
    ],
}

# All canonical type names (flat)
ALL_PRODUCT_TYPES: list[dict] = []
for parent, subs in PRODUCT_TYPE_HIERARCHY.items():
    ALL_PRODUCT_TYPES.append({"name": parent, "parent": None})
    for sub in subs:
        ALL_PRODUCT_TYPES.append({"name": sub, "parent": parent})


# (type_a, relationship, type_b)
PRODUCT_TYPE_RELATIONS: list[tuple[str, str, str]] = [
    # AM/PM routine ordering
    ("face_wash",           "FOLLOWED_BY",          "toner"),
    ("toner",               "FOLLOWED_BY",          "serum"),
    ("serum",               "FOLLOWED_BY",          "moisturizer"),
    ("moisturizer",         "FOLLOWED_BY",          "sunscreen"),
    ("face_wash",           "PREPARES_FOR",         "serum"),
    ("toner",               "PREPARES_FOR",         "serum"),

    # Complementary pairings
    ("vitamin_c_serum",     "PAIRS_WELL_WITH",      "sunscreen"),
    ("niacinamide_serum",   "PAIRS_WELL_WITH",      "gel_moisturizer"),
    ("aha_bha_serum",       "PAIRS_WELL_WITH",      "barrier_cream"),
    ("exfoliating_cleanser","PAIRS_WELL_WITH",      "moisturizer"),
    ("retinol_serum",       "PAIRS_WELL_WITH",      "hydrating_serum"),
    ("retinol_serum",       "PAIRS_WELL_WITH",      "barrier_cream"),

    # Lip care intensity
    ("lip_mask",            "MORE_INTENSIVE_THAN",  "lip_balm"),
    ("lip_mask",            "CAN_REPLACE",          "lip_balm"),
    ("lip_serum",           "MORE_INTENSIVE_THAN",  "lip_balm"),
    ("lip_oil",             "COMPLEMENTS",          "lip_balm"),
    ("lip_scrub",           "PREPARES_FOR",         "lip_mask"),

    # Mask intensity
    ("clay_mask",           "MORE_INTENSIVE_THAN",  "gel_cleanser"),
    ("exfoliating_mask",    "MORE_INTENSIVE_THAN",  "exfoliating_toner"),
    ("sleeping_mask",       "CAN_REPLACE",          "cream_moisturizer"),
    ("sleeping_mask",       "MORE_INTENSIVE_THAN",  "moisturizer"),

    # Sunscreen types
    ("tinted_sunscreen",    "COMPLEMENTS",          "moisturizer"),
    ("invisible_sunscreen", "COMPLEMENTS",          "moisturizer"),
    ("mineral_sunscreen",   "COMPLEMENTS",          "sensitive"),  # skin type as node
    ("gel_sunscreen",       "PAIRS_WELL_WITH",      "gel_moisturizer"),

    # Caution pairings
    ("retinol_serum",       "NOT_RECOMMENDED_WITH", "aha_bha_serum"),
    ("exfoliating_toner",   "NOT_RECOMMENDED_WITH", "vitamin_c_serum"),

    # Eye care
    ("eye_serum",           "MORE_INTENSIVE_THAN",  "eye_cream"),
    ("eye_cream",           "FOLLOWED_BY",          "sunscreen"),
]


# Category → default product types (used to auto-assign HAS_TYPE at ingest)
# Mapping is keyword-based on product title.
TITLE_TO_TYPE_HINTS: dict[str, list[tuple[str, str]]] = {
    # (keyword_in_title_lower, product_type_name)
    "sunscreen": [
        ("tinted",     "tinted_sunscreen"),
        ("invisible",  "invisible_sunscreen"),
        ("gel",        "gel_sunscreen"),
        ("stick",      "stick_sunscreen"),
        ("body",       "body_sunscreen"),
        ("mineral",    "mineral_sunscreen"),
    ],
    "moisturizer": [
        ("gel",        "gel_moisturizer"),
        ("cream",      "cream_moisturizer"),
        ("sleeping",   "sleeping_mask"),
        ("barrier",    "barrier_cream"),
    ],
    "face_wash": [
        ("foam",       "foam_cleanser"),
        ("gel",        "gel_cleanser"),
        ("clay",       "clay_cleanser"),
        ("scrub",      "exfoliating_cleanser"),
    ],
    "serum": [
        ("vitamin c",  "vitamin_c_serum"),
        ("niacinamide","niacinamide_serum"),
        ("hyaluronic", "hydrating_serum"),
        ("retinol",    "retinol_serum"),
        ("aha",        "aha_bha_serum"),
        ("bha",        "aha_bha_serum"),
        ("salicylic",  "acne_serum"),
        ("bright",     "brightening_serum"),
    ],
    "lip_care": [
        ("mask",       "lip_mask"),
        ("oil",        "lip_oil"),
        ("serum",      "lip_serum"),
        ("scrub",      "lip_scrub"),
        ("tinted",     "tinted_lip_balm"),
    ],
    "toner": [
        ("aha",        "exfoliating_toner"),
        ("bha",        "exfoliating_toner"),
        ("glow",       "brightening_toner"),
        ("bright",     "brightening_toner"),
        ("hydra",      "hydrating_toner"),
        ("clear",      "clarifying_toner"),
    ],
}
