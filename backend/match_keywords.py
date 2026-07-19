"""
backend/match_keywords.py

Generates short, human-readable "why this matches" keyword tags for a
top-pick product card — e.g. ["Brightening", "Sulphate-free", "Lightweight"].

Deliberately NOT LLM-generated. The previous design had the LLM write a
full sentence of reasoning per top pick, which produced long, repetitive
chat bubbles (see live testing — a 4-sentence product breakdown for what
should be a glanceable card). Pulling tags directly from graph edges is:
  - faster (zero extra LLM tokens)
  - more trustworthy (can't hallucinate a quality the product doesn't have)
  - more scannable (3 keywords beat a sentence for a product card)

The LLM's only remaining job in the recommend playbook is one short
opening line — see prompts/recommend.md.
"""

# Concern -> short marketing-style label. Picked first (highest priority)
# since "why this addresses YOUR concern" is the most relevant signal.
CONCERN_LABELS = {
    "acne":               "Anti-acne",
    "dark_spots":         "Spot-fading",
    "dullness":           "Brightening",
    "dryness":            "Hydrating",
    "excess_oil":         "Oil-control",
    "pigmentation":       "Tone-evening",
    "ageing":             "Anti-aging",
    "damaged_skin_barrier": "Barrier-repair",
    "dehydration":         "Hydrating",
    "clogged_pores":       "Pore-clearing",
    "redness_irritation":  "Calming",
    "tanning":             "De-tan",
    "fine_lines":          "Anti-aging",
    "open_pores":          "Pore-refining",
}

# Texture -> label. Picked second.
TEXTURE_LABELS = {
    "lightweight": "Lightweight",
    "rich":        "Rich texture",
    "gel":         "Gel texture",
    "dewy":        "Dewy finish",
    "matte":       "Matte finish",
}

# Key active ingredient -> label. Picked third. Only "interesting" actives
# are surfaced (mirrors questions.py's key_actives list) — showing every
# ingredient edge would be noise, not a highlight.
INGREDIENT_LABELS = {
    "vitamin_c":   "Vitamin C",
    "niacinamide": "Niacinamide",
    "retinol":     "Retinol",
    "salicylic":   "Salicylic acid",
    "glycolic":    "Glycolic acid",
    "ceramides":   "Ceramides",
    "hyaluronic":  "Hyaluronic acid",
    "cica":        "Cica",
}

# Allergen-free claim -> label. Picked last — useful trust signal, but
# lowest priority versus concern/texture/ingredient relevance.
FREE_FROM_LABELS = {
    "fragrance": "Fragrance-free",
    "alcohol":   "Alcohol-free",
    "sulfate":   "Sulphate-free",
    "paraben":   "Paraben-free",
    "silicone":  "Silicone-free",
}

MAX_KEYWORDS = 3

# The 5 core skin types in the graph (excludes the "all" sentinel tag).
# A product matching every one of these (or carrying the explicit "all" tag)
# suits all skin types, regardless of how its title happens to be worded
# (e.g. "...for Oily Skin" titles that are graph-tagged for all 5 types too —
# the title is marketing copy, not the actual SUITS_SKIN_TYPE match set).
_CORE_SKIN_TYPES = {"oily", "dry", "combination", "normal", "sensitive"}


def build_keywords(
    matched_concerns: list[str],
    texture: str | None,
    key_ingredients: list[str],
    free_from: list[str],
    all_skin_types: list[str] | None = None,
) -> list[str]:
    """Return up to MAX_KEYWORDS human-readable tags, in priority order:
    all-skin-types > matched concern > texture > key ingredient > allergen-free claim.

    Pure function — no DB, no LLM, fully deterministic and fast to test.
    Deduplicates (e.g. dryness + dehydration both map to "Hydrating").
    """
    tags: list[str] = []
    seen: set[str] = set()

    def _add(label: str | None):
        if label and label not in seen:
            seen.add(label)
            tags.append(label)

    skin_types = set(all_skin_types or [])
    if "all" in skin_types or _CORE_SKIN_TYPES.issubset(skin_types):
        _add("All Skin Types")

    for concern in matched_concerns or []:
        _add(CONCERN_LABELS.get(concern))

    if texture:
        _add(TEXTURE_LABELS.get(texture))

    for ingredient in key_ingredients or []:
        _add(INGREDIENT_LABELS.get(ingredient))

    for allergen in free_from or []:
        _add(FREE_FROM_LABELS.get(allergen))

    return tags[:MAX_KEYWORDS]