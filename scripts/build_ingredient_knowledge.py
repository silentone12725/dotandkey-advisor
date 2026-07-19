"""
scripts/build_ingredient_knowledge.py

Ingests ingredient→concern and ingredient→capability edges from
graph/ingredient_knowledge.py into FalkorDB.

Run after csv_to_graph.py (requires Ingredient + Concern nodes to exist):
    python3 scripts/build_ingredient_knowledge.py [--dry-run] [--graph dotandkey]
"""

import argparse
from pathlib import Path


from graph.ingredient_knowledge import INGREDIENT_CONCERN_EDGES, INGREDIENT_CAPABILITY_EDGES
from graph.capability_schema import CAPABILITY_AXES


def run(args):
    if not args.dry_run:
        from falkordb import FalkorDB
        db = FalkorDB(host=args.host, port=args.port)
        graph = db.select_graph(args.graph)

        # Ensure Capability nodes exist
        for axis in CAPABILITY_AXES:
            graph.query(
                "MERGE (:Capability {name: $name})",
                {"name": axis},
            )
        print(f"Ensured {len(CAPABILITY_AXES)} Capability nodes.")

    concern_ok = concern_miss = 0
    cap_ok = cap_miss = 0

    print(f"\nIngesting {len(INGREDIENT_CONCERN_EDGES)} ingredient→concern edges ...")
    for ing, rel, concern, strength, confidence, explanation in INGREDIENT_CONCERN_EDGES:
        cypher = (
            f"MATCH (i:Ingredient {{name: $ing}}) "
            f"MATCH (c:Concern {{name: $concern}}) "
            f"MERGE (i)-[r:{rel}]->(c) "
            f"SET r.strength = $strength, r.confidence = $confidence, r.explanation = $explanation"
        )
        params = {"ing": ing, "concern": concern, "strength": strength,
                  "confidence": confidence, "explanation": explanation}
        if args.dry_run:
            print(f"  [DRY] ({ing})-[:{rel}]->({concern})  str={strength}")
            concern_ok += 1
        else:
            try:
                graph.query(cypher, params)
                concern_ok += 1
            except Exception as e:
                print(f"  WARN: {ing}→{concern}: {e}")
                concern_miss += 1

    print(f"\nIngesting {len(INGREDIENT_CAPABILITY_EDGES)} ingredient→capability edges ...")
    for ing, rel, axis, strength in INGREDIENT_CAPABILITY_EDGES:
        cypher = (
            f"MATCH (i:Ingredient {{name: $ing}}) "
            f"MATCH (cap:Capability {{name: $axis}}) "
            f"MERGE (i)-[r:{rel}]->(cap) "
            f"SET r.strength = $strength"
        )
        params = {"ing": ing, "axis": axis, "strength": strength}
        if args.dry_run:
            print(f"  [DRY] ({ing})-[:{rel}]->({axis})  str={strength}")
            cap_ok += 1
        else:
            try:
                graph.query(cypher, params)
                cap_ok += 1
            except Exception as e:
                print(f"  WARN: {ing}→{axis}: {e}")
                cap_miss += 1

    print(f"\nSummary:")
    print(f"  Ingredient→Concern edges: {concern_ok} ok, {concern_miss} failed")
    print(f"  Ingredient→Capability edges: {cap_ok} ok, {cap_miss} failed")
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
    run(parser.parse_args())
