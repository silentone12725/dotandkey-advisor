"""
scripts/generate_product_identity.py

Computes the identity_statement for every product based on its highest-ranked
unique strength relative to its category.
Answers: "If this product disappeared tomorrow, what would users lose?"

Example: "Best lightweight sunscreen for oily skin."
         "Most hydrating lip balm."
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph.capability_schema import CAPABILITY_LABELS

FETCH_DNA_RANKS = """
MATCH (p:Product)
RETURN p.sku, p.title, p.category_raw, p.dna_primary, p.dna_secondary,
       p.unique_strengths, p.rank_oil_control_in_cat, p.rank_hydration_in_cat,
       p.rank_barrier_repair_in_cat, p.rank_brightening_in_cat,
       p.rank_pigmentation_in_cat, p.rank_acne_in_cat, p.rank_pore_care_in_cat,
       p.rank_sensitivity_in_cat, p.rank_sun_protection_in_cat, p.rank_lip_repair_in_cat
"""

def _build_identity(row_dict: dict) -> str:
    import json
    cat = (row_dict.get("category_raw") or "product").replace("_", " ")
    if cat.endswith("care"):
        cat = cat + " product"
        
    dna_primary = row_dict.get("dna_primary")
    strengths_json = row_dict.get("unique_strengths")
    
    strengths = []
    if strengths_json:
        try:
            strengths = json.loads(strengths_json)
        except:
            pass
            
    # Try to find a #1 rank
    for ax in strengths:
        rank = row_dict.get(f"rank_{ax}_in_cat")
        if rank == 1:
            label = CAPABILITY_LABELS.get(ax, ax.replace("_", " ")).lower()
            return f"Best {label} {cat}."
            
    # If no #1 rank, use Top 3 if available
    for ax in strengths:
        rank = row_dict.get(f"rank_{ax}_in_cat")
        if rank and rank <= 3:
            label = CAPABILITY_LABELS.get(ax, ax.replace("_", " ")).lower()
            return f"Top-rated {label} {cat}."
            
    # Fallback to primary DNA
    if dna_primary:
        label = CAPABILITY_LABELS.get(dna_primary, dna_primary.replace("_", " ")).lower()
        return f"Solid {label} {cat}."
        
    return f"A balanced {cat}."
    

def run(args):
    from falkordb import FalkorDB
    db = FalkorDB(host=args.host, port=args.port)
    graph = db.select_graph(args.graph)

    rows = graph.query(FETCH_DNA_RANKS).result_set
    print(f"Computing Product Identity for {len(rows)} products ...")
    
    written = 0
    for row in rows:
        sku = row[0]
        p = {
            "category_raw": row[2],
            "dna_primary": row[3],
            "dna_secondary": row[4],
            "unique_strengths": row[5],
            "rank_oil_control_in_cat": row[6],
            "rank_hydration_in_cat": row[7],
            "rank_barrier_repair_in_cat": row[8],
            "rank_brightening_in_cat": row[9],
            "rank_pigmentation_in_cat": row[10],
            "rank_acne_in_cat": row[11],
            "rank_pore_care_in_cat": row[12],
            "rank_sensitivity_in_cat": row[13],
            "rank_sun_protection_in_cat": row[14],
            "rank_lip_repair_in_cat": row[15],
        }
        
        identity = _build_identity(p)
        
        if args.dry_run:
            if written < 5:
                print(f"  [DRY] {sku}: {identity}")
            written += 1
            continue
            
        cypher = "MATCH (p:Product {sku: $sku}) SET p.identity_statement = $ident"
        try:
            graph.query(cypher, {"sku": sku, "ident": identity})
            written += 1
        except Exception as e:
            print(f"  WARN {sku}: {e}")
            
    if args.dry_run:
        print(f"Dry run — computed {written} updates, nothing written.")
    else:
        print(f"\nProduct identity written to {written} products.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--host",  default="localhost")
    parser.add_argument("--port",  type=int, default=6379)
    parser.add_argument("--graph", default="dotandkey")
    run(parser.parse_args())
