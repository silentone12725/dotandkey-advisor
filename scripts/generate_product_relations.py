"""
scripts/generate_product_relations.py

Generates semantic product-to-product relationship edges within each category.

For each product pair in the same category:
  - Compare cap_* scores → MORE_HYDRATING_THAN, BETTER_FOR_OILY_SKIN_THAN, etc.
  - Compare prices → BUDGET_ALTERNATIVE_TO / PREMIUM_ALTERNATIVE_TO
  - Compare ingredient overlap → SIMILAR_TO / OVERLAPS_WITH
  - Identify fragrance-free alternatives

Run after generate_capability_scores.py:
    python3 scripts/generate_product_relations.py [--dry-run] [--category sunscreen]
"""

import argparse
from pathlib import Path


from graph.capability_schema import cap_prop

# Cap delta thresholds for generating comparative edges
DELTA_THRESHOLD = 1.5   # minimum score difference to assert A > B
BUDGET_THRESHOLD = 100  # ₹ price difference for budget/premium edges
OVERLAP_THRESHOLD = 0.5  # fraction of shared ingredients for SIMILAR_TO

# Capability axis → relationship type
AXIS_TO_REL: dict[str, str] = {
    "hydration":      "MORE_HYDRATING_THAN",
    "oil_control":    "BETTER_FOR_OILY_SKIN_THAN",
    "barrier_repair": "BETTER_BARRIER_REPAIR_THAN",
    "brightening":    "MORE_BRIGHTENING_THAN",
    "pigmentation":   "BETTER_FOR_PIGMENTATION_THAN",
    "acne":           "BETTER_FOR_ACNE_THAN",
    "pore_care":      "BETTER_PORE_CARE_THAN",
    "sensitivity":    "GENTLER_THAN",
}

FETCH_BY_CATEGORY = """
MATCH (p:Product)-[:IN_CATEGORY]->(:Category {name: $category})
WHERE p.active = true
OPTIONAL MATCH (p)-[:CONTAINS_INGREDIENT]->(i:Ingredient)
OPTIONAL MATCH (p)-[:FREE_FROM]->(af:AllergenClass)
RETURN p.sku, p.title, p.price,
       p.cap_oil_control, p.cap_hydration, p.cap_barrier_repair,
       p.cap_brightening, p.cap_pigmentation, p.cap_acne,
       p.cap_pore_care, p.cap_sensitivity, p.cap_sun_protection,
       collect(DISTINCT i.name) AS ingredients,
       collect(DISTINCT af.name) AS free_from
"""


def _cap_scores(row: list) -> dict[str, float]:
    axes = ["oil_control", "hydration", "barrier_repair", "brightening",
            "pigmentation", "acne", "pore_care", "sensitivity", "sun_protection"]
    return {ax: (row[3 + i] or 0.0) for i, ax in enumerate(axes)}


def _ingredient_overlap(a_ings: list, b_ings: list) -> float:
    sa, sb = set(a_ings), set(b_ings)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(len(sa), len(sb))


def _write_edge(graph, sku_a: str, sku_b: str, rel: str, reason: str, confidence: float, dry_run: bool):
    cypher = (
        f"MATCH (a:Product {{sku: $a}}) "
        f"MATCH (b:Product {{sku: $b}}) "
        f"MERGE (a)-[r:{rel}]->(b) "
        f"SET r.reason = $reason, r.confidence = $conf, r.source = 'capability_comparison'"
    )
    if dry_run:
        print(f"  [DRY] ({sku_a})-[:{rel}]->({sku_b})  reason='{reason[:60]}'")
    else:
        graph.query(cypher, {"a": sku_a, "b": sku_b, "reason": reason, "conf": confidence})


def run(args):
    from falkordb import FalkorDB
    db = FalkorDB(host=args.host, port=args.port)
    graph = db.select_graph(args.graph)

    categories = ["sunscreen", "moisturizer", "serum", "face_wash",
                  "toner", "mask", "lip_care", "eye_care"]
    if args.category:
        categories = [args.category]

    total_edges = 0

    for category in categories:
        rows = graph.query(FETCH_BY_CATEGORY, {"category": category}).result_set
        if len(rows) < 2:
            continue
        print(f"\nCategory: {category} ({len(rows)} products)")

        # Build product dicts
        products = []
        for row in rows:
            sku, title, price = row[0], row[1], row[2]
            caps = _cap_scores(row)
            ingredients = row[12] or []
            free_from = row[13] or []
            products.append({
                "sku": sku, "title": title,
                "price": float(price or 0),
                "caps": caps, "ingredients": ingredients, "free_from": free_from,
            })

        # Pairwise comparison
        for i, pa in enumerate(products):
            for pb in products[i + 1:]:
                _compare_pair(graph, pa, pb, args.dry_run)
                total_edges += 1

    print(f"\nTotal pairs compared: {total_edges}")
    if args.dry_run:
        print("Dry run — nothing written.")


def _compare_pair(graph, pa: dict, pb: dict, dry_run: bool):
    caps_a, caps_b = pa["caps"], pb["caps"]

    # Capability comparisons
    for axis, rel in AXIS_TO_REL.items():
        score_a = caps_a.get(axis, 0.0)
        score_b = caps_b.get(axis, 0.0)
        delta = score_a - score_b
        if abs(delta) >= DELTA_THRESHOLD:
            winner, loser = (pa, pb) if delta > 0 else (pb, pa)
            w_score = max(score_a, score_b)
            l_score = min(score_a, score_b)
            reason = (
                f"Higher {axis.replace('_', ' ')} capability score "
                f"({w_score:.1f} vs {l_score:.1f})"
            )
            confidence = min(0.95, 0.6 + abs(delta) * 0.1)
            _write_edge(graph, winner["sku"], loser["sku"], rel, reason, confidence, dry_run)

    # Budget/premium comparison
    price_a, price_b = pa["price"], pb["price"]
    if price_a > 0 and price_b > 0:
        diff = abs(price_a - price_b)
        if diff >= BUDGET_THRESHOLD:
            cheaper = pa if price_a < price_b else pb
            pricier = pb if price_a < price_b else pa
            _write_edge(graph, cheaper["sku"], pricier["sku"],
                        "BUDGET_ALTERNATIVE_TO",
                        f"₹{cheaper['price']:.0f} vs ₹{pricier['price']:.0f}",
                        0.90, dry_run)
            _write_edge(graph, pricier["sku"], cheaper["sku"],
                        "PREMIUM_ALTERNATIVE_TO",
                        f"Higher price point with potentially elevated formulation",
                        0.75, dry_run)

    # Ingredient overlap → SIMILAR_TO
    overlap = _ingredient_overlap(pa["ingredients"], pb["ingredients"])
    if overlap >= OVERLAP_THRESHOLD:
        reason = f"{int(overlap*100)}% shared key ingredients"
        _write_edge(graph, pa["sku"], pb["sku"], "SIMILAR_TO", reason, overlap, dry_run)
        _write_edge(graph, pb["sku"], pa["sku"], "SIMILAR_TO", reason, overlap, dry_run)

    # Fragrance-free alternative
    a_ff = "fragrance" in [f.lower() for f in pa["free_from"]]
    b_ff = "fragrance" in [f.lower() for f in pb["free_from"]]
    if a_ff and not b_ff:
        _write_edge(graph, pa["sku"], pb["sku"], "FRAGRANCE_FREE_ALTERNATIVE_TO",
                    "Confirmed fragrance-free vs. not verified", 0.90, dry_run)
    elif b_ff and not a_ff:
        _write_edge(graph, pb["sku"], pa["sku"], "FRAGRANCE_FREE_ALTERNATIVE_TO",
                    "Confirmed fragrance-free vs. not verified", 0.90, dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--host",  default="localhost")
    parser.add_argument("--port",  type=int, default=6379)
    parser.add_argument("--graph", default="dotandkey")
    parser.add_argument("--category", default=None,
                        help="Limit to one category (default: all)")
    run(parser.parse_args())
