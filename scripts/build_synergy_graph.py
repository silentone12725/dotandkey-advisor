"""
scripts/build_synergy_graph.py

Ingests ingredient synergy edges (SYNERGIZES_WITH) into FalkorDB.
Both directions are stored so traversal works from either ingredient.

Run after build_ingredient_knowledge.py:
    python3 scripts/build_synergy_graph.py [--dry-run] [--graph dotandkey]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph.ingredient_synergy import SYNERGY_EDGES


def run(args):
    if not args.dry_run:
        from falkordb import FalkorDB
        db = FalkorDB(host=args.host, port=args.port)
        graph = db.select_graph(args.graph)

    ok = miss = 0

    print(f"Ingesting {len(SYNERGY_EDGES)} synergy pairs ({len(SYNERGY_EDGES)*2} directed edges) ...")
    for ing_a, ing_b, ev_strength, confidence, concerns, explanation, source in SYNERGY_EDGES:
        concerns_json = json.dumps(concerns)
        for (a, b) in [(ing_a, ing_b), (ing_b, ing_a)]:
            cypher = (
                "MATCH (a:Ingredient {name: $a}) "
                "MATCH (b:Ingredient {name: $b}) "
                "MERGE (a)-[r:SYNERGIZES_WITH]->(b) "
                "SET r.evidence_strength = $ev, r.confidence = $conf, "
                "    r.supported_concerns = $concerns, r.explanation = $explanation, "
                "    r.source = $source"
            )
            params = {
                "a": a, "b": b,
                "ev": ev_strength, "conf": confidence,
                "concerns": concerns_json,
                "explanation": explanation,
                "source": source,
            }
            if args.dry_run:
                print(f"  [DRY] ({a})-[:SYNERGIZES_WITH]->({b})  ev={ev_strength}")
                ok += 1
            else:
                try:
                    graph.query(cypher, params)
                    ok += 1
                except Exception as e:
                    print(f"  WARN: {a}↔{b}: {e}")
                    miss += 1

    print(f"\nSynergy edges: {ok} ok, {miss} failed")
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
