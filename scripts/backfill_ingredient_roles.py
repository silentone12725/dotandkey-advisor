"""
scripts/backfill_ingredient_roles.py

Sets role and role_reason properties on all existing CONTAINS_INGREDIENT
edges in the graph, using the ingredient_importance classifier.

Run once after ingredient_importance.py is available:
    python3 scripts/backfill_ingredient_roles.py [--dry-run]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from graph.ingredient_importance import classify_all_ingredients


def run(args):
    from falkordb import FalkorDB
    db = FalkorDB(host=args.host, port=args.port)
    graph = db.select_graph(args.graph)

    # Fetch all products with their ingredients
    rows = graph.query(
        "MATCH (p:Product)-[:CONTAINS_INGREDIENT]->(i:Ingredient) "
        "RETURN p.sku, p.title, collect(i.name)"
    ).result_set

    print(f"Processing {len(rows)} products ...")
    ok = miss = 0

    for sku, title, ingredients in rows:
        ings = [i for i in (ingredients or []) if i]
        roles = classify_all_ingredients(ings, title or "")

        for ing, (role, reason) in roles.items():
            cypher = (
                "MATCH (p:Product {sku: $sku})-[r:CONTAINS_INGREDIENT]->(i:Ingredient {name: $ing}) "
                "SET r.role = $role, r.role_reason = $reason"
            )
            if args.dry_run:
                print(f"  [DRY] {sku} — {ing}: {role} ({reason})")
                ok += 1
            else:
                try:
                    graph.query(cypher, {"sku": sku, "ing": ing, "role": role, "reason": reason})
                    ok += 1
                except Exception as e:
                    print(f"  WARN {sku}/{ing}: {e}")
                    miss += 1

    print(f"\nIngredient roles set: {ok} ok, {miss} failed")
    if args.dry_run:
        print("Dry run — nothing written.")
    else:
        print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--host",  default="localhost")
    parser.add_argument("--port",  type=int, default=6379)
    parser.add_argument("--graph", default="dotandkey")
    run(parser.parse_args())
