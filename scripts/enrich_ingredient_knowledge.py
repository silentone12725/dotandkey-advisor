#!/usr/bin/env python3
"""
scripts/enrich_ingredient_knowledge.py

Enrich Ingredient nodes in the graph with:
  1. skincare-database.com scrape  — categories, mechanism description
  2. Hardcoded expert mapping      — concern edges for ingredients not on skincare-db
  3. CosIng EU CSV (optional)      — inci_name, cas_number, function[]

Conflict policy:
  - Properties:  ON CREATE SET only — existing product-page data wins.
  - Edges:       MERGE with source property — additive, never removed.
                 Source values: "skincare_db", "cosing", "expert"

Usage:
    python3 -m scripts.enrich_ingredient_knowledge
    python3 -m scripts.enrich_ingredient_knowledge --cosing /path/to/cosing.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = "Mozilla/5.0 (compatible; DotKeyAdvisorEnricher/1.0)"

# ── Concern taxonomy (matches graph Concern node names) ──────────────────────

# skincare-database.com category → graph concern names
_CATEGORY_TO_CONCERNS: dict[str, list[str]] = {
    "barrier repair":  ["dryness", "redness_irritation"],
    "brightening":     ["dark_spots", "dullness", "pigmentation", "tanning"],
    "acne":            ["acne", "clogged_pores", "excess_oil"],
    "anti-aging":      ["ageing"],
    "hydration":       ["dryness"],
    "exfoliation":     ["dark_spots", "dullness", "ageing", "clogged_pores"],
    "sun protection":  ["tanning", "ageing"],
    "antioxidant":     ["ageing", "dullness"],
    "soothing":        ["redness_irritation"],
}

# Ingredient node name → skincare-database.com URL slug
_SLUG_MAP: dict[str, str] = {
    "argan_oil":    "argan-oil",
    "ceramides":    "ceramides",
    "cica":         "centella-asiatica",
    "glycolic":     "glycolic-acid",
    "hyaluronic":   "hyaluronic-acid",
    "kojic_acid":   "kojic-acid",
    "niacinamide":  "niacinamide",
    "retinol":      "retinol",
    "salicylic":    "salicylic-acid",
    "vitamin_c":    "vitamin-c",
    "zinc_oxide":   "zinc-oxide",
}

# Expert mapping for ingredients NOT on skincare-database.com
# (fruit extracts, brand-specific names)
# Official INCI names, CAS numbers, and EU CosIng function strings for
# the skincare-db ingredients (pure compounds with well-known identifiers).
_INCI_DATA: dict[str, dict] = {
    "niacinamide": {
        "inci_name":       "Niacinamide",
        "cas_number":      "98-92-0",
        "cosing_function": "SKIN CONDITIONING, VISCOSITY CONTROLLING",
    },
    "salicylic": {
        "inci_name":       "Salicylic Acid",
        "cas_number":      "69-72-7",
        "cosing_function": "ANTI-ACNE, ANTIMICROBIAL, ANTISEBORRHOEIC, EXFOLIANT, KERATOLYTIC, PRESERVATIVE, SKIN CONDITIONING",
    },
    "retinol": {
        "inci_name":       "Retinol",
        "cas_number":      "68-26-8",
        "cosing_function": "SKIN CONDITIONING",
    },
    "vitamin_c": {
        "inci_name":       "Ascorbic Acid",
        "cas_number":      "50-81-7",
        "cosing_function": "ANTIOXIDANT, SKIN CONDITIONING",
    },
    "glycolic": {
        "inci_name":       "Glycolic Acid",
        "cas_number":      "79-14-1",
        "cosing_function": "BUFFERING, EXFOLIANT, SKIN CONDITIONING",
    },
    "kojic_acid": {
        "inci_name":       "Kojic Acid",
        "cas_number":      "501-30-4",
        "cosing_function": "ANTIOXIDANT, SKIN CONDITIONING",
    },
    "zinc_oxide": {
        "inci_name":       "Zinc Oxide",
        "cas_number":      "1314-13-2",
        "cosing_function": "OPACIFYING, SKIN CONDITIONING, SKIN PROTECTING, UV ABSORBER, UV FILTER",
    },
    "ceramides": {
        "inci_name":       "Ceramide NP",
        "cas_number":      "100403-19-8",
        "cosing_function": "SKIN CONDITIONING",
    },
    "hyaluronic": {
        "inci_name":       "Sodium Hyaluronate",
        "cas_number":      "9067-32-7",
        "cosing_function": "FILM FORMING, MOISTURISING, SKIN CONDITIONING",
    },
    "argan_oil": {
        "inci_name":       "Argania Spinosa Kernel Oil",
        "cas_number":      "223748-44-5",
        "cosing_function": "EMOLLIENT, HAIR CONDITIONING, SKIN CONDITIONING",
    },
    "cica": {
        "inci_name":       "Centella Asiatica Extract",
        "cas_number":      "84696-21-9",
        "cosing_function": "SKIN CONDITIONING, SOOTHING",
    },
}

_EXPERT_MAPPING: dict[str, dict] = {
    "blood_orange": {
        "inci_name":   "Citrus Sinensis Fruit Extract",
        "function":    "Antioxidant, Brightening",
        "concerns":    ["dark_spots", "dullness", "pigmentation"],
    },
    "blueberry": {
        "inci_name":   "Vaccinium Angustifolium Fruit Extract",
        "function":    "Antioxidant",
        "concerns":    ["ageing", "dullness"],
    },
    "dragon_fruit": {
        "inci_name":   "Hylocereus Undatus Fruit Extract",
        "function":    "Antioxidant, Brightening",
        "concerns":    ["dullness"],
    },
    "lime": {
        "inci_name":   "Citrus Aurantifolia Fruit Extract",
        "function":    "Brightening, Exfoliant",
        "concerns":    ["dullness", "dark_spots", "pigmentation"],
    },
    "liquid_ice": {
        "inci_name":   "Cooling Complex",
        "function":    "Cooling, Soothing",
        "concerns":    ["redness_irritation", "excess_oil"],
    },
    "mango": {
        "inci_name":   "Mangifera Indica Fruit Extract",
        "function":    "Emollient, Antioxidant",
        "concerns":    ["dryness"],
    },
    "pomegranate": {
        "inci_name":   "Punica Granatum Extract",
        "function":    "Antioxidant",
        "concerns":    ["ageing", "dullness"],
    },
    "ricewater": {
        "inci_name":   "Oryza Sativa (Rice) Bran Water",
        "function":    "Brightening, Soothing",
        "concerns":    ["dullness", "dark_spots"],
    },
    "strawberry": {
        "inci_name":   "Fragaria Vesca Fruit Extract",
        "function":    "Brightening, Antioxidant",
        "concerns":    ["dullness", "dark_spots"],
    },
    "watermelon": {
        "inci_name":   "Citrullus Lanatus Fruit Extract",
        "function":    "Humectant, Antioxidant",
        "concerns":    ["dryness", "dullness"],
    },
}


# ── skincare-database.com scraper ────────────────────────────────────────────

def _scrape_ingredient(slug: str) -> dict[str, Any] | None:
    url = f"https://www.skincare-database.com/ingredients/{slug}"
    try:
        resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=15.0, follow_redirects=True)
        if resp.status_code != 200:
            return None
        html = resp.text
    except Exception:
        return None

    # Strip scripts/styles, get plain text
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    # Extract category tags (appear immediately after ingredient name before description)
    # Pattern: ingredient name followed by category words before any sentence
    cat_candidates: list[str] = []
    for cat in _CATEGORY_TO_CONCERNS:
        if cat in text.lower()[:500]:
            cat_candidates.append(cat)

    # Extract first substantial sentence as mechanism description (skip nav/breadcrumb)
    desc = ""
    # Find first sentence that starts with capital and is at least 80 chars
    for m in re.finditer(r"([A-Z][^.!?]{80,400}[.!?])", text):
        candidate = m.group(1).strip()
        # Skip navigation-like lines
        if any(skip in candidate.lower() for skip in
               ["skincare database", "ingredients", "skin types", "guide", "about"]):
            continue
        desc = candidate
        break

    concerns: list[str] = []
    for cat in cat_candidates:
        for concern in _CATEGORY_TO_CONCERNS[cat]:
            if concern not in concerns:
                concerns.append(concern)

    if not concerns and not desc:
        return None

    return {
        "categories":  cat_candidates,
        "description": desc,
        "concerns":    concerns,
        "source_url":  url,
    }


# ── CosIng CSV loader ─────────────────────────────────────────────────────────

def load_cosing(csv_path: str) -> dict[str, dict]:
    """Load CosIng CSV keyed by lowercase INCI name."""
    entries: dict[str, dict] = {}
    try:
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                name = (row.get("INCI name") or row.get("Name") or "").strip().lower()
                if not name:
                    continue
                entries[name] = {
                    "inci_name":   row.get("INCI name", "").strip(),
                    "cas_number":  row.get("CAS No", "").strip(),
                    "function":    row.get("Functions", "").strip(),
                    "restriction": row.get("Restriction", "").strip(),
                }
    except Exception as e:
        print(f"[cosing] failed to load {csv_path}: {e}")
    return entries


# ── Graph writes ──────────────────────────────────────────────────────────────

def _merge_ingredient_props(graph: Any, name: str, props: dict) -> None:
    """Set ingredient properties only if they don't already exist (non-destructive)."""
    sets = []
    params: dict = {"name": name}
    for key, val in props.items():
        if val:
            sets.append(f"i.{key} = CASE WHEN i.{key} IS NULL OR i.{key} = '' THEN ${key} ELSE i.{key} END")
            params[key] = val
    if sets:
        graph.query(
            f"MATCH (i:Ingredient {{name: $name}}) SET {', '.join(sets)}",
            params,
        )


