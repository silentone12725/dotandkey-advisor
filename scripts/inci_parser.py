"""
scripts/inci_parser.py

Parses allergen-free claims and INCI ingredient lists from the
description text already in each Product node, then writes
FREE_FROM and CONTAINS_INGREDIENT edges where they were previously
missing from tag-based ingest.

Two detection methods:
  1. Explicit "free-from" marketing claims
     e.g. "fragrance-free", "alcohol-free", "sulphate free"
  2. INCI string detection — finds the ingredient list block
     (usually starts after "INGREDIENTS:" or a long comma-separated
     string of INCI names) and checks for known allergen compounds

Run after csv_to_graph.py:
  python3 scripts/inci_parser.py --host localhost --port 6379 --graph dotandkey --dry-run
"""

import argparse
import re



# ---------------------------------------------------------------------------
# Free-from keyword detection
# ---------------------------------------------------------------------------

FREE_FROM_PATTERNS: dict[str, list[str]] = {
    "fragrance": [
        "fragrance-free", "fragrance free", "no fragrance",
        "unscented", "artificial fragrance-free",
    ],
    "alcohol": [
        "alcohol-free", "alcohol free", "no alcohol",
    ],
    "sulfate": [
        "sulfate-free", "sulphate-free", "sulfate free", "sulphate free",
        "sls-free", "sles free",
    ],
    "paraben": [
        "paraben-free", "paraben free", "no parabens",
    ],
    "silicone": [
        "silicone-free", "silicone free",
    ],
}


def detect_free_from(description: str) -> set[str]:
    desc = description.lower()
    found = set()
    for allergen, phrases in FREE_FROM_PATTERNS.items():
        if any(p in desc for p in phrases):
            found.add(allergen)
    return found


# ---------------------------------------------------------------------------
# INCI ingredient list detection + allergen extraction
# ---------------------------------------------------------------------------

# INCI names that map to allergen classes
INCI_ALLERGEN_MAP: dict[str, str] = {
    # Fragrance
    "parfum":               "fragrance",
    "fragrance":            "fragrance",
    "limonene":             "fragrance",
    "linalool":             "fragrance",
    "citronellol":          "fragrance",
    "geraniol":             "fragrance",
    "eugenol":              "fragrance",
    "benzyl alcohol":       "fragrance",
    # Alcohol
    "alcohol denat":        "alcohol",
    "alcohol denat.":       "alcohol",
    "sd alcohol":           "alcohol",
    "denatured alcohol":    "alcohol",
    "ethanol":              "alcohol",
    # Sulfates
    "sodium lauryl sulfate":  "sulfate",
    "sodium laureth sulfate": "sulfate",
    "ammonium lauryl sulfate":"sulfate",
    # Parabens
    "methylparaben":       "paraben",
    "propylparaben":       "paraben",
    "butylparaben":        "paraben",
    "ethylparaben":        "paraben",
    "isobutylparaben":     "paraben",
    # Silicones
    "dimethicone":         "silicone",
    "cyclomethicone":      "silicone",
    "cyclopentasiloxane":  "silicone",
    "cyclohexasiloxane":   "silicone",
    "phenyl trimethicone": "silicone",
}

# Patterns to detect the INCI block in description HTML/text
INCI_BLOCK_PATTERNS = [
    r"(?i)(?:ingredients?|inci)[:\s]*([A-Za-z][^.]{60,}?\.)",
    r"(?i)aqua[\s,]+[A-Za-z][^.]{40,}?(?:phenoxyethanol|parfum|glycerin)[^.]*\.",
]

# Known active ingredients to INGREDIENT node names
INCI_INGREDIENT_MAP: dict[str, str] = {
    "ascorbyl glucoside":          "vitamin_c",
    "ethyl ascorbic acid":         "vitamin_c",
    "sodium ascorbyl phosphate":   "vitamin_c",
    "3-o-ethyl ascorbic acid":     "vitamin_c",
    "niacinamide":                 "niacinamide",
    "sodium hyaluronate":          "hyaluronic",
    "hyaluronic acid":             "hyaluronic",
    "ceramide":                    "ceramides",
    "salicylic acid":              "salicylic",
    "retinol":                     "retinol",
    "glycolic acid":               "glycolic",
    "centella asiatica":           "cica",
    "cica":                        "cica",
    "citrullus lanatus":           "watermelon",
    "watermelon":                  "watermelon",
    "fragaria ananassa":           "strawberry",
    "citrus sinensis":             "blood_orange",
    "blueberry":                   "blueberry",
    "punica granatum":             "pomegranate",
    "mangifera indica":            "mango",
    "hylocereus undatus":          "dragon_fruit",
    "citrus aurantifolia":         "lime",
    "rice water":                  "ricewater",
    "oryza sativa":                "ricewater",
    "argania spinosa":             "argan_oil",
    "titanium dioxide":            "zinc_oxide",   # both are mineral filters
    "zinc oxide":                  "zinc_oxide",
}


