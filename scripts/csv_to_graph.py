"""
Phase 0 — CSV -> FalkorDB ingest.

Reads data/dot_and_key_products_comparison.csv, normalizes tags via
graph/taxonomy.py, and writes Product nodes + relationship edges into
a FalkorDB graph.

Usage:
    # dry run — no DB connection, just prints stats and sample Cypher
    python3 scripts/csv_to_graph.py --dry-run

    # real run — requires FalkorDB reachable (default localhost:6379)
    python3 scripts/csv_to_graph.py --host localhost --port 6379 --graph dotandkey
"""

import argparse
import csv
import re
from pathlib import Path
from collections import Counter


from graph.taxonomy import TAG_MAP, TYPE_TO_CATEGORY, find_allergen_free_claims

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "dot_and_key_products_comparison.csv"

# ---------------------------------------------------------------------------
# Size extraction from Variant column
# ---------------------------------------------------------------------------

_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|g|gm|mg|l)\b", re.IGNORECASE)


def _parse_size_g(variant: str) -> float | None:
    """Parse the Variant string (e.g. '50ml', '100g', '1L') into grams/ml.
    Returns None if no size found.
    """
    m = _SIZE_RE.search(variant)
    if not m:
        return None
    amount = float(m.group(1))
    unit = m.group(2).lower()
    if unit in ("l",):
        return amount * 1000
    if unit in ("mg",):
        return amount / 1000
    return amount   # ml, g, gm all normalise to the same numeric scale


# ---------------------------------------------------------------------------
# Combo row detection and component SKU parsing
# ---------------------------------------------------------------------------

_COMBO_SEP = re.compile(r"_DK_")


def _parse_combo_components(sku: str) -> list[str] | None:
    """Split a compound combo SKU like 'DK_BROFM50_DK_BHBRS' into its
    component product SKUs ['DK_BROFM50', 'DK_BHBRS'].

    Returns None if this doesn't look like a proper multi-product combo
    (i.e. a single '_DK_' separator is required).  Handles trailing
    version suffixes like '_DK_NVBHBRS_2' by stripping the trailing '_N'.
    """
    if not _COMBO_SEP.search(sku):
        return None
    parts = _COMBO_SEP.split(sku)
    if len(parts) < 2:
        return None
    components = []
    for i, part in enumerate(parts):
        comp = part if i == 0 else "DK_" + part
        # strip trailing version number (e.g. _2, _3)
        comp = re.sub(r"_\d+$", "", comp)
        if comp and len(comp) > 3:
            components.append(comp)
    return components if len(components) >= 2 else None


def load_rows():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def parse_row(row):
    """Convert one CSV row into a structured product dict, or None if this
    row should be skipped (e.g. sample-size 'Free' variants)."""
    if row["Type"].strip() == "Free":
        return None

    sku_raw = row["SKU"].strip()

    # Multi-product combo SKU — handle separately in build_combos(), not here
    if sku_raw.count("DK_") > 1:
        return None

    raw_type = row["Type"].strip()
    category = TYPE_TO_CATEGORY.get(raw_type)

    tags = [t.strip() for t in row["Tags (Features/Applications)"].split(",") if t.strip()]

    edges = {
        "SKIN_TYPE": set(),
        "CONCERN": set(),
        "SEASON": set(),
        "INGREDIENT": set(),
        "TEXTURE": set(),
    }
    for tag in tags:
        for edge_type, name in TAG_MAP.get(tag, []):
            edges[edge_type].add(name)

    allergen_free = set(find_allergen_free_claims(row["Description (Use/Features)"]))

    try:
        price = float(row["Price"]) if row["Price"] else None
    except ValueError:
        price = None
    try:
        compare_at = float(row["Compare At Price"]) if row["Compare At Price"] else None
    except ValueError:
        compare_at = None

    variant = row["Variant"].strip()
    return {
        "sku": row["SKU"].strip(),
        "title": row["Title"].strip(),
        "variant": variant,
        "category_raw": raw_type,
        "category": category,
        "price": price,
        "compare_at_price": compare_at,
        "description": row["Description (Use/Features)"].strip(),
        "edges": edges,
        "allergen_free": allergen_free,
        "size_g": _parse_size_g(variant),   # None when no size data
    }


def build_products():
    products = {}
    skipped_free = 0
    skipped_no_category = 0

    for row in load_rows():
        parsed = parse_row(row)
        if parsed is None:
            skipped_free += 1
            continue
        if parsed["category"] is None:
            skipped_no_category += 1
            continue

        sku = parsed["sku"]
        if sku in products:
            # duplicate SKU (non-Free) — merge edges, keep first title/price
            existing = products[sku]
            for k in existing["edges"]:
                existing["edges"][k] |= parsed["edges"][k]
            existing["allergen_free"] |= parsed["allergen_free"]
        else:
            products[sku] = parsed

    return list(products.values()), skipped_free, skipped_no_category


