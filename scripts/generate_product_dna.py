"""
scripts/generate_product_dna.py

Writes the Product DNA properties computed by graph/product_dna.py to all Product nodes.

Run after generate_capability_scores.py:
  python3 scripts/generate_product_dna.py [--dry-run]
"""

import argparse
from pathlib import Path


from graph.product_dna import compute_product_dna
from graph.capability_schema import CAPABILITY_AXES, cap_prop

FETCH_PRODUCTS = """
MATCH (p:Product)
OPTIONAL MATCH (p)-[:HAS_TEXTURE]->(t:Texture)
RETURN p.sku, p.title, p.category_raw, t.name,
       p.cap_oil_control, p.cap_hydration, p.cap_barrier_repair, p.cap_brightening,
       p.cap_pigmentation, p.cap_acne, p.cap_pore_care, p.cap_sensitivity,
       p.cap_sun_protection, p.cap_lip_repair
"""


def run(args):
    from falkordb import FalkorDB
    db = FalkorDB(host=args.host, port=args.port)
    graph = db.select_graph(args.graph)

    rows = graph.query(FETCH_PRODUCTS).result_set
    print(f"Computing Product DNA for {len(rows)} products ...")
    
    written = 0
    for row in rows:
        sku = row[0]
        p = {
            "sku": sku,
            "title": row[1] or "",
            "category_raw": row[2] or "",
            "texture": row[3] or "Natural",
        }
        for i, axis in enumerate(CAPABILITY_AXES):
            p[cap_prop(axis)] = float(row[4+i] or 0.0)
            
        dna = compute_product_dna(p)
        
        if args.dry_run:
            if written < 5:
                print(f"  [DRY] {sku}: {dna['dna_label']} | Primary: {dna['dna_primary']}")
            written += 1
            continue
            
        set_parts = []
        params = {"sku": sku}
        for k, v in dna.items():
            set_parts.append(f"p.{k} = ${k}")
            params[k] = v
            
        cypher = f"MATCH (p:Product {{sku: $sku}}) SET {', '.join(set_parts)}"
        try:
            graph.query(cypher, params)
            written += 1
        except Exception as e:
            print(f"  WARN {sku}: {e}")
            
    if args.dry_run:
        print(f"Dry run — computed {written} updates, nothing written.")
    else:
        print(f"\nProduct DNA props written to {written} products.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--host",  default="localhost")
    parser.add_argument("--port",  type=int, default=6379)
    parser.add_argument("--graph", default="dotandkey")
    run(parser.parse_args())
