#!/usr/bin/env python3
"""
Fetch product image URLs and clean product URLs from the Dot & Key Shopify store
for all products in the FalkorDB graph, then save to data/product_media.json.

Usage:
    python3 scripts/fetch_product_media.py
"""

import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode, quote

import httpx
import falkordb

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
GRAPH_HOST = os.getenv("FALKORDB_HOST", "localhost")
GRAPH_PORT = int(os.getenv("FALKORDB_PORT", "6379"))
GRAPH_NAME = os.getenv("FALKORDB_GRAPH", "dotandkey")
SHOPIFY_SUGGEST_URL = "https://www.dotandkey.com/search/suggest.json"
RATE_LIMIT_S = 0.3
OUTPUT_PATH = Path(__file__).parent.parent / "data" / "product_media.json"

# Detect non-shade variant titles: sizes (50g, 100ml) and count units (30 Pairs).
_NON_SHADE_RE = re.compile(
    r"\d+(\.\d+)?\s*(ml|g|gm|mg|l|pairs?|pcs?|pack|sheets?|tablets?|ct)\b",
    re.IGNORECASE,
)


def is_shade(title: str) -> bool:
    """Return True when a variant title names a colour/tint, not a size or count."""
    if not title or title in ("Default Title", ""):
        return False
    return not _NON_SHADE_RE.search(title)


# Stop-words to skip when building the search query from the title
_STOP = {
    "with", "and", "for", "the", "a", "an", "of", "to", "in", "on", "by",
    "from", "that", "this", "is", "it", "all", "your", "&",
}


def title_to_query(title: str, n_words: int = 5) -> str:
    """Return the first n_words meaningful words from a product title."""
    words = [w for w in title.split() if w.lower() not in _STOP]
    return " ".join(words[:n_words])


def title_similarity(a: str, b: str) -> float:
    """
    Very cheap title similarity: fraction of lowercased words in `a`
    that appear in `b`. Used only to pick the best of 2-3 Shopify results.
    """
    a_words = set(re.findall(r"[a-z0-9]+", a.lower()))
    b_words = set(re.findall(r"[a-z0-9]+", b.lower()))
    if not a_words:
        return 0.0
    return len(a_words & b_words) / len(a_words)


def clean_url(raw_url: str) -> tuple[str, str]:
    """
    Given a Shopify product URL (may include /products/handle?variant=...),
    return (handle, product_url) where product_url = /products/{handle}.
    """
    # raw_url may be relative (/products/...) or absolute
    path = urlparse(raw_url).path  # always /products/handle
    handle = path.removeprefix("/products/")
    return handle, f"/products/{handle}"


def make_image_url(raw_image: str) -> str:
    """
    Append ?width=400 (or &width=400) to the CDN image URL for an
    optimised size. If width is already set, leave it alone.
    """
    if not raw_image:
        return raw_image
    parsed = urlparse(raw_image)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    if "width" not in qs:
        sep = "&" if parsed.query else "?"
        return raw_image + sep + "width=400"
    return raw_image


# ---------------------------------------------------------------------------
# FalkorDB: fetch all (sku, title) pairs
# ---------------------------------------------------------------------------
def get_all_products() -> list[dict]:
    """Return list of {sku, title} dicts from the graph."""
    db = falkordb.FalkorDB(host=GRAPH_HOST, port=GRAPH_PORT)
    g = db.select_graph(GRAPH_NAME)
    result = g.query("MATCH (p:Product) RETURN p.sku AS sku, p.title AS title")
    rows = []
    for record in result.result_set:
        sku, title = record[0], record[1]
        if sku and title:
            rows.append({"sku": sku, "title": title})
    print(f"[graph] Found {len(rows)} product nodes.")
    return rows


