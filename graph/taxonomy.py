"""
Canonical vocabulary for the Dot & Key product graph, plus the mapping
from raw, messy CSV tags -> (relationship_type, canonical_node_name).

The CSV tag column mixes at least 4 generations of tagging conventions
(fltr_skin_type_*, st_skin_type_*, skintype_*, plain "Oily Skin") that
all mean the same thing. This module collapses them into one vocabulary
so the graph has ONE node per concept, not five duplicates with
different casings.

Each entry in TAG_MAP maps a raw tag string -> a list of
(edge_type, node_label, canonical_name) tuples. A single raw tag can
expand to multiple edges (e.g. "Mature Skin" -> concern: ageing).
"""

# ---------------------------------------------------------------------------
# Canonical vocabularies (the only allowed values for each node label)
# ---------------------------------------------------------------------------

SKIN_TYPES = ["oily", "dry", "combination", "normal", "sensitive", "all"]

CONCERNS = [
    "acne",
    "dark_spots",
    "dullness",
    "dryness",
    "excess_oil",
    "pigmentation",
    "ageing",
    "damaged_skin_barrier",
    "dehydration",
    "clogged_pores",
    "dry_lips",
    "fine_lines",
    "open_pores",
    "tanning",
    "redness_irritation",
]

SEASONS = ["summer", "monsoon", "post_monsoon", "winter"]

INGREDIENTS = [
    "vitamin_c", "niacinamide", "hyaluronic", "ceramides", "salicylic",
    "retinol", "glycolic", "cica", "watermelon", "strawberry",
    "blood_orange", "blueberry", "pomegranate", "mango", "dragon_fruit",
    "lime", "ricewater", "argan_oil", "liquid_ice", "zinc_oxide",
    "kojic_acid",
]

ALLERGEN_CLASSES = [
    "fragrance", "alcohol", "sulfate", "paraben", "silicone", "essential_oil",
]

CATEGORIES = [
    "sunscreen", "moisturizer", "face_wash", "serum", "toner", "mask",
    "lip_care", "eye_care", "body_care", "hair_care", "combo",
]

TEXTURES = ["dewy", "matte", "gel", "lightweight", "rich"]


# ---------------------------------------------------------------------------
# Tag -> edge mapping
# Edge types: SKIN_TYPE, CONCERN, SEASON, INGREDIENT, ALLERGEN_FREE, TEXTURE
# ---------------------------------------------------------------------------

TAG_MAP = {}


def _add(tag, *targets):
    """targets: tuples of (edge_type, canonical_name)"""
    TAG_MAP[tag] = list(targets)


# --- skin type: fltr_skin_type_* -------------------------------------------
_add("fltr_skin_type_Oily", ("SKIN_TYPE", "oily"))
_add("fltr_skin_type_Dry", ("SKIN_TYPE", "dry"))
_add("fltr_skin_type_Combination", ("SKIN_TYPE", "combination"))
_add("fltr_skin_type_Normal", ("SKIN_TYPE", "normal"))
_add("fltr_skin_type_Sensitive", ("SKIN_TYPE", "sensitive"))
_add("fltr_skin_type_All Skin Types", ("SKIN_TYPE", "all"))
_add("fltr_skin_type_Normal to Dry Skin", ("SKIN_TYPE", "normal"), ("SKIN_TYPE", "dry"))
_add("fltr_skin_type_Normal to Very Dry Skin", ("SKIN_TYPE", "normal"), ("SKIN_TYPE", "dry"))
_add("fltr_skin_type_Oily & Combination Skin", ("SKIN_TYPE", "oily"), ("SKIN_TYPE", "combination"))
# mis-tagged benefit claims that landed under the skin_type prefix
_add("fltr_skin_type_Boosts Collagen", ("CONCERN", "ageing"))
_add("fltr_skin_type_Firms Skin", ("CONCERN", "ageing"))
_add("fltr_skin_type_Moisturizes", ("CONCERN", "dryness"))

# --- skin type: st_skin_type_* (already lowercase, 1:1) ---------------------
for _name in ["oily", "dry", "combination", "normal", "sensitive"]:
    _add(f"st_skin_type_{_name}", ("SKIN_TYPE", _name))

# --- skin type: skintype_* ---------------------------------------------------
_add("skintype_Oily", ("SKIN_TYPE", "oily"))
_add("skintype_Dry", ("SKIN_TYPE", "dry"))
_add("skintype_Combination", ("SKIN_TYPE", "combination"))
_add("skintype_Sensitive", ("SKIN_TYPE", "sensitive"))
_add("skintype_all", ("SKIN_TYPE", "all"))