def build_combos():
    """Parse multi-SKU rows into Combo dicts (tagged with skin type / concern
    edges from their tags column, linked to component Product SKUs via INCLUDES).

    Rows with malformed SKUs (missing `_DK_` separator between components) are
    skipped — they cannot be reliably decomposed into their constituent products.
    """
    combos = []
    skipped_malformed = 0

    for row in load_rows():
        sku_raw = row["SKU"].strip()
        if sku_raw.count("DK_") <= 1:
            continue   # single-product row, handled by build_products()

        components = _parse_combo_components(sku_raw)
        if not components:
            skipped_malformed += 1
            continue

        tags = [t.strip() for t in row["Tags (Features/Applications)"].split(",") if t.strip()]
        edges: dict[str, set] = {
            "SKIN_TYPE": set(),
            "CONCERN": set(),
        }
        for tag in tags:
            for edge_type, name in TAG_MAP.get(tag, []):
                if edge_type in edges:
                    edges[edge_type].add(name)

        try:
            price = float(row["Price"]) if row["Price"] else None
        except ValueError:
            price = None
        try:
            compare_at = float(row["Compare At Price"]) if row["Compare At Price"] else None
        except ValueError:
            compare_at = None

        combos.append({
            "sku": sku_raw,
            "title": row["Title"].strip(),
            "price": price,
            "compare_at_price": compare_at,
            "description": row["Description (Use/Features)"].strip(),
            "components": components,   # list of component Product SKUs
            "edges": edges,
        })

    return combos, skipped_malformed


def to_cypher(product):
    """Return a list of Cypher statements (with params) for one product."""
    stmts = []

    merge_params = {
        "sku": product["sku"],
        "title": product["title"],
        "variant": product["variant"],
        "category_raw": product["category_raw"],
        "price": product["price"],
        "compare_at_price": product["compare_at_price"],
        "description": product["description"],
    }
    set_clause = (
        "SET p.title = $title, p.variant = $variant, p.category_raw = $category_raw, "
        "p.price = $price, p.compare_at_price = $compare_at_price, "
        "p.description = $description, p.active = true"
    )
    if product.get("size_g") is not None:
        set_clause += ", p.size_g = $size_g"
        merge_params["size_g"] = product["size_g"]

    stmts.append((
        f"MERGE (p:Product {{sku: $sku}}) {set_clause}",
        merge_params,
    ))

    if product["category"]:
        stmts.append((
            "MATCH (p:Product {sku: $sku}) MATCH (c:Category {name: $name}) "
            "MERGE (p)-[:IN_CATEGORY]->(c)",
            {"sku": product["sku"], "name": product["category"]},
        ))

    edge_rel = {
        "SKIN_TYPE": ("SkinType", "SUITS_SKIN_TYPE"),
        "CONCERN": ("Concern", "TARGETS_CONCERN"),
        "SEASON": ("Season", "BEST_IN_SEASON"),
        "INGREDIENT": ("Ingredient", "CONTAINS_INGREDIENT"),
        "TEXTURE": ("Texture", "HAS_TEXTURE"),
    }
    for edge_type, names in product["edges"].items():
        label, rel = edge_rel[edge_type]
        for name in names:
            stmts.append((
                f"MATCH (p:Product {{sku: $sku}}) MATCH (n:{label} {{name: $name}}) "
                f"MERGE (p)-[:{rel}]->(n)",
                {"sku": product["sku"], "name": name},
            ))

    for allergen in product["allergen_free"]:
        stmts.append((
            "MATCH (p:Product {sku: $sku}) MATCH (a:AllergenClass {name: $name}) "
            "MERGE (p)-[:FREE_FROM]->(a)",
            {"sku": product["sku"], "name": allergen},
        ))

    return stmts


