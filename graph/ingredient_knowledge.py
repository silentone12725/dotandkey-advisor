"""
graph/ingredient_knowledge.py

Curated ingredient → concern and ingredient → capability mappings.
Ground-truth dermatology literature — not LLM-generated.

Edge schemas:
  INGREDIENT_CONCERN_EDGES: (ingredient, rel, concern, strength, confidence, explanation)
  INGREDIENT_CAPABILITY_EDGES: (ingredient, rel, capability_axis, strength)
  INGREDIENT_ALLERGEN_FLAGS: {ingredient: {flag: value}}
"""

# (ingredient_node_name, relationship, concern_name, strength 0-1, confidence 0-1, explanation)
INGREDIENT_CONCERN_EDGES: list[tuple] = [
    # Niacinamide
    ("niacinamide", "TREATS",   "excess_oil",           0.90, 0.95, "Regulates sebaceous gland activity, reduces sebum production"),
    ("niacinamide", "TREATS",   "clogged_pores",        0.85, 0.90, "Minimises pore appearance and reduces clogging"),
    ("niacinamide", "HELPS",    "pigmentation",         0.75, 0.85, "Inhibits melanosome transfer from melanocytes to keratinocytes"),
    ("niacinamide", "HELPS",    "acne",                 0.70, 0.80, "Anti-inflammatory, reduces post-acne redness"),
    ("niacinamide", "HELPS",    "dullness",             0.65, 0.75, "Improves skin texture and radiance"),
    ("niacinamide", "HELPS",    "damaged_skin_barrier", 0.60, 0.75, "Stimulates ceramide synthesis"),
    ("niacinamide", "HELPS",    "redness_irritation",   0.65, 0.80, "Anti-inflammatory, calms reactive skin"),

    # Vitamin C
    ("vitamin_c",   "BEST_FOR", "pigmentation",         0.95, 0.95, "Tyrosinase inhibitor, directly targets melanin synthesis"),
    ("vitamin_c",   "TREATS",   "dullness",             0.90, 0.90, "Antioxidant brightening, boosts radiance"),
    ("vitamin_c",   "TREATS",   "dark_spots",           0.88, 0.90, "Fades existing hyperpigmentation"),
    ("vitamin_c",   "TREATS",   "tanning",              0.80, 0.85, "Corrects UV-induced pigmentation"),
    ("vitamin_c",   "HELPS",    "ageing",               0.75, 0.85, "Antioxidant protection, collagen synthesis support"),
    ("vitamin_c",   "HELPS",    "fine_lines",           0.65, 0.75, "Collagen synthesis stimulation"),

    # Hyaluronic Acid
    ("hyaluronic",  "TREATS",   "dehydration",          0.95, 0.95, "Humectant holding up to 1000× its weight in water"),
    ("hyaluronic",  "TREATS",   "dryness",              0.85, 0.90, "Deep moisture delivery to skin layers"),
    ("hyaluronic",  "HELPS",    "fine_lines",           0.70, 0.80, "Plumps skin by retaining moisture"),
    ("hyaluronic",  "HELPS",    "damaged_skin_barrier", 0.60, 0.70, "Supports barrier hydration"),
    ("hyaluronic",  "HELPS",    "dry_lips",             0.90, 0.90, "High water-binding capacity for lip hydration"),

    # Ceramides
    ("ceramides",   "TREATS",   "damaged_skin_barrier", 0.95, 0.95, "Replenishes the skin's natural lipid bilayer"),
    ("ceramides",   "TREATS",   "dryness",              0.85, 0.90, "Locks in moisture by sealing barrier"),
    ("ceramides",   "TREATS",   "redness_irritation",   0.75, 0.85, "Reduces transepidermal water loss, calms reactive skin"),
    ("ceramides",   "HELPS",    "ageing",               0.65, 0.75, "Maintains structural integrity of skin"),
    ("ceramides",   "HELPS",    "acne",                 0.50, 0.65, "Barrier repair reduces irritant penetration"),

    # Salicylic Acid
    ("salicylic",   "TREATS",   "acne",                 0.95, 0.95, "BHA exfoliant, unclogs pores, anti-bacterial"),
    ("salicylic",   "TREATS",   "clogged_pores",        0.90, 0.92, "Oil-soluble, penetrates pores to dissolve sebum and debris"),
    ("salicylic",   "TREATS",   "excess_oil",           0.75, 0.80, "Controls surface oil"),
    ("salicylic",   "HELPS",    "open_pores",           0.70, 0.75, "Regular exfoliation reduces pore appearance"),
    ("salicylic",   "HELPS",    "dullness",             0.65, 0.70, "Chemical exfoliation improves texture"),

    # Retinol
    ("retinol",     "TREATS",   "ageing",               0.92, 0.95, "Increases cell turnover, stimulates collagen production"),
    ("retinol",     "TREATS",   "fine_lines",           0.90, 0.92, "Direct collagen stimulation and gap junction normalisation"),
    ("retinol",     "HELPS",    "pigmentation",         0.75, 0.80, "Accelerates cell turnover, fades spots"),
    ("retinol",     "HELPS",    "acne",                 0.65, 0.70, "Normalises follicular keratinisation"),
    ("retinol",     "HELPS",    "dullness",             0.70, 0.75, "Accelerates skin renewal"),

    # Glycolic Acid
    ("glycolic",    "TREATS",   "dullness",             0.85, 0.88, "AHA exfoliant removes dead skin cells"),
    ("glycolic",    "TREATS",   "dark_spots",           0.75, 0.80, "Accelerates turnover of pigmented cells"),
    ("glycolic",    "HELPS",    "ageing",               0.70, 0.75, "Stimulates collagen, improves texture"),
    ("glycolic",    "HELPS",    "pigmentation",         0.70, 0.75, "Exfoliates surface pigmentation"),
    ("glycolic",    "HELPS",    "clogged_pores",        0.65, 0.70, "Surface exfoliation"),

    # CICA (Centella Asiatica)
    ("cica",        "TREATS",   "redness_irritation",   0.90, 0.90, "Asiaticoside reduces inflammation and redness"),
    ("cica",        "TREATS",   "damaged_skin_barrier", 0.85, 0.88, "Madecassoside supports barrier recovery"),
    ("cica",        "HELPS",    "acne",                 0.65, 0.70, "Anti-inflammatory reduces acne redness"),
    ("cica",        "HELPS",    "ageing",               0.60, 0.65, "Collagen synthesis stimulation"),

    # Zinc Oxide
    ("zinc_oxide",  "TREATS",   "acne",                 0.75, 0.80, "Anti-bacterial, reduces inflammation"),
    ("zinc_oxide",  "TREATS",   "excess_oil",           0.70, 0.75, "Mattifying effect, absorbs sebum"),
    ("zinc_oxide",  "HELPS",    "redness_irritation",   0.65, 0.70, "Mineral soothing properties"),

    # Kojic Acid
    ("kojic_acid",  "TREATS",   "pigmentation",         0.80, 0.85, "Copper chelation inhibits tyrosinase"),
    ("kojic_acid",  "TREATS",   "dark_spots",           0.78, 0.82, "Targeted melanin inhibition"),
    ("kojic_acid",  "HELPS",    "tanning",              0.72, 0.75, "Corrects UV-induced pigmentation"),

    # Watermelon
    ("watermelon",  "TREATS",   "dehydration",          0.70, 0.75, "High water content, natural humectant"),
    ("watermelon",  "HELPS",    "redness_irritation",   0.55, 0.60, "Lycopene antioxidant, cooling"),

    # Mango
    ("mango",       "TREATS",   "dryness",              0.70, 0.72, "Mango butter is an emollient"),
    ("mango",       "HELPS",    "ageing",               0.55, 0.60, "Vitamin A and C content"),

    # Blood Orange / Lime (Vitamin C sources)
    ("blood_orange","TREATS",   "dullness",             0.70, 0.72, "Natural source of Vitamin C, brightening"),
    ("blood_orange","HELPS",    "pigmentation",         0.60, 0.65, "Antioxidant citrus extract"),
    ("lime",        "HELPS",    "dullness",             0.60, 0.65, "Citric acid brightening, mild AHA"),

    # Argan Oil
    ("argan_oil",   "TREATS",   "dryness",              0.85, 0.88, "Rich oleic and linoleic acids, deep nourishment"),
    ("argan_oil",   "TREATS",   "damaged_skin_barrier", 0.75, 0.80, "Fatty acids replenish lipid barrier"),
    ("argan_oil",   "HELPS",    "ageing",               0.65, 0.70, "Vitamin E antioxidant protection"),
    ("argan_oil",   "HELPS",    "dry_lips",             0.80, 0.82, "Emollient for lip barrier repair"),

    # Rice Water
    ("ricewater",   "TREATS",   "dullness",             0.72, 0.75, "Inositol, ferulic acid — brightening"),
    ("ricewater",   "HELPS",    "pigmentation",         0.60, 0.65, "Mild brightening, traditional use"),
    ("ricewater",   "HELPS",    "damaged_skin_barrier", 0.55, 0.60, "Emollient and barrier support"),

    # Dragon Fruit
    ("dragon_fruit","HELPS",    "dullness",             0.60, 0.63, "Betacyanin antioxidant"),
    ("dragon_fruit","HELPS",    "ageing",               0.55, 0.60, "Antioxidant protection"),

    # Strawberry, Blueberry, Pomegranate (antioxidant cluster)
    ("strawberry",  "HELPS",    "dullness",             0.60, 0.65, "Ellagic acid antioxidant, mild AHA"),
    ("blueberry",   "HELPS",    "ageing",               0.60, 0.65, "Anthocyanin antioxidant protection"),
    ("pomegranate", "HELPS",    "ageing",               0.65, 0.70, "Punicalagin antioxidant, collagen support"),

    # Liquid Ice (cooling complex)
    ("liquid_ice",  "TREATS",   "redness_irritation",   0.75, 0.78, "Menthyl lactate cooling, soothes overheated skin"),
    ("liquid_ice",  "HELPS",    "excess_oil",           0.55, 0.58, "Cooling reduces visible shine"),
]