# --- skin type: plain tags ----------------------------------------------------
_add("Oily Skin", ("SKIN_TYPE", "oily"))
_add("Dry Skin", ("SKIN_TYPE", "dry"))
_add("Combination Skin", ("SKIN_TYPE", "combination"))
_add("Sensitive Skin", ("SKIN_TYPE", "sensitive"))
_add("Normal Skin", ("SKIN_TYPE", "normal"))
_add("Oily", ("SKIN_TYPE", "oily"))
_add("Dry", ("SKIN_TYPE", "dry"))
_add("Oily & Combination Skin", ("SKIN_TYPE", "oily"), ("SKIN_TYPE", "combination"))
_add("Combination & Normal Skin", ("SKIN_TYPE", "combination"), ("SKIN_TYPE", "normal"))
_add("Sensitive & Combination Skin", ("SKIN_TYPE", "sensitive"), ("SKIN_TYPE", "combination"))
_add("Acne-Prone & Sensitive Skin", ("SKIN_TYPE", "sensitive"), ("CONCERN", "acne"))
_add("Mature Skin", ("CONCERN", "ageing"))

# --- concerns: fltr_skin_concern_* ------------------------------------------
_add("fltr_skin_concern_Acne", ("CONCERN", "acne"))
_add("fltr_skin_concern_Ageing", ("CONCERN", "ageing"))
_add("fltr_skin_concern_Clogged Pores", ("CONCERN", "clogged_pores"))
_add("fltr_skin_concern_Damaged Skin Barrier", ("CONCERN", "damaged_skin_barrier"))
_add("fltr_skin_concern_Dark Spots", ("CONCERN", "dark_spots"))
_add("fltr_skin_concern_Dehydrated Skin", ("CONCERN", "dehydration"))
_add("fltr_skin_concern_Dryness", ("CONCERN", "dryness"))
_add("fltr_skin_concern_Dullness", ("CONCERN", "dullness"))
_add("fltr_skin_concern_Excess Oil", ("CONCERN", "excess_oil"))
_add("fltr_skin_concern_Overheated Skin", ("CONCERN", "excess_oil"))
_add("fltr_skin_concern_Pigmentation/Uneven Skin Tone", ("CONCERN", "pigmentation"))
_add("fltr_skin_concern_Pore Care/ Blackheads", ("CONCERN", "clogged_pores"))
_add("fltr_skin_concern_Redness & Irritation", ("CONCERN", "redness_irritation"))
_add("fltr_skin_concern_Sun-Stressed Skin", ("CONCERN", "tanning"))
_add("fltr_skin_concern_Tanning", ("CONCERN", "tanning"))
# "Others" is too vague to map -> intentionally skipped

# --- concerns: st_skinconcern_* (already lowercase, 1:1) --------------------
for _name in ["acne", "ageing", "damaged_skin_barrier", "dark_spots",
              "dryness", "dullness", "excess_oil", "pigmentation"]:
    _add(f"st_skinconcern_{_name}", ("CONCERN", _name))

# --- concerns: skinconcern_* --------------------------------------------------
_add("skinconcern_Acne", ("CONCERN", "acne"))
_add("skinconcern_Clogged Pores", ("CONCERN", "clogged_pores"))
_add("skinconcern_Dark Spots", ("CONCERN", "dark_spots"))
_add("skinconcern_Dehydrated", ("CONCERN", "dehydration"))
_add("skinconcern_Dry Lips", ("CONCERN", "dry_lips"))
_add("skinconcern_Dryness", ("CONCERN", "dryness"))
_add("skinconcern_Dullness", ("CONCERN", "dullness"))
_add("skinconcern_Fine Lines and Wrinkles", ("CONCERN", "fine_lines"))
_add("skinconcern_Open Pores", ("CONCERN", "open_pores"))
_add("skinconcern_Pigmentation", ("CONCERN", "pigmentation"))
_add("skinconcern_Pore Care", ("CONCERN", "clogged_pores"))
_add("skinconcern_Sun Tan", ("CONCERN", "tanning"))
_add("skinconcern_Uneven Skin Tone", ("CONCERN", "pigmentation"))