def _add_concern_edge(graph: Any, ingredient_name: str, concern_name: str, source: str) -> None:
    """MERGE an ADDRESSES edge with source tag — idempotent, never overwrites."""
    graph.query(
        "MATCH (i:Ingredient {name: $iname}) "
        "MATCH (c:Concern {name: $cname}) "
        "MERGE (i)-[r:ADDRESSES]->(c) "
        "ON CREATE SET r.source = $source, r.added_at = timestamp()",
        {"iname": ingredient_name, "cname": concern_name, "source": source},
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def run(args) -> None:
    from falkordb import FalkorDB

    graph = FalkorDB(host=args.host, port=args.port).select_graph(args.graph)

    # Load existing ingredient names
    rows = graph.query("MATCH (i:Ingredient) RETURN i.name").result_set
    ingredient_names: list[str] = [r[0] for r in rows if r[0]]
    print(f"[enrich_ingredients] {len(ingredient_names)} ingredient nodes found")

    # Load existing concern names
    concern_rows = graph.query("MATCH (c:Concern) RETURN c.name").result_set
    valid_concerns: set[str] = {r[0] for r in concern_rows if r[0]}
    print(f"[enrich_ingredients] {len(valid_concerns)} concern nodes found: {sorted(valid_concerns)}")

    # ── CosIng lookup (optional) ───────────────────────────────────────────
    cosing_data: dict[str, dict] = {}
    if args.cosing and Path(args.cosing).exists():
        cosing_data = load_cosing(args.cosing)
        print(f"[cosing] loaded {len(cosing_data)} entries")

    # ── skincare-database.com scrape ───────────────────────────────────────
    sdb_results: dict[str, dict] = {}
    for iname, slug in _SLUG_MAP.items():
        if iname not in ingredient_names:
            continue
        print(f"  [skincare-db] scraping {iname} ({slug}) ...", end=" ", flush=True)
        result = _scrape_ingredient(slug)
        if result:
            sdb_results[iname] = result
            print(f"→ categories={result['categories']}, concerns={result['concerns']}")
        else:
            print("→ no data")
        time.sleep(0.5)

    # ── Apply skincare-db data ────────────────────────────────────────────
    edges_added = 0
    props_updated = 0

    for iname, data in sdb_results.items():
        # Properties (description only — inci_name comes from expert/cosing)
        if data.get("description"):
            _merge_ingredient_props(graph, iname, {
                "mechanism_description": data["description"],
                "skincare_db_url": data["source_url"],
                "skincare_db_categories": ", ".join(data["categories"]),
            })
            props_updated += 1

        # ADDRESSES edges
        for concern in data["concerns"]:
            if concern not in valid_concerns:
                continue
            _add_concern_edge(graph, iname, concern, "skincare_db")
            edges_added += 1

    # ── Apply expert mapping (for ingredients not on skincare-db) ─────────
    for iname, data in _EXPERT_MAPPING.items():
        if iname not in ingredient_names:
            continue
        _merge_ingredient_props(graph, iname, {
            "inci_name": data.get("inci_name", ""),
            "function":  data.get("function", ""),
        })
        props_updated += 1

        for concern in data.get("concerns", []):
            if concern not in valid_concerns:
                continue
            _add_concern_edge(graph, iname, concern, "expert")
            edges_added += 1

    # ── Apply INCI data (CAS, official function strings) ──────────────────
    for iname, data in _INCI_DATA.items():
        if iname not in ingredient_names:
            continue
        _merge_ingredient_props(graph, iname, {
            "inci_name":       data.get("inci_name", ""),
            "cas_number":      data.get("cas_number", ""),
            "cosing_function": data.get("cosing_function", ""),
        })
        props_updated += 1

    # ── Apply CosIng data ─────────────────────────────────────────────────
    # Map our slug names → possible INCI names to look up
    _INCI_LOOKUP: dict[str, str] = {
        "niacinamide":  "niacinamide",
        "hyaluronic":   "hyaluronic acid",
        "salicylic":    "salicylic acid",
        "retinol":      "retinol",
        "vitamin_c":    "ascorbic acid",
        "glycolic":     "glycolic acid",
        "ceramides":    "ceramide np",
        "zinc_oxide":   "zinc oxide",
        "kojic_acid":   "kojic acid",
        "argan_oil":    "argania spinosa kernel oil",
        "cica":         "centella asiatica extract",
    }
    for iname, inci_key in _INCI_LOOKUP.items():
        if iname not in ingredient_names:
            continue
        entry = cosing_data.get(inci_key.lower())
        if not entry:
            continue
        _merge_ingredient_props(graph, iname, {
            "inci_name":       entry["inci_name"],
            "cas_number":      entry["cas_number"],
            "cosing_function": entry["function"],
            "restriction":     entry["restriction"],
        })
        props_updated += 1
        print(f"  [cosing] enriched {iname}: {entry['function'][:60]}")

    print(
        f"\n[enrich_ingredients] done — "
        f"{props_updated} property updates, {edges_added} ADDRESSES edges added"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enrich Ingredient nodes with dermatological data")
    parser.add_argument("--host",   default="localhost")
    parser.add_argument("--port",   type=int, default=6379)
    parser.add_argument("--graph",  default="dotandkey")
    parser.add_argument("--cosing", default="", help="Path to CosIng CSV (optional)")
    run(parser.parse_args())
