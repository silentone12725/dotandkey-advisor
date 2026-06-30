"""
scripts/generate_capability_scores.py  (v2)

Computes full capability profiles (score + confidence + sources) for every
Product node and writes them as properties:

    cap_oil_control          float 0-10
    cap_oil_control_conf     float 0-1
    cap_oil_control_src      str   JSON e.g. '["niacinamide:0.90","zinc_oxide:0.70"]'
    ...repeated for all 10 axes...

Run after backfill_ingredient_roles.py:
    python3 scripts/generate_capability_scores.py [--dry-run]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph.capability_schema import CAPABILITY_AXES, cap_prop
from graph.capability_scorer import score_product_v2
from graph.ingredient_importance import classify_all_ingredients

FETCH_PRODUCTS = """
MATCH (p:Product)
OPTIONAL MATCH (p)-[ri:CONTAINS_INGREDIENT]->(i:Ingredient)
OPTIONAL MATCH (p)-[:TARGETS_CONCERN]->(c:Concern)
RETURN p.sku AS sku, p.title AS title, p.category_raw AS category_raw,
       collect(DISTINCT [i.name, ri.role, ri.role_reason]) AS ing_roles,
       collect(DISTINCT c.name) AS concerns
"""


def run(args):
    from falkordb import FalkorDB
    db = FalkorDB(host=args.host, port=args.port)
    graph = db.select_graph(args.graph)

    rows = graph.query(FETCH_PRODUCTS).result_set
    print(f"Computing capability scores (v2) for {len(rows)} products ...")

    scored = 0
    for row in rows:
        sku, title, category_raw, ing_roles_raw, concerns = row

        # Parse ingredient roles from graph (role may be None if not yet set)
        ingredient_roles: dict[str, tuple[str, str]] = {}
        ingredients: list[str] = []
        for entry in (ing_roles_raw or []):
            if not entry or not entry[0]:
                continue
            ing = entry[0]
            role   = entry[1] if len(entry) > 1 and entry[1] else None
            reason = entry[2] if len(entry) > 2 and entry[2] else None
            ingredients.append(ing)
            if role:
                ingredient_roles[ing] = (role, reason or "")

        # Fallback: classify inline if roles not yet in graph
        if not ingredient_roles and ingredients:
            ingredient_roles = classify_all_ingredients(ingredients, title or "")

        product = {
            "sku": sku,
            "title": title or "",
            "category_raw": category_raw or "",
            "ingredients": ingredients,
            "matched_concerns": concerns or [],
            "_ingredient_roles": ingredient_roles,
        }

        profiles = score_product_v2(product)

        if args.dry_run:
            top = sorted(
                ((ax, d["score"], d["confidence"]) for ax, d in profiles.items()),
                key=lambda x: -x[1],
            )[:4]
            print(f"  {sku[:20]:20s} | " + " | ".join(
                f"{ax}={s:.1f}(c={c:.2f})" for ax, s, c in top
            ))
            scored += 1
            continue

        # Build SET params
        set_parts = []
        params: dict = {"sku": sku}
        for axis, data in profiles.items():
            prop_score = cap_prop(axis)
            prop_conf  = f"cap_{axis}_conf"
            prop_src   = f"cap_{axis}_src"
            set_parts += [
                f"p.{prop_score} = ${prop_score}",
                f"p.{prop_conf} = ${prop_conf}",
                f"p.{prop_src} = ${prop_src}",
            ]
            params[prop_score] = data["score"]
            params[prop_conf]  = data["confidence"]
            params[prop_src]   = json.dumps(data["sources"][:6])  # cap at 6 sources

        cypher = f"MATCH (p:Product {{sku: $sku}}) SET {', '.join(set_parts)}"
        try:
            graph.query(cypher, params)
            scored += 1
        except Exception as e:
            print(f"  WARN {sku}: {e}")

    print(f"\nCapability profiles written: {scored}/{len(rows)} products")
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