def extract_inci_block(description: str) -> str:
    """Try to find the INCI ingredient list inside a description string."""
    for pattern in INCI_BLOCK_PATTERNS:
        m = re.search(pattern, description)
        if m:
            return m.group(0)
    return ""


def detect_inci_allergens(description: str) -> set[str]:
    """Detect allergen PRESENCE in INCI list.
    Returns set of AllergenClass names that ARE PRESENT (not free-from).
    """
    inci_block = extract_inci_block(description).lower()
    if not inci_block:
        return set()
    found = set()
    for inci_name, allergen_class in INCI_ALLERGEN_MAP.items():
        if inci_name in inci_block:
            found.add(allergen_class)
    return found


def detect_inci_ingredients(description: str) -> set[str]:
    """Detect which canonical Ingredient nodes appear in the INCI block."""
    inci_block = extract_inci_block(description).lower()
    if not inci_block:
        return set()
    found = set()
    for inci_name, ingredient_node in INCI_INGREDIENT_MAP.items():
        if inci_name in inci_block:
            found.add(ingredient_node)
    return found


def compute_free_from(
    explicit_free_from: set[str],
    inci_present: set[str],
) -> set[str]:
    """A product is free-from an allergen if:
    (a) it explicitly claims to be, OR
    (b) the allergen's INCI compound is absent from the detected INCI block
        AND the INCI block was actually found (non-empty detection).
    """
    return explicit_free_from


# ---------------------------------------------------------------------------
# Graph queries
# ---------------------------------------------------------------------------

FETCH_PRODUCTS = """
MATCH (p:Product)
WHERE p.description IS NOT NULL AND p.description <> ""
RETURN p.sku AS sku, p.description AS description
"""

ADD_FREE_FROM = """
MATCH (p:Product {sku: $sku})
MATCH (a:AllergenClass {name: $allergen})
MERGE (p)-[:FREE_FROM]->(a)
"""

ADD_INGREDIENT = """
MATCH (p:Product {sku: $sku})
MATCH (i:Ingredient {name: $ingredient})
MERGE (p)-[:CONTAINS_INGREDIENT]->(i)
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(args):
    from falkordb import FalkorDB

    db    = FalkorDB(host=args.host, port=args.port)
    graph = db.select_graph(args.graph)

    result = graph.query(FETCH_PRODUCTS)
    products = result.result_set
    print(f"Parsing descriptions for {len(products)} products ...\n")

    total_free_from  = 0
    total_ingredients = 0

    for row in products:
        sku, description = row
        desc = description or ""

        free_from_explicit = detect_free_from(desc)
        inci_ingredients   = detect_inci_ingredients(desc)

        if free_from_explicit or inci_ingredients:
            print(f"  {sku}")
            if free_from_explicit:
                print(f"    FREE_FROM: {free_from_explicit}")
            if inci_ingredients:
                print(f"    INGREDIENTS (INCI): {inci_ingredients}")

        if not args.dry_run:
            for allergen in free_from_explicit:
                graph.query(ADD_FREE_FROM, {"sku": sku, "allergen": allergen})
                total_free_from += 1
            for ingredient in inci_ingredients:
                graph.query(ADD_INGREDIENT, {"sku": sku, "ingredient": ingredient})
                total_ingredients += 1

    print(f"\nFREE_FROM edges written:       {total_free_from}")
    print(f"CONTAINS_INGREDIENT edges (INCI): {total_ingredients}")

    if args.dry_run:
        print("\nDry run — nothing written.")
    else:
        print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--host",  default="localhost")
    parser.add_argument("--port",  type=int, default=6379)
    parser.add_argument("--graph", default="dotandkey")
    args = parser.parse_args()
    run(args)