#!/usr/bin/env python3
"""
scripts/update_products.py

Fetch the live Dot & Key product catalog from Shopify and append any new
products to data/dot_and_key_products_comparison.csv, then rebuild the graph.

Usage:
    python3 scripts/update_products.py            # fetch + diff + append to CSV
    python3 scripts/update_products.py --ingest   # also re-run csv_to_graph + fetch_product_media
    python3 scripts/update_products.py --dry-run  # print new products, don't write
"""

import argparse
import csv
import html
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "dot_and_key_products_comparison.csv"
SHOPIFY_BASE = "https://www.dotandkey.com"
RATE_LIMIT_S = 0.5


def fetch_all_shopify_products() -> list[dict]:
    products = []
    page = 1
    with httpx.Client(timeout=15.0) as client:
        while True:
            url = f"{SHOPIFY_BASE}/products.json?limit=250&page={page}"
            resp = client.get(url, headers={"Accept": "application/json"})
            resp.raise_for_status()
            batch = resp.json().get("products", [])
            if not batch:
                break
            products.extend(batch)
            print(f"  page {page}: {len(batch)} products (total {len(products)})")
            if len(batch) < 250:
                break
            page += 1
            time.sleep(RATE_LIMIT_S)
    return products


def load_existing_skus() -> set[str]:
    skus: set[str] = set()
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sku = row["SKU"].strip()
            if sku:
                skus.add(sku)
    return skus


def shopify_product_to_rows(p: dict) -> list[dict]:
    """Convert one Shopify product dict to one or more CSV row dicts (one per variant)."""
    title = p.get("title", "")
    product_type = p.get("product_type", "")
    tags = ", ".join(p.get("tags", []))
    body = html.unescape(p.get("body_html", "") or "")
    # Strip HTML tags from description
    import re
    body_clean = re.sub(r"<[^>]+>", " ", body)
    body_clean = re.sub(r"\s+", " ", body_clean).strip()

    rows = []
    variants = p.get("variants", [{}])
    for v in variants:
        sku = (v.get("sku") or "").strip()
        if not sku or sku.count("DK_") > 1:
            continue
        variant_title = v.get("title", "Default Title")
        price = v.get("price", "")
        compare_at = v.get("compare_at_price", "") or price
        rows.append({
            "SKU": sku,
            "Title": title,
            "Variant": variant_title,
            "Type": product_type,
            "Tags (Features/Applications)": tags,
            "Price": price,
            "Compare At Price": compare_at,
            "Description (Use/Features)": body_clean[:2000],  # cap to avoid huge cells
        })
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ingest", action="store_true",
                        help="Re-run csv_to_graph.py and fetch_product_media.py after update")
    args = parser.parse_args()

    print("Fetching live Shopify catalog ...")
    shopify_products = fetch_all_shopify_products()
    print(f"  {len(shopify_products)} products fetched from Shopify")

    print("Loading existing CSV SKUs ...")
    existing_skus = load_existing_skus()
    print(f"  {len(existing_skus)} SKUs in current CSV")

    # Find new rows
    new_rows: list[dict] = []
    new_sku_set: set[str] = set()
    for p in shopify_products:
        for row in shopify_product_to_rows(p):
            sku = row["SKU"]
            if sku not in existing_skus and sku not in new_sku_set:
                new_rows.append(row)
                new_sku_set.add(sku)

    if not new_rows:
        print("\nNo new products found — CSV is up to date.")
        return

    print(f"\n{len(new_rows)} new product rows found:")
    for r in new_rows:
        print(f"  {r['SKU']:20s}  {r['Title'][:60]}  ₹{r['Price']}")

    if args.dry_run:
        print("\n[dry-run] Not writing to CSV.")
        return

    # Append to CSV
    fieldnames = ["SKU", "Title", "Variant", "Type",
                  "Tags (Features/Applications)", "Price",
                  "Compare At Price", "Description (Use/Features)"]
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writerows(new_rows)
    print(f"\nAppended {len(new_rows)} rows to {CSV_PATH.name}")

    if args.ingest:
        print("\nRe-ingesting into FalkorDB ...")
        import subprocess
        result = subprocess.run(
            [sys.executable, "scripts/csv_to_graph.py"],
            cwd=ROOT, capture_output=False
        )
        if result.returncode != 0:
            print("csv_to_graph.py failed — stopping.", file=sys.stderr)
            sys.exit(1)

        print("\nFetching product media for new SKUs ...")
        subprocess.run(
            [sys.executable, "scripts/fetch_product_media.py"],
            cwd=ROOT, capture_output=False
        )
        print("\nDone. Run the full graph pipeline if you want capability scores updated:")
        print("  python3 scripts/build_graph_pipeline.py")


if __name__ == "__main__":
    main()
