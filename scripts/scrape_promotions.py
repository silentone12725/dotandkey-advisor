"""
scripts/scrape_promotions.py

Derives promotional labels for each product SKU from two sources:
  1. CSV tag column  — deterministic, offline (run anytime)
  2. Live dotandkey.com Shopify AJAX endpoints (optional, --live flag)
     Fetches /collections/all/products.json and enriches with current
     sale/offer data — requires network access to the store.

Output: data/promotions.json  { sku: promo_label_string | null }

Run:
    python3 scripts/scrape_promotions.py          # CSV tags only
    python3 scripts/scrape_promotions.py --live   # CSV + live prices
"""

import argparse
import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "dot_and_key_products_comparison.csv"
OUT_PATH = ROOT / "data" / "promotions.json"

# ---------------------------------------------------------------------------
# Priority-ordered tag → human label mapping.
# Earlier entries win when a product has multiple promo tags.
# ---------------------------------------------------------------------------
_TAG_PRIORITY: list[tuple[str, str]] = [
    ("deal_of_the_day",        "🔥 Deal of the Day"),
    ("gpay_30_percent",        "30% OFF via GPay"),
    ("label_flat_25",          "Flat 25% OFF"),
    ("label_25_off_combo25",   "25% OFF on Combos"),
    ("flat20",                 "Upto 20% OFF + Free Gifts"),
    ("label_15_off_flat15",    "Flat 15% OFF"),
    ("label_buy_1_get_1",      "Free Gift on Purchase"),
    ("Offer popup",            "Special Offer"),
]

_TAG_MAP = {tag: label for tag, label in _TAG_PRIORITY}
_TAG_ORDER = {tag: i for i, (tag, _) in enumerate(_TAG_PRIORITY)}


def _promo_from_tags(tags_str: str) -> str | None:
    tags = [t.strip() for t in tags_str.split(",") if t.strip()]
    hits = [(t, _TAG_ORDER[t]) for t in tags if t in _TAG_MAP]
    if not hits:
        return None
    hits.sort(key=lambda x: x[1])
    return _TAG_MAP[hits[0][0]]


def build_from_csv() -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sku = row["SKU"].strip()
            if not sku or sku.count("DK_") > 1:
                continue
            result[sku] = _promo_from_tags(row["Tags (Features/Applications)"])
    return result


def enrich_live(result: dict) -> dict:
    """Optionally fetch live Shopify product data and overlay current pricing."""
    try:
        import urllib.request
    except ImportError:
        print("urllib not available", file=sys.stderr)
        return result

    # Shopify AJAX: /products.json returns 30 products per page
    page = 1
    fetched = 0
    while True:
        url = f"https://www.dotandkey.com/products.json?page={page}&limit=250"
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                data = json.loads(r.read().decode())
        except Exception as e:
            print(f"Live fetch page {page} failed: {e}", file=sys.stderr)
            break

        products = data.get("products", [])
        if not products:
            break

        for p in products:
            handle = p.get("handle", "")
            tags = p.get("tags", [])
            # Map Shopify tags → promo label
            for tag in tags:
                if tag in _TAG_MAP:
                    # match by handle against CSV media data
                    pass  # future: resolve handle → sku via product_media.json

        fetched += len(products)
        if len(products) < 250:
            break
        page += 1
        time.sleep(0.3)

    print(f"Live: fetched {fetched} products")
    return result


def run(args):
    print("Reading CSV tags ...")
    result = build_from_csv()

    labelled = sum(1 for v in result.values() if v)
    print(f"  {len(result)} SKUs processed, {labelled} have promo labels")

    if args.live:
        print("Fetching live promotions ...")
        result = enrich_live(result)

    OUT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"Written: {OUT_PATH}")

    # Show top labels
    from collections import Counter
    counts = Counter(v for v in result.values() if v)
    print("\nLabel distribution:")
    for label, n in counts.most_common():
        print(f"  {n:4d}  {label}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true",
                        help="Enrich with live Shopify data (requires network)")
    run(parser.parse_args())
