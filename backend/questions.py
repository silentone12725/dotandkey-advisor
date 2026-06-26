"""
backend/questions.py

Generates contextual question chips for product pages.
Questions are derived from the product's actual graph edges —
not generic hardcoded lists. Each question category is gated
on whether the product has relevant data for it.
"""

from graph.taxonomy import INGREDIENTS   # canonical ingredient names

# ---------------------------------------------------------------------------
# Ingredient humanizer  (snake_case → display name)
# ---------------------------------------------------------------------------

_INGREDIENT_DISPLAY = {
    "vitamin_c":   "Vitamin C",
    "niacinamide": "Niacinamide",
    "hyaluronic":  "Hyaluronic Acid",
    "ceramides":   "Ceramides",
    "salicylic":   "Salicylic Acid",
    "retinol":     "Retinol",
    "glycolic":    "Glycolic Acid",
    "cica":        "Cica",
    "watermelon":  "Watermelon Extract",
    "strawberry":  "Strawberry Extract",
    "blood_orange":"Blood Orange",
    "blueberry":   "Blueberry Extract",
    "pomegranate": "Pomegranate Extract",
    "mango":       "Mango Extract",
    "dragon_fruit":"Dragon Fruit",
    "lime":        "Lime Extract",
    "ricewater":   "Rice Water",
    "argan_oil":   "Argan Oil",
    "liquid_ice":  "Liquid Ice",
    "zinc_oxide":  "Zinc Oxide",
    "kojic_acid":  "Kojic Acid",
}


def _display(ingredient: str) -> str:
    return _INGREDIENT_DISPLAY.get(ingredient, ingredient.replace("_", " ").title())


# ---------------------------------------------------------------------------
# Category-specific FAQ bank  (sourced from Dot & Key product page FAQs)
# ---------------------------------------------------------------------------

CATEGORY_FAQ: dict[str, list[str]] = {
    "sunscreen": [
        "Does this leave a white cast?",
        "Will it sting my eyes?",
        "Can I use this under makeup?",
        "How often should I reapply?",
        "Is it water resistant?",
        "Do I still need moisturizer before this?",
        "What does PA++++ actually mean?",
        "Is it non-comedogenic?",
    ],
    "moisturizer": [
        "Can I use this on oily skin?",
        "Does this work as a night cream too?",
        "Will it pill under makeup?",
        "Can this replace a serum?",
        "How much should I apply?",
    ],
    "serum": [
        "Where does this go in my routine?",
        "How many drops should I use?",
        "Can I mix this with my moisturizer?",
        "Do I use this morning or night?",
    ],
    "face_wash": [
        "Can I use this twice a day?",
        "Is this sulphate-free?",
        "Will this remove sunscreen properly?",
        "Is it suitable for morning use?",
    ],
    "toner": [
        "Do I need this if I already use a serum?",
        "Should I use a cotton pad or just my hands?",
        "Does this replace moisturizer?",
    ],
    "mask": [
        "How long do I leave this on?",
        "How many times a week should I use it?",
        "Can I use this if I have active breakouts?",
    ],
    "lip_care": [
        "Can I use this as a base under lipstick?",
        "Is it safe to use overnight?",
        "Does this have SPF?",
    ],
    "eye_care": [
        "How close to the eye can I apply this?",
        "Can I use this under makeup?",
        "Is it safe for contact lens wearers?",
    ],
    "body_care": [
        "Can I use this on my face too?",
        "How long before it absorbs fully?",
        "Is it safe for daily use?",
    ],
}


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_product_questions(ctx: dict, current_season: str) -> list[str]:
    """Generate up to 8 contextual question chips for a product page.

    Args:
        ctx: product context dict from context.py — includes ingredients,
             allergen_free, skin_types, concerns, seasons, category, texture
        current_season: from session init (summer|monsoon|post_monsoon|winter)
    """
    questions = []
    ingredients = ctx.get("ingredients", [])
    allergen_free = set(ctx.get("allergen_free", []))
    skin_types = set(ctx.get("skin_types", []))
    seasons = set(ctx.get("seasons", []))
    category = ctx.get("category", "")

    # 1. Key active ingredients (always show, personalised to what's in product)
    key_actives = [i for i in ingredients
                   if i in ("vitamin_c", "niacinamide", "retinol", "salicylic",
                             "glycolic", "ceramides", "hyaluronic", "cica")]
    if key_actives:
        questions.append(f"What does {_display(key_actives[0])} do for my skin?")
    if len(key_actives) > 1:
        questions.append("What are all the key actives in this?")

    # 2. Allergen / sensitivity
    if "fragrance" in allergen_free:
        questions.append("Is this completely fragrance-free?")
    else:
        questions.append("Does this contain any fragrance?")
    if "alcohol" not in allergen_free:
        questions.append("Does this contain alcohol?")

    # 3. Skin type coverage (ask about types NOT explicitly listed as suited)
    all_types = {"oily", "dry", "combination", "sensitive", "normal"}
    not_suited = all_types - skin_types
    if "oily" in not_suited and category != "lip_care":
        questions.append("Can this work on oily skin too?")
    if "sensitive" in not_suited:
        questions.append("Is this okay for sensitive skin?")

    # 4. Season mismatch (ask about current season if product isn't tagged for it)
    if current_season and current_season not in seasons and seasons:
        season_labels = {
            "monsoon": "the monsoon",
            "winter": "winter",
            "summer": "summer",
            "post_monsoon": "this time of year",
        }
        label = season_labels.get(current_season, current_season)
        questions.append(f"Is this okay to use in {label}?")

    # 5. Compatibility (for products with actives that have known interactions)
    if "retinol" in ingredients or "glycolic" in ingredients or "salicylic" in ingredients:
        questions.append("Can I use this with Vitamin C?")
    if "vitamin_c" in ingredients:
        questions.append("Can I use this with retinol at night?")
    if "niacinamide" in ingredients and "vitamin_c" in ingredients:
        # common myth — worth addressing
        questions.append("Can Niacinamide and Vitamin C be used together?")

    # 6. Category-specific FAQ (pick first 2 not already covered)
    faq_pool = CATEGORY_FAQ.get(category, [])
    for q in faq_pool:
        if len(questions) >= 7:
            break
        if q not in questions:
            questions.append(q)

    # 7. "Something else" always last
    questions.append("Something else…")

    return questions[:8]