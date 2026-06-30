"""
graph/ingredient_importance.py

Ingredient role classification for the CONTAINS_INGREDIENT edges.

Every ingredient in a product is classified as:
  primary    — hero active; named in product title or sole recognized active
  supporting — recognized active not featured in title
  incidental — base formulation ingredient (emollient, preservative, etc.)

Role multipliers used by capability_scorer.py:
  primary    → 1.5×  (ingredient is doing the heavy lifting)
  supporting → 1.0×  (full credit as per literature)
  incidental → 0.3×  (trace amount; unreliable efficacy signal)

Unknown ingredients (not in knowledge base):
  → "unknown" role, 0.0× capability multiplier, marks product for confidence penalty

role_reason examples:
  "Appears in product title"
  "Sole active ingredient in product"
  "Clinically marketed active"
  "Supporting antioxidant"
  "Barrier-supporting lipid"
  "Humectant base"
  "Preservative"
  "Base formulation ingredient"
"""

# ---------------------------------------------------------------------------
# Known actives registry
# ---------------------------------------------------------------------------

# Ingredients recognized as efficacious actives in dermatology literature.
# Role = "supporting" unless promoted to "primary" by title matching.
KNOWN_ACTIVES: frozenset[str] = frozenset({
    # Vitamin C family
    "vitamin_c", "ascorbic_acid", "ascorbyl_glucoside",
    "sodium_ascorbyl_phosphate", "blood_orange",
    # Niacinamide family
    "niacinamide",
    # Exfoliants
    "salicylic", "glycolic", "lactic_acid", "mandelic_acid",
    # Retinoids
    "retinol", "retinal", "bakuchiol",
    # Hydrators
    "hyaluronic", "sodium_hyaluronate",
    # Barrier
    "ceramides", "cholesterol", "fatty_acids", "argan_oil",
    # Soothing
    "cica", "centella_asiatica", "aloe_vera", "madecassoside",
    # Brightening
    "kojic_acid", "alpha_arbutin", "tranexamic_acid",
    "ricewater", "turmeric",
    # Antioxidants
    "vitamin_e", "ferulic_acid", "resveratrol", "coenzyme_q10",
    "blueberry", "pomegranate", "dragon_fruit", "strawberry",
    "watermelon", "mango", "lime",
    # Minerals / UV
    "zinc_oxide", "zinc_pca", "titanium_dioxide",
    # Cooling / specialty
    "liquid_ice", "caffeine", "peptide", "copper_peptide",
})

# Antioxidant sub-group (used for role_reason)
_ANTIOXIDANTS: frozenset[str] = frozenset({
    "vitamin_e", "ferulic_acid", "resveratrol", "coenzyme_q10",
    "blueberry", "pomegranate", "dragon_fruit", "strawberry",
})

# Barrier lipids sub-group
_BARRIER_LIPIDS: frozenset[str] = frozenset({
    "ceramides", "cholesterol", "fatty_acids", "argan_oil",
})

# Humectants
_HUMECTANTS: frozenset[str] = frozenset({
    "hyaluronic", "sodium_hyaluronate", "glycerin", "watermelon", "aloe_vera",
})

# Role multipliers
ROLE_MULTIPLIERS: dict[str, float] = {
    "primary":    1.5,
    "supporting": 1.0,
    "incidental": 0.3,
    "unknown":    0.0,
}


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_ingredient_role(
    ingredient: str,
    product_title: str,
    all_product_ingredients: list[str],
) -> tuple[str, str]:
    """Classify a single ingredient and return (role, role_reason).

    Args:
        ingredient:              Normalized ingredient name (snake_case).
        product_title:           Product title (used for primary detection).
        all_product_ingredients: All ingredients in this product (for sole-active check).

    Returns:
        (role, role_reason) where role ∈ {"primary", "supporting", "incidental", "unknown"}
    """
    ing_l = ingredient.lower()
    title_l = product_title.lower().replace("-", " ").replace("_", " ")

    # Canonical display form for matching against title
    display = ing_l.replace("_", " ")

    # ── Primary detection ───────────────────────────────────────────────────
    if display in title_l or ing_l in title_l:
        return "primary", "Appears in product title"

    # Check common aliases in title
    _TITLE_ALIASES: dict[str, list[str]] = {
        "vitamin_c":   ["vitamin c", "vit c", "ascorbic", "blood orange"],
        "niacinamide": ["niacinamide", "niacin"],
        "salicylic":   ["salicylic", "bha", "2% bha"],
        "hyaluronic":  ["hyaluronic", "ha", "hyaluron"],
        "ceramides":   ["ceramide"],
        "retinol":     ["retinol", "retinoid"],
        "glycolic":    ["glycolic", "aha"],
        "cica":        ["cica", "centella"],
        "kojic_acid":  ["kojic"],
        "zinc_oxide":  ["zinc", "mineral"],
        "ricewater":   ["rice water", "rice"],
        "argan_oil":   ["argan"],
        "liquid_ice":  ["liquid ice", "ice"],
    }
    for aliases in _TITLE_ALIASES.get(ing_l, []):
        if aliases in title_l:
            return "primary", "Appears in product title"

    # Sole active: only one recognized active in the product
    known_in_product = [i for i in all_product_ingredients if i in KNOWN_ACTIVES]
    if len(known_in_product) == 1 and ing_l in KNOWN_ACTIVES:
        return "primary", "Sole active ingredient in product"

    # ── Supporting detection ────────────────────────────────────────────────
    if ing_l in KNOWN_ACTIVES:
        if ing_l in _ANTIOXIDANTS:
            return "supporting", "Supporting antioxidant"
        if ing_l in _BARRIER_LIPIDS:
            return "supporting", "Barrier-supporting lipid"
        if ing_l in _HUMECTANTS:
            return "supporting", "Humectant base"
        return "supporting", "Clinically marketed active"

    # ── Incidental / Unknown ────────────────────────────────────────────────
    _PRESERVATIVES = {"phenoxyethanol", "ethylhexylglycerin", "benzyl_alcohol",
                      "methylparaben", "propylparaben"}
    _EMOLLIENTS = {"dimethicone", "cyclopentasiloxane", "isododecane",
                   "mineral_oil", "petrolatum", "beeswax"}
    _SOLVENTS = {"water", "aqua", "butylene_glycol", "propylene_glycol",
                 "alcohol_denat", "sd_alcohol"}

    if ing_l in _PRESERVATIVES:
        return "incidental", "Preservative"
    if ing_l in _EMOLLIENTS:
        return "incidental", "Emollient"
    if ing_l in _SOLVENTS:
        return "incidental", "Solvent / base"

    return "unknown", "Not in ingredient knowledge base"


def classify_all_ingredients(
    ingredients: list[str],
    product_title: str,
) -> dict[str, tuple[str, str]]:
    """Classify all ingredients in a product.

    Returns {ingredient_name: (role, role_reason)}
    """
    return {
        ing: classify_ingredient_role(ing, product_title, ingredients)
        for ing in ingredients
    }