def combo_to_cypher(combo):
    """Return Cypher statements to create a Combo node + its edges."""
    stmts = []

    stmts.append((
        "MERGE (c:Combo {sku: $sku}) "
        "SET c.title = $title, c.price = $price, c.compare_at_price = $compare_at_price, "
        "c.description = $description, c.url = '', c.image_url = '', c.active = true",
        {
            "sku": combo["sku"],
            "title": combo["title"],
            "price": combo["price"],
            "compare_at_price": combo["compare_at_price"],
            "description": combo["description"],
        },
    ))

    # link to combo Category node
    stmts.append((
        "MATCH (c:Combo {sku: $sku}) MATCH (cat:Category {name: 'combo'}) "
        "MERGE (c)-[:IN_CATEGORY]->(cat)",
        {"sku": combo["sku"]},
    ))

    # INCLUDES edges to component Product nodes
    for component_sku in combo["components"]:
        stmts.append((
            "MATCH (c:Combo {sku: $sku}) "
            "MERGE (p:Product {sku: $comp_sku}) "   # MERGE so we don't fail if product missing
            "MERGE (c)-[:INCLUDES]->(p)",
            {"sku": combo["sku"], "comp_sku": component_sku},
        ))

    # skin type edges
    for st_name in combo["edges"].get("SKIN_TYPE", set()):
        stmts.append((
            "MATCH (c:Combo {sku: $sku}) MATCH (st:SkinType {name: $name}) "
            "MERGE (c)-[:SUITS_SKIN_TYPE]->(st)",
            {"sku": combo["sku"], "name": st_name},
        ))

    # concern edges
    for cn_name in combo["edges"].get("CONCERN", set()):
        stmts.append((
            "MATCH (c:Combo {sku: $sku}) MATCH (cn:Concern {name: $name}) "
            "MERGE (c)-[:TARGETS_CONCERN]->(cn)",
            {"sku": combo["sku"], "name": cn_name},
        ))

    return stmts


def run(args):
    products, skipped_free, skipped_no_category = build_products()
    combos, skipped_malformed = build_combos()

    print(f"Parsed products: {len(products)}")
    print(f"Parsed combos:   {len(combos)}")
    print(f"Skipped (Type=='Free', sample-size duplicates): {skipped_free}")
    print(f"Skipped (no category mapping): {skipped_no_category}")
    print(f"Skipped (malformed combo SKU): {skipped_malformed}")

    edge_counts = Counter()
    for p in products:
        for edge_type, names in p["edges"].items():
            edge_counts[edge_type] += len(names)
        edge_counts["ALLERGEN_FREE"] += len(p["allergen_free"])
        if p["category"]:
            edge_counts["CATEGORY"] += 1

    print("\nEdge counts (products):")
    for k, v in edge_counts.items():
        print(f"  {k}: {v}")

    if args.dry_run:
        print("\n--- sample Cypher for first product ---")
        sample = products[0]
        print(f"# {sample['sku']} — {sample['title']}")
        for stmt, params in to_cypher(sample)[:6]:
            print(stmt, params)
        if combos:
            print("\n--- sample Cypher for first combo ---")
            for stmt, params in combo_to_cypher(combos[0])[:4]:
                print(stmt, params)
        print(f"\nDry run complete. {len(products)} products + {len(combos)} combos ready.")
        return

    # real run
    from falkordb import FalkorDB

    db = FalkorDB(host=args.host, port=args.port)
    graph = db.select_graph(args.graph)

    print(f"\nApplying schema from graph/schema.cypher ...")
    schema_path = Path(__file__).resolve().parent.parent / "graph" / "schema.cypher"
    schema_src = schema_path.read_text()
    # strip full-line comments first, then split on ';' so semicolons that
    # might appear inside comment text can't break statement boundaries
    no_comments = "\n".join(
        l for l in schema_src.splitlines() if not l.strip().startswith("//")
    )
    for stmt in [s.strip() for s in no_comments.split(";") if s.strip()]:
        try:
            graph.query(stmt)
        except Exception as e:
            if "already indexed" in str(e).lower():
                pass  # index exists from a previous ingest run, safe to skip
            else:
                raise

    print(f"Ingesting {len(products)} products ...")
    for p in products:
        for stmt, params in to_cypher(p):
            graph.query(stmt, params)

    print(f"Ingesting {len(combos)} combos ...")
    for c in combos:
        for stmt, params in combo_to_cypher(c):
            graph.query(stmt, params)

    # Apply media (url, image_url) from product_media.json
    media_path = Path(__file__).resolve().parent.parent / "data" / "product_media.json"
    if media_path.exists():
        import json
        media = json.loads(media_path.read_text())
        print(f"Applying media for {len(media)} known products ...")
        for sku, info in media.items():
            graph.query(
                "MATCH (p:Product {sku: $sku}) "
                "SET p.url = $url, p.image_url = $image_url, "
                "p.shade = $shade, p.available = $available",
                {
                    "sku": sku,
                    "url": info.get("product_url", ""),
                    "image_url": info.get("image_url", ""),
                    "shade": info.get("shade", ""),
                    "available": info.get("available", True),
                },
            )

    print("Done.")

    # quick sanity check
    result = graph.query("MATCH (p:Product) RETURN count(p) AS n")
    print(f"Product nodes in graph: {result.result_set[0][0]}")
    result2 = graph.query("MATCH (c:Combo) RETURN count(c) AS n")
    print(f"Combo nodes in graph:   {result2.result_set[0][0]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--graph", default="dotandkey")
    args = parser.parse_args()
    run(args)