"""
scripts/season_infer.py

Applies the India 4-season knowledge matrix to all Product nodes in the graph.
For each product, scores it against (season, skin_type) pairs using its existing
CONTAINS_INGREDIENT, HAS_TEXTURE, and IN_CATEGORY edges.
Writes BEST_IN_SEASON edges where score >= threshold.

Run after csv_to_graph.py:
  python3 scripts/season_infer.py --host localhost --port 6379 --graph dotandkey

--dry-run prints scores without writing to graph.
"""

import argparse
import os


# ---------------------------------------------------------------------------
# Knowledge matrix
# ---------------------------------------------------------------------------
# Structure: season -> {recommended_ingredients, recommended_textures,
#                       recommended_categories, avoid_ingredients,
#                       avoid_textures}
# Score +1 for each recommended signal, -2 for each avoid signal.
# Edge is written if final score >= 1 and no avoid signals fired.

MATRIX = {
    "summer": {
        "rec_ingredients": {
            "vitamin_c", "watermelon", "niacinamide", "zinc_oxide",
            "hyaluronic", "blood_orange",
        },
        "rec_textures":    {"lightweight", "gel", "dewy"},
        "rec_categories":  {"sunscreen"},
        "avoid_ingredients": set(),
        "avoid_textures":  {"rich"},
    },
    "monsoon": {
        "rec_ingredients": {
            "salicylic", "niacinamide", "cica", "liquid_ice",
        },
        "rec_textures":    {"lightweight", "gel", "matte"},
        "rec_categories":  {"sunscreen", "face_wash", "toner"},
        "avoid_ingredients": {"argan_oil"},
        "avoid_textures":  {"rich"},
    },
    "post_monsoon": {
        "rec_ingredients": {
            "vitamin_c", "ceramides", "niacinamide", "glycolic",
            "blood_orange", "strawberry",
        },
        "rec_textures":    {"lightweight", "gel"},
        "rec_categories":  {"serum", "moisturizer", "face_wash"},
        "avoid_ingredients": set(),
        "avoid_textures":  set(),
    },
    "winter": {
        "rec_ingredients": {
            "ceramides", "hyaluronic", "argan_oil", "shea",
            "niacinamide", "retinol",
        },
        "rec_textures":    {"rich"},
        "rec_categories":  {"moisturizer", "lip_care", "eye_care"},
        "avoid_ingredients": {"salicylic", "glycolic"},   # can over-dry in winter
        "avoid_textures":  set(),
    },
}

SCORE_THRESHOLD = 1   # minimum score to write edge


def score_product(ingredients, textures, categories, season_key) -> int:
    rules = MATRIX[season_key]
    score = 0

    for ing in ingredients:
        if ing in rules["rec_ingredients"]:
            score += 1
        if ing in rules["avoid_ingredients"]:
            score -= 2

    for tx in textures:
        if tx in rules["rec_textures"]:
            score += 1
        if tx in rules["avoid_textures"]:
            score -= 2

    for cat in categories:
        if cat in rules["rec_categories"]:
            score += 1

    return score


# ---------------------------------------------------------------------------
# Graph queries
# ---------------------------------------------------------------------------

FETCH_ALL = """
MATCH (p:Product)
OPTIONAL MATCH (p)-[:CONTAINS_INGREDIENT]->(ing:Ingredient)
OPTIONAL MATCH (p)-[:HAS_TEXTURE]->(tx:Texture)
OPTIONAL MATCH (p)-[:IN_CATEGORY]->(cat:Category)
RETURN p.sku AS sku, p.title AS title,
       collect(DISTINCT ing.name) AS ingredients,
       collect(DISTINCT tx.name) AS textures,
       collect(DISTINCT cat.name) AS categories
"""

WRITE_SEASON_EDGE = """
MATCH (p:Product {sku: $sku})
MATCH (s:Season {name: $season})
MERGE (p)-[:BEST_IN_SEASON]->(s)
"""

DELETE_INFERRED_SEASONS = """
MATCH (p:Product {sku: $sku})-[r:BEST_IN_SEASON]->()
DELETE r
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(args):
    from falkordb import FalkorDB

    db    = FalkorDB(host=args.host, port=args.port)
    graph = db.select_graph(args.graph)

    result = graph.query(FETCH_ALL)
    products = result.result_set
    print(f"Scoring {len(products)} products across 4 seasons ...\n")

    edges_to_write = []

    for row in products:
        sku, title, ingredients, textures, categories = row
        row_edges = []
        for season in MATRIX:
            s = score_product(ingredients, textures, categories, season)
            if s >= SCORE_THRESHOLD:
                row_edges.append((season, s))

        if row_edges:
            print(f"  {sku:16s} {title[:40]:40s}")
            for season, s in sorted(row_edges, key=lambda x: -x[1]):
                print(f"    → {season:15s} score={s}")
            edges_to_write.append((sku, [s for s, _ in row_edges]))

    print(f"\nTotal products receiving season edges: {len(edges_to_write)}")
    total_edges = sum(len(seasons) for _, seasons in edges_to_write)
    print(f"Total BEST_IN_SEASON edges to write:   {total_edges}")

    if args.dry_run:
        print("\nDry run — nothing written.")
        return

    print("\nWriting edges ...")
    for sku, seasons in edges_to_write:
        # clear previously inferred season edges first
        graph.query(DELETE_INFERRED_SEASONS, {"sku": sku})
        for season in seasons:
            graph.query(WRITE_SEASON_EDGE, {"sku": sku, "season": season})

    print("Done.")

    # verify
    res = graph.query(
        "MATCH (p:Product)-[:BEST_IN_SEASON]->(s:Season) "
        "RETURN s.name AS season, count(p) AS n ORDER BY n DESC"
    )
    print("\nSeason edge counts:")
    for r in res.result_set:
        print(f"  {r[0]:15s} {r[1]} products")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--host",  default="localhost")
    parser.add_argument("--port",  type=int, default=6379)
    parser.add_argument("--graph", default="dotandkey")
    args = parser.parse_args()
    run(args)