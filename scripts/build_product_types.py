"""
scripts/build_product_types.py

Ingests the ProductType ontology into FalkorDB:
  - ProductType nodes with HAS_SUBTYPE hierarchy
  - ProductType→ProductType routine relationship edges
  - Product→ProductType HAS_TYPE edges (auto-assigned from title keywords)

Run after csv_to_graph.py:
    python3 scripts/build_product_types.py [--dry-run] [--graph dotandkey]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph.product_type_ontology import (
    ALL_PRODUCT_TYPES, PRODUCT_TYPE_HIERARCHY,
    PRODUCT_TYPE_RELATIONS, TITLE_TO_TYPE_HINTS,
)


def run(args):
    if not args.dry_run:
        from falkordb import FalkorDB
        db = FalkorDB(host=args.host, port=args.port)
        graph = db.select_graph(args.graph)

    # 1. ProductType nodes
    print(f"Creating {len(ALL_PRODUCT_TYPES)} ProductType nodes ...")
    for pt in ALL_PRODUCT_TYPES:
        if args.dry_run:
            continue
        graph.query("MERGE (:ProductType {name: $name})", {"name": pt["name"]})

    # 2. HAS_SUBTYPE hierarchy
    total_hierarchy = 0
    for parent, subs in PRODUCT_TYPE_HIERARCHY.items():
        for sub in subs:
            if not args.dry_run:
                graph.query(
                    "MATCH (p:ProductType {name: $parent}) "
                    "MATCH (s:ProductType {name: $sub}) "
                    "MERGE (p)-[:HAS_SUBTYPE]->(s)",
                    {"parent": parent, "sub": sub},
                )
            total_hierarchy += 1
    print(f"HAS_SUBTYPE edges: {total_hierarchy}")

    # 3. Routine relationship edges
    print(f"Creating {len(PRODUCT_TYPE_RELATIONS)} routine relationship edges ...")
    for type_a, rel, type_b in PRODUCT_TYPE_RELATIONS:
        if not args.dry_run:
            try:
                graph.query(
                    f"MATCH (a:ProductType {{name: $a}}) "
                    f"MATCH (b:ProductType {{name: $b}}) "
                    f"MERGE (a)-[:{rel}]->(b)",
                    {"a": type_a, "b": type_b},
                )
            except Exception as e:
                print(f"  WARN {type_a}→{type_b}: {e}")

    # 4. Product → ProductType HAS_TYPE edges
    if not args.dry_run:
        result = graph.query(
            "MATCH (p:Product) RETURN p.sku, p.title, p.category_raw"
        )
        products = result.result_set
        print(f"\nAssigning HAS_TYPE to {len(products)} products ...")
        assigned = 0
        for sku, title, category_raw in products:
            title_l = (title or "").lower()
            cat_key = _map_category(category_raw or "")
            hints = TITLE_TO_TYPE_HINTS.get(cat_key, [])
            matched_type = None
            for keyword, ptype in hints:
                if keyword in title_l:
                    matched_type = ptype
                    break
            if not matched_type and cat_key:
                matched_type = cat_key  # fallback to parent type
            if matched_type:
                try:
                    graph.query(
                        "MATCH (p:Product {sku: $sku}) "
                        "MATCH (t:ProductType {name: $type}) "
                        "MERGE (p)-[:HAS_TYPE]->(t)",
                        {"sku": sku, "type": matched_type},
                    )
                    assigned += 1
                except Exception as e:
                    print(f"  WARN {sku}: {e}")
        print(f"  HAS_TYPE edges assigned: {assigned}/{len(products)}")

    if args.dry_run:
        print("\nDry run — nothing written.")
    else:
        print("Done.")


def _map_category(category_raw: str) -> str:
    _MAP = {
        "Sunscreen": "sunscreen", "Moisturiser": "moisturizer",
        "Face Wash": "face_wash", "Serum": "serum", "Toner": "toner",
        "Mask": "mask", "Lip Balm": "lip_care", "Eye Care": "eye_care",
    }
    return _MAP.get(category_raw, category_raw.lower().replace(" ", "_"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--host",  default="localhost")
    parser.add_argument("--port",  type=int, default=6379)
    parser.add_argument("--graph", default="dotandkey")
    run(parser.parse_args())