# (ingredient_node_name, relationship, capability_axis, strength 0-1)
INGREDIENT_CAPABILITY_EDGES: list[tuple] = [
    # Niacinamide
    ("niacinamide", "PROVIDES", "oil_control",     0.90),
    ("niacinamide", "PROVIDES", "pore_care",       0.85),
    ("niacinamide", "SUPPORTS", "brightening",     0.65),
    ("niacinamide", "SUPPORTS", "barrier_repair",  0.60),
    ("niacinamide", "SUPPORTS", "sensitivity",     0.65),

    # Vitamin C
    ("vitamin_c",   "PROVIDES", "brightening",     0.95),
    ("vitamin_c",   "PROVIDES", "pigmentation",    0.95),

    # Hyaluronic Acid
    ("hyaluronic",  "PROVIDES", "hydration",       0.95),
    ("hyaluronic",  "SUPPORTS", "lip_repair",      0.80),
    ("hyaluronic",  "SUPPORTS", "sensitivity",     0.55),

    # Ceramides
    ("ceramides",   "PROVIDES", "barrier_repair",  0.95),
    ("ceramides",   "SUPPORTS", "hydration",       0.75),
    ("ceramides",   "SUPPORTS", "sensitivity",     0.80),

    # Salicylic Acid
    ("salicylic",   "PROVIDES", "acne",            0.95),
    ("salicylic",   "PROVIDES", "pore_care",       0.90),
    ("salicylic",   "SUPPORTS", "oil_control",     0.70),

    # Retinol
    ("retinol",     "PROVIDES", "pigmentation",    0.75),
    ("retinol",     "SUPPORTS", "brightening",     0.70),

    # Glycolic Acid
    ("glycolic",    "PROVIDES", "brightening",     0.80),
    ("glycolic",    "SUPPORTS", "pigmentation",    0.70),

    # CICA
    ("cica",        "PROVIDES", "sensitivity",     0.90),
    ("cica",        "SUPPORTS", "barrier_repair",  0.80),

    # Zinc Oxide
    ("zinc_oxide",  "PROVIDES", "sun_protection",  0.80),
    ("zinc_oxide",  "SUPPORTS", "acne",            0.70),
    ("zinc_oxide",  "SUPPORTS", "oil_control",     0.65),

    # Kojic Acid
    ("kojic_acid",  "PROVIDES", "pigmentation",    0.80),
    ("kojic_acid",  "SUPPORTS", "brightening",     0.72),

    # Watermelon
    ("watermelon",  "PROVIDES", "hydration",       0.70),
    ("watermelon",  "SUPPORTS", "sensitivity",     0.55),

    # Mango / Argan Oil
    ("mango",       "SUPPORTS", "hydration",       0.65),
    ("argan_oil",   "PROVIDES", "hydration",       0.80),
    ("argan_oil",   "SUPPORTS", "barrier_repair",  0.72),
    ("argan_oil",   "SUPPORTS", "lip_repair",      0.80),

    # Rice Water / Blood Orange / Dragon Fruit
    ("ricewater",   "SUPPORTS", "brightening",     0.68),
    ("blood_orange","SUPPORTS", "brightening",     0.65),
    ("dragon_fruit","SUPPORTS", "brightening",     0.58),
    ("strawberry",  "SUPPORTS", "brightening",     0.58),

    # Liquid Ice
    ("liquid_ice",  "PROVIDES", "sensitivity",     0.72),
]


# Allergen / irritation flags per ingredient INCI name
# Used by sensitivity_memory.py and explainability.py
INGREDIENT_ALLERGEN_FLAGS: dict[str, dict] = {
    "parfum":               {"contains_fragrance": True,  "irritation_level": "high"},
    "fragrance":            {"contains_fragrance": True,  "irritation_level": "high"},
    "limonene":             {"contains_fragrance": True,  "irritation_level": "medium"},
    "linalool":             {"contains_fragrance": True,  "irritation_level": "medium"},
    "citronellol":          {"contains_fragrance": True,  "irritation_level": "medium"},
    "geraniol":             {"contains_fragrance": True,  "irritation_level": "medium"},
    "eugenol":              {"contains_fragrance": True,  "irritation_level": "medium"},
    "benzyl_alcohol":       {"contains_fragrance": True,  "irritation_level": "low"},
    "alcohol_denat":        {"contains_alcohol":   True,  "irritation_level": "medium"},
    "sd_alcohol":           {"contains_alcohol":   True,  "irritation_level": "medium"},
    "dimethicone":          {"contains_silicone":  True,  "irritation_level": "very_low"},
    "methylparaben":        {"contains_paraben":   True,  "irritation_level": "low"},
    "propylparaben":        {"contains_paraben":   True,  "irritation_level": "low"},
    "sodium_lauryl_sulfate":{"contains_sulfate":   True,  "irritation_level": "high"},
    "sodium_laureth_sulfate":{"contains_sulfate":  True,  "irritation_level": "medium"},
    # Low-irritation actives
    "niacinamide":          {"irritation_level": "very_low", "suitable_sensitive": True},
    "ceramide":             {"irritation_level": "very_low", "suitable_sensitive": True},
    "hyaluronic_acid":      {"irritation_level": "very_low", "suitable_sensitive": True},
    "sodium_hyaluronate":   {"irritation_level": "very_low", "suitable_sensitive": True},
    "centella_asiatica":    {"irritation_level": "very_low", "suitable_sensitive": True},
    "zinc_oxide":           {"irritation_level": "very_low", "suitable_sensitive": True},
    "salicylic_acid":       {"irritation_level": "low",      "suitable_sensitive": False},
    "retinol":              {"irritation_level": "medium",   "suitable_sensitive": False},
    "glycolic_acid":        {"irritation_level": "medium",   "suitable_sensitive": False},
}
