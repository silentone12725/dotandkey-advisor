"""
scripts/generate_differentiation.py

Computes product differentiation within its category.
Calculates rank, total, and percentile for all 10 capability axes.
Also computes overall unique strengths and weaknesses relative to the category.

Writes properties to Product nodes:
  rank_oil_control_in_cat      int (1 = best)
  rank_oil_control_total       int
  rank_oil_control_percentile  float (0.0 - 1.0, 1.0 = top)
  ... (for all 10 axes)
  unique_strengths             str JSON list
  unique_weaknesses            str JSON list
  beats_pct                    float

Run after generate_capability_scores.py:
  python3 scripts/generate_differentiation.py [--dry-run]
"""

import argparse
import json
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph.capability_schema import CAPABILITY_AXES, cap_prop

FETCH_CAT_PRODUCTS = """
MATCH (p:Product)-[:IN_CATEGORY]->(c:Category)
RETURN c.name, p.sku,
       p.cap_oil_control, p.cap_hydration, p.cap_barrier_repair, p.cap_brightening,
       p.cap_pigmentation, p.cap_acne, p.cap_pore_care, p.cap_sensitivity,
       p.cap_sun_protection, p.cap_lip_repair
"""


def _compute_ranks_for_category(products: list[dict]) -> dict[str, dict]:
    """
    products: [{"sku": str, "cap_oil_control": float, ...}, ...]
    returns: {sku: {"rank_oil_control_in_cat": int, "rank_oil_control_percentile": float, ...}}
    """
    total = len(products)
    if total == 0:
        return {}

    updates = defaultdict(dict)
    
    # 1. Rank each axis
    for axis in CAPABILITY_AXES:
        prop = cap_prop(axis)
        # Sort by score desc. Tie-breaker doesn't matter much, but we could use SKU to be stable.
        sorted_prods = sorted(products, key=lambda p: (p.get(prop) or 0.0, p["sku"]), reverse=True)
        
        for rank_0_idx, p in enumerate(sorted_prods):
            rank = rank_0_idx + 1
            # percentile: 1.0 = best, 0.0 = worst. 
            # If total == 1, percentile is 1.0.
            pct = (total - rank) / (total - 1) if total > 1 else 1.0
            
            sku = p["sku"]
            updates[sku][f"rank_{axis}_in_cat"] = rank
            updates[sku][f"rank_{axis}_total"] = total
            updates[sku][f"rank_{axis}_percentile"] = round(pct, 3)

    # 2. Overall category stats & strengths/weaknesses
    for p in products:
        sku = p["sku"]
        upd = updates[sku]
        
        # Overall score (sum of all axes) for beats_pct
        p["_total_score"] = sum(p.get(cap_prop(ax)) or 0.0 for ax in CAPABILITY_AXES)
    
    sorted_overall = sorted(products, key=lambda p: (p["_total_score"], p["sku"]), reverse=True)
    for rank_0_idx, p in enumerate(sorted_overall):
        rank = rank_0_idx + 1
        pct = (total - rank) / (total - 1) if total > 1 else 1.0
        updates[p["sku"]]["beats_pct"] = round(pct, 3)
        
    for p in products:
        sku = p["sku"]
        upd = updates[sku]
        
        strengths = []
        weaknesses = []
        
        for axis in CAPABILITY_AXES:
            score = p.get(cap_prop(axis)) or 0.0
            pct = upd[f"rank_{axis}_percentile"]
            rank = upd[f"rank_{axis}_in_cat"]
            
            # Strength: top 25% of category OR rank <= 3, AND meaningful score (>= 4.0)
            if (pct >= 0.75 or rank <= 3) and score >= 4.0:
                strengths.append((axis, score))
                
            # Weakness: bottom 25% of category, and score is low (< 3.0)
            elif pct <= 0.25 and score < 3.0:
                weaknesses.append((axis, score))
                
        # Sort strengths by score desc
        strengths.sort(key=lambda x: -x[1])
        weaknesses.sort(key=lambda x: x[1])
        
        upd["unique_strengths"] = [s[0] for s in strengths][:3] # top 3
        upd["unique_weaknesses"] = [w[0] for w in weaknesses][:3] # bottom 3
        
    return updates


def run(args):
    from falkordb import FalkorDB
    db = FalkorDB(host=args.host, port=args.port)
    graph = db.select_graph(args.graph)

    rows = graph.query(FETCH_CAT_PRODUCTS).result_set
    
    cat_products = defaultdict(list)
    for row in rows:
        cat = row[0]
        p = {"sku": row[1]}
        for i, axis in enumerate(CAPABILITY_AXES):
            p[cap_prop(axis)] = float(row[2+i] or 0.0)
        cat_products[cat].append(p)
        
    print(f"Computing differentiation for {len(rows)} products across {len(cat_products)} categories...")
    
    all_updates = {}
    for cat, prods in cat_products.items():
        cat_upd = _compute_ranks_for_category(prods)
        all_updates.update(cat_upd)
        
    written = 0
    for sku, upd in all_updates.items():
        if args.dry_run:
            if written < 5:
                print(f"  [DRY] {sku}: beats_pct={upd['beats_pct']}, str={upd['unique_strengths']}, weak={upd['unique_weaknesses']}")
            written += 1
            continue
            
        set_parts = []
        params = {"sku": sku}
        for k, v in upd.items():
            if isinstance(v, list):
                v = json.dumps(v)
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
        print(f"\nDifferentiation props written to {written} products.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--host",  default="localhost")
    parser.add_argument("--port",  type=int, default=6379)
    parser.add_argument("--graph", default="dotandkey")
    run(parser.parse_args())