# --- concerns: plain tags ------------------------------------------------------
_add("acne", ("CONCERN", "acne"))
_add("activeAcne", ("CONCERN", "acne"))
_add("proneAcne", ("CONCERN", "acne"))
_add("postAcne", ("CONCERN", "acne"))
_add("blemish", ("CONCERN", "acne"))
_add("Pimple", ("CONCERN", "acne"))
_add("pimple", ("CONCERN", "acne"))
_add("Acne Control", ("CONCERN", "acne"))
_add("Acne-Prone Skin", ("CONCERN", "acne"))
_add("Dark Spots", ("CONCERN", "dark_spots"))
_add("Dullness", ("CONCERN", "dullness"))
_add("Pigmentation", ("CONCERN", "pigmentation"))
_add("Uneven Tone", ("CONCERN", "pigmentation"))
_add("Dryness And Repair", ("CONCERN", "dryness"))
_add("Age Defense", ("CONCERN", "ageing"))

# --- seasons -----------------------------------------------------------------
_add("Summer Picks", ("SEASON", "summer"))
_add("winter-pick", ("SEASON", "winter"))
_add("winter must haves", ("SEASON", "winter"))

# --- ingredients: fltr_ingredients_* (key actives, often combos) ------------
_add("fltr_ingredients_Argan Oil", ("INGREDIENT", "argan_oil"))
_add("fltr_ingredients_CICA", ("INGREDIENT", "cica"))
_add("fltr_ingredients_Ceramides", ("INGREDIENT", "ceramides"))
_add("fltr_ingredients_Cica + Niacinamide", ("INGREDIENT", "cica"), ("INGREDIENT", "niacinamide"))
_add("fltr_ingredients_Glycolic", ("INGREDIENT", "glycolic"))
_add("fltr_ingredients_Hyaluronic", ("INGREDIENT", "hyaluronic"))
_add("fltr_ingredients_Hyaluronic + Ceramides", ("INGREDIENT", "hyaluronic"), ("INGREDIENT", "ceramides"))
_add("fltr_ingredients_Liquid Ice", ("INGREDIENT", "liquid_ice"))
_add("fltr_ingredients_Mango + Glycolic", ("INGREDIENT", "mango"), ("INGREDIENT", "glycolic"))
_add("fltr_ingredients_Niacinamide", ("INGREDIENT", "niacinamide"))
_add("fltr_ingredients_Niacinamide + Cica", ("INGREDIENT", "niacinamide"), ("INGREDIENT", "cica"))
_add("fltr_ingredients_Retinol", ("INGREDIENT", "retinol"))
_add("fltr_ingredients_Ricewater", ("INGREDIENT", "ricewater"))
_add("fltr_ingredients_Salicylic", ("INGREDIENT", "salicylic"))
_add("fltr_ingredients_Strawberry", ("INGREDIENT", "strawberry"))
_add("fltr_ingredients_Vitamin C", ("INGREDIENT", "vitamin_c"))
_add("fltr_ingredients_Watermelon", ("INGREDIENT", "watermelon"))
_add("fltr_ingredients_Watermelon + AHA", ("INGREDIENT", "watermelon"), ("INGREDIENT", "glycolic"))
# "Others" -> too vague, skipped

# --- ingredients: st_ingredients_* (already lowercase, 1:1) -----------------
for _name in ["blood_orange", "blueberry", "ceramides", "cica", "dragon_fruit",
              "glycolic", "hyaluronic", "lime", "mango", "niacinamide",
              "pomegranate", "retinol", "salicylic", "strawberry",
              "vitamin_c", "watermelon"]:
    _add(f"st_ingredients_{_name}", ("INGREDIENT", _name))

# --- ingredients: plain tags ---------------------------------------------------
_add("Vitamin C", ("INGREDIENT", "vitamin_c"))
_add("CICA", ("INGREDIENT", "cica"))
_add("Cica+Niacinamide", ("INGREDIENT", "cica"), ("INGREDIENT", "niacinamide"))
_add("Cica+niacinamide", ("INGREDIENT", "cica"), ("INGREDIENT", "niacinamide"))
_add("Dragon Fruit", ("INGREDIENT", "dragon_fruit"))
_add("5% ceramides serum", ("INGREDIENT", "ceramides"))

# --- texture ----------------------------------------------------------------
_add("Dewy", ("TEXTURE", "dewy"))
_add("Sunscreen_dewy", ("TEXTURE", "dewy"))
_add("sunscreen_matte", ("TEXTURE", "matte"))
_add("sunscreen_oilfree", ("TEXTURE", "lightweight"))