# ---------------------------------------------------------------------------
# Shopify predictive search
# ---------------------------------------------------------------------------
def search_shopify(query: str, client: httpx.Client) -> list[dict]:
    """
    Call Shopify's predictive search endpoint and return the product list.
    Each item has at minimum: title, url, image (may be None).
    Returns [] on any error.
    """
    params = {
        "q": query,
        "resources[type]": "product",
        "resources[limit]": "3",
    }
    try:
        resp = client.get(SHOPIFY_SUGGEST_URL, params=params, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        products = (
            data.get("resources", {})
                .get("results", {})
                .get("products", [])
        )
        return products
    except Exception as exc:
        print(f"  [warn] Search failed for '{query}': {exc}")
        return []


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    products = get_all_products()

    # Group SKUs by title so we only hit Shopify once per distinct title
    title_to_skus: dict[str, list[str]] = {}
    for p in products:
        title_to_skus.setdefault(p["title"], []).append(p["sku"])

    unique_titles = list(title_to_skus.keys())
    print(f"[info] {len(unique_titles)} unique titles to look up.")

    # Seed from existing file so manually-patched entries survive a re-run
    # when Shopify's suggest API can't find them (title mismatch, renamed products).
    existing: dict[str, dict] = {}
    if OUTPUT_PATH.exists():
        existing = json.loads(OUTPUT_PATH.read_text())

    media_map: dict[str, dict] = {}
    found = 0
    missed = 0

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; DotKeyAdvisorBot/1.0; "
            "+https://github.com/dotandkey-advisor)"
        ),
        "Accept": "application/json",
    }

    with httpx.Client(headers=headers, follow_redirects=True) as client:
        for idx, title in enumerate(unique_titles, 1):
            query = title_to_query(title)
            print(f"[{idx:3d}/{len(unique_titles)}] '{title[:55]}' → query='{query}'")

            candidates = search_shopify(query, client)

            best = None
            best_score = -1.0
            for candidate in candidates:
                score = title_similarity(title, candidate.get("title", ""))
                if score > best_score:
                    best_score = score
                    best = candidate

            # Retry with a shorter 3-word query when the default 5-word query
            # returns no candidates (often happens with long parenthetical titles).
            if (best is None or best_score < 0.2) and len(query.split()) > 3:
                short_query = title_to_query(title, n_words=3)
                retry_candidates = search_shopify(short_query, client)
                for candidate in retry_candidates:
                    score = title_similarity(title, candidate.get("title", ""))
                    if score > best_score:
                        best_score = score
                        best = candidate

            if best is None or best_score < 0.2:
                # Fall back to the existing entry so manual patches survive re-runs.
                for sku in title_to_skus[title]:
                    if sku in existing:
                        media_map[sku] = dict(existing[sku])
                        print(f"         ✗ no match — kept existing entry for {sku}")
                    else:
                        print(f"         ✗ no match — skipped {sku} (no existing entry)")
                missed += 1
            else:
                raw_url = best.get("url", "")
                raw_image = best.get("image", "") or ""
                handle, product_url = clean_url(raw_url)
                image_url = make_image_url(raw_image)

                # Fetch /products/{handle}.js — the storefront endpoint that
                # exposes variant.featured_image.src (actual per-shade packshot)
                # AND variant.available (live stock status) in a single call.
                # The .json endpoint lacks `available` and uses an image-id
                # indirection that the .js endpoint makes unnecessary.
                variant_image_map: dict[str, str] = {}
                shade_map: dict[str, str] = {}
                available_map: dict[str, bool] = {}
                try:
                    js_resp = client.get(
                        f"https://www.dotandkey.com/products/{handle}.js",
                        timeout=10.0,
                    )
                    if js_resp.status_code == 200:
                        js_data = js_resp.json()
                        # images[] is a list of URL strings in the .js format
                        raw_first = (js_data.get("images") or [""])[0] or ""
                        first_img = re.sub(r"^//", "https://", str(raw_first))
                        for v in js_data.get("variants", []):
                            v_sku = (v.get("sku") or "").strip()
                            if not v_sku:
                                continue
                            if v.get("available") is not None:
                                available_map[v_sku] = bool(v["available"])
                            fi = v.get("featured_image") or {}
                            raw_src = fi.get("src") or first_img
                            if raw_src:
                                src = re.sub(r"^//", "https://", str(raw_src))
                                variant_image_map[v_sku] = make_image_url(src)
                            v_title = (v.get("title") or "").strip()
                            if is_shade(v_title):
                                shade_map[v_sku] = v_title
                except Exception:
                    pass  # fall back to suggest image / no shade / no availability
                time.sleep(RATE_LIMIT_S)

                for sku in title_to_skus[title]:
                    media_map[sku] = {
                        "handle": handle,
                        "image_url": variant_image_map.get(sku, image_url),
                        "product_url": product_url,
                        "shade": shade_map.get(sku, ""),
                        "available": available_map.get(sku, True),
                    }
                found += 1
                print(
                    f"         ✓ handle='{handle}' score={best_score:.2f} "
                    f"skus={title_to_skus[title]}"
                )

            time.sleep(RATE_LIMIT_S)

    # Build siblings: for each handle, which SKUs share it (= other shades/sizes).
    # Stored on each entry so the widget / backend can do cross-shade navigation
    # without an extra lookup.
    handle_to_skus: dict[str, list[str]] = {}
    for sku, info in media_map.items():
        handle_to_skus.setdefault(info["handle"], []).append(sku)
    for sku, info in media_map.items():
        info["siblings"] = sorted(handle_to_skus[info["handle"]])

    with open(OUTPUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(media_map, fh, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60)
    print(f"Done. Found: {found}, Missed: {missed}")
    print(f"Total SKUs written: {len(media_map)}")
    print(f"Output: {OUTPUT_PATH}")

    # Print first 5 entries
    print("\nFirst 5 entries:")
    for i, (sku, info) in enumerate(list(media_map.items())[:5]):
        print(f"  {sku}: {json.dumps(info, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
