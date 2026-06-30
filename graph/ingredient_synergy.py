"""
graph/ingredient_synergy.py

Curated ingredient synergy pairs for the Dot & Key knowledge graph.
Literature-grounded — represents well-established co-ingredient benefits.

Edge schema (both directions are stored):
  (Ingredient)-[:SYNERGIZES_WITH {
      evidence_strength,   # 0-1
      confidence,          # 0-1
      supported_concerns,  # list[str]
      explanation,         # str
      source,              # "literature" | "clinical"
  }]->(Ingredient)
"""

# (ingredient_a, ingredient_b, evidence_strength, confidence, supported_concerns, explanation, source)
SYNERGY_EDGES: list[tuple] = [
    (
        "niacinamide", "zinc_oxide",
        0.90, 0.90,
        ["excess_oil", "acne"],
        "Zinc enhances niacinamide's sebum regulation; combined anti-bacterial and anti-inflammatory effect",
        "literature",
    ),
    (
        "vitamin_c", "vitamin_e",
        0.95, 0.95,
        ["pigmentation", "dullness", "ageing"],
        "Vitamin E regenerates oxidised Vitamin C, extending antioxidant activity; mutual potentiation",
        "literature",
    ),
    (
        "vitamin_c", "ferulic_acid",
        0.95, 0.90,
        ["pigmentation", "dullness"],
        "Ferulic acid stabilises L-ascorbic acid from oxidation, doubling its photoprotection efficacy",
        "literature",
    ),
    (
        "ceramides", "hyaluronic",
        0.85, 0.88,
        ["dryness", "dehydration", "damaged_skin_barrier"],
        "Hyaluronic acid draws moisture in; ceramides seal it behind the restored lipid barrier",
        "literature",
    ),
    (
        "ceramides", "niacinamide",
        0.80, 0.82,
        ["damaged_skin_barrier", "excess_oil"],
        "Niacinamide stimulates ceramide synthesis; combined effect accelerates barrier restoration",
        "literature",
    ),
    (
        "niacinamide", "hyaluronic",
        0.80, 0.82,
        ["dehydration", "excess_oil"],
        "Hydration without comedogenicity — niacinamide controls oil while hyaluronic maintains moisture balance",
        "literature",
    ),
    (
        "salicylic", "niacinamide",
        0.82, 0.83,
        ["acne", "excess_oil", "clogged_pores"],
        "BHA clears pores; niacinamide reduces post-exfoliation inflammation and regulates oil rebound",
        "literature",
    ),
    (
        "vitamin_c", "niacinamide",
        0.78, 0.80,
        ["pigmentation", "dullness"],
        "At low concentrations these do not antagonise; together they target pigmentation via different pathways",
        "literature",
    ),
    (
        "retinol", "hyaluronic",
        0.85, 0.87,
        ["ageing", "fine_lines"],
        "Hyaluronic counteracts retinol-induced dryness and irritation, improving tolerability",
        "literature",
    ),
    (
        "retinol", "niacinamide",
        0.83, 0.85,
        ["ageing", "pigmentation"],
        "Niacinamide buffers retinol irritation while amplifying pigmentation-correction synergy",
        "literature",
    ),
    (
        "cica", "ceramides",
        0.85, 0.87,
        ["damaged_skin_barrier", "redness_irritation"],
        "Centella's asiaticoside calms while ceramides restore; ideal post-treatment barrier recovery",
        "literature",
    ),
    (
        "cica", "niacinamide",
        0.78, 0.80,
        ["redness_irritation", "acne"],
        "Dual anti-inflammatory pathways: cica reduces cytokine response, niacinamide reduces oil and redness",
        "literature",
    ),
    (
        "glycolic", "vitamin_c",
        0.75, 0.78,
        ["dullness", "pigmentation"],
        "Glycolic acid lowers skin pH which stabilises Vitamin C and improves its penetration depth",
        "literature",
    ),
    (
        "hyaluronic", "glycolic",
        0.75, 0.76,
        ["dryness", "dullness"],
        "Glycolic exfoliates; hyaluronic acid replenishes the hydration lost during exfoliation",
        "literature",
    ),
    (
        "argan_oil", "ceramides",
        0.78, 0.80,
        ["dryness", "damaged_skin_barrier"],
        "Fatty acids in argan oil complement ceramide lipid composition for barrier repair",
        "literature",
    ),
    (
        "kojic_acid", "vitamin_c",
        0.80, 0.82,
        ["pigmentation", "dark_spots"],
        "Two complementary tyrosinase inhibition pathways — copper chelation + ascorbate reduction",
        "literature",
    ),
    (
        "salicylic", "cica",
        0.75, 0.78,
        ["acne", "redness_irritation"],
        "Salicylic clears pores; cica reduces post-treatment redness and supports healing",
        "literature",
    ),
    (
        "zinc_oxide", "cica",
        0.72, 0.75,
        ["acne", "redness_irritation"],
        "Mineral zinc soothes while cica accelerates skin recovery; both are low-irritation",
        "literature",
    ),
]