# --- skin type: per-category "<category>_<skintype>" tags -------------------
# (e.g. "sunscreen_oily", "moisturizer_dry", "serum_sensitive")
for _cat in ["sunscreen", "Sunscreen", "moisturizer", "serum"]:
    for _name in ["oily", "dry", "combination", "normal", "sensitive"]:
        _add(f"{_cat}_{_name}", ("SKIN_TYPE", _name))
_add("sunscreen_notsure", ("SKIN_TYPE", "all"))
_add("moisturizer_notsure", ("SKIN_TYPE", "all"))

# --- concerns: "orbo_*" diagnostic tags + remaining plain/typo tags ----------
_add("orbo_dark_spots", ("CONCERN", "dark_spots"))
_add("orbo_pigmentation", ("CONCERN", "pigmentation"))
_add("orbo_uneven_skin", ("CONCERN", "pigmentation"))
_add("uneven_skin", ("CONCERN", "pigmentation"))
_add("orbo_acne", ("CONCERN", "acne"))
_add("orbo_skin_dullness", ("CONCERN", "dullness"))
_add("skin_dullness", ("CONCERN", "dullness"))
_add("serum_dullness", ("CONCERN", "dullness"))
_add("orbo_redness", ("CONCERN", "redness_irritation"))
_add("redness", ("CONCERN", "redness_irritation"))
_add("orbo_face_wrinkles", ("CONCERN", "fine_lines"))
_add("face_wrinkles", ("CONCERN", "fine_lines"))
_add("serum_wrinkle", ("CONCERN", "fine_lines"))
_add("orbo_firmness", ("CONCERN", "ageing"))
_add("firmness", ("CONCERN", "ageing"))
_add("serum_age", ("CONCERN", "ageing"))
_add("moisturizer_dryness", ("CONCERN", "dryness"))
_add("moisturizer_acne", ("CONCERN", "acne"))
_add("dark_spots", ("CONCERN", "dark_spots"))
_add("detan", ("CONCERN", "tanning"))
_add("de tan", ("CONCERN", "tanning"))
_add("sunscreen_tanx", ("CONCERN", "tanning"))
_add("sunscreen_spots", ("CONCERN", "dark_spots"))
_add("Acne Prone Skin", ("CONCERN", "acne"))
_add("dryAcne", ("SKIN_TYPE", "dry"), ("CONCERN", "acne"))

# --- ingredients: typos + remaining plain tags -------------------------------
_add("hyluronic", ("INGREDIENT", "hyaluronic"))  # common typo in source data
_add("watermelon", ("INGREDIENT", "watermelon"))
_add("retinol", ("INGREDIENT", "retinol"))
_add("cica+niacinamide", ("INGREDIENT", "cica"), ("INGREDIENT", "niacinamide"))
_add("kojic", ("INGREDIENT", "kojic_acid"))


# ---------------------------------------------------------------------------
# Category: from the CSV "Type" column (not the tags column)
# ---------------------------------------------------------------------------

TYPE_TO_CATEGORY = {
    "Sunscreen": "sunscreen",
    "Moisturiser": "moisturizer",
    "Face Wash": "face_wash",
    "Serum": "serum",
    "Toner": "toner",
    "Mask": "mask",
    "Lip Balm": "lip_care",
    "Eye Care": "eye_care",
    "bodycare": "body_care",
    "haircare": "hair_care",
    "Combo": "combo",
    "combo": "combo",
    # "Free" rows are sample-size duplicates of another SKU's full-size
    # product -> not given their own category, handled separately in ingest
}


# ---------------------------------------------------------------------------
# Allergen-free signals: keyword scan over the free-text description
# (the CSV has no structured INCI ingredient list, so this is a best-effort
# proxy built from explicit marketing claims like "fragrance-free")
# ---------------------------------------------------------------------------

ALLERGEN_FREE_KEYWORDS = {
    "fragrance": ["fragrance-free", "fragrance free", "no fragrance", "unscented"],
    "alcohol": ["alcohol-free", "alcohol free"],
    "sulfate": ["sulfate-free", "sulphate-free", "sulfate free", "sulphate free"],
    "paraben": ["paraben-free", "paraben free"],
    "silicone": ["silicone-free", "silicone free"],
    "essential_oil": ["essential oil free", "no essential oils"],
}


def find_allergen_free_claims(description: str):
    """Return list of AllergenClass names this product explicitly claims
    to be free of, based on its description text."""
    desc = description.lower()
    found = []
    for allergen, phrases in ALLERGEN_FREE_KEYWORDS.items():
        if any(p in desc for p in phrases):
            found.append(allergen)
    return found
