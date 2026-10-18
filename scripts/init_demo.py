"""
scripts/init_demo.py

Idempotent one-shot graph seeding for demo / first-run Docker deployment.

Run order:
  1. csv_to_graph           — MERGE product nodes + apply existing product_media.json
  2. fetch_product_media    — pull image URLs / availability from Shopify
                             (skipped when data/product_media.json already present)
  3. media re-apply         — patch nodes with freshly fetched URLs (only when step 2 ran)
  4. build_graph_pipeline   — LLM enrichment
                             (skipped when cap_* scores already present on >20 products)
  5. enrich_product_graph   — page scrape for ingredients_text, FAQ, Claim nodes
                             (skipped when >20 products already have enriched_from_page=true)
  6. enrich_ingredient_knowledge — dermatological concern mapping + skincare-db scrape
                             (skipped when ADDRESSES edges already exist)

Usage (invoked by Docker Compose graph-init service):
    python3 -m scripts.init_demo --host falkordb
    python3 -m scripts.init_demo                 # defaults to localhost
"""

import argparse
import json
import os
import time
from pathlib import Path


def _get_graph(args):
    from falkordb import FalkorDB
    return FalkorDB(host=args.host, port=args.port).select_graph(args.graph)


def _count(graph, query):
    return graph.query(query).result_set[0][0]


def run(args):
    t0 = time.time()
    print("\n=== Dot & Key demo graph init ===\n")

    # ── 1. csv_to_graph — idempotent MERGE, always safe to re-run ─────────────
    print("[1/4] csv_to_graph ...")
    from scripts.csv_to_graph import run as csv_run
    csv_run(argparse.Namespace(
        dry_run=False, host=args.host, port=args.port, graph=args.graph,
    ))

    # ── 2. fetch_product_media — intentionally refresh when requested ─────────
    media_path = Path("data/product_media.json")
    media_fetched = False
    should_refresh_media = getattr(args, "force_media_refresh", False)
    if media_path.exists() and media_path.stat().st_size > 10_000 and not should_refresh_media:
        print("[2/4] data/product_media.json present — skip Shopify fetch")
    else:
        print("[2/4] fetching product media from Shopify ...")
        # Module-level constants in fetch_product_media read from env at import time;
        # set before importing so the in-process call uses the right host.
        os.environ["FALKORDB_HOST"] = args.host
        os.environ["FALKORDB_PORT"] = str(args.port)
        os.environ["FALKORDB_GRAPH"] = args.graph
        from scripts.fetch_product_media import main as fetch_main
        fetch_main()
        media_fetched = True

    # ── 3. apply freshly fetched media back to graph ──────────────────────────
    if media_fetched and media_path.exists():
        print("[3/4] applying freshly fetched media to graph ...")
        graph = _get_graph(args)
        media = json.loads(media_path.read_text())
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
        print(f"  Applied media for {len(media)} SKUs")
    else:
        print("[3/4] media already applied by csv_to_graph — skip")

    # ── 4. build_graph_pipeline — skip if enrichment already done ─────────────
    graph = _get_graph(args)
    n_enriched = _count(
        graph,
        "MATCH (p:Product) WHERE p.cap_brightening IS NOT NULL RETURN count(p) AS n",
    )
    if n_enriched > 20:
        print(f"[4/4] {n_enriched} enriched products found — skip pipeline")
    else:
        print(f"[4/4] running full enrichment pipeline ({n_enriched} enriched so far) ...")
        from scripts.build_graph_pipeline import ALL_STEPS, step as log_step
        pipeline_args = argparse.Namespace(
            dry_run=False, host=args.host, port=args.port,
            graph=args.graph, step=None, category=None,
        )
        for name, fn in ALL_STEPS:
            log_step(name, fn, pipeline_args)

    # ── 5. enrich_product_graph — skip if already done ────────────────────────
    graph = _get_graph(args)
    n_enriched_pages = _count(
        graph,
        "MATCH (p:Product) WHERE p.enriched_from_page = true RETURN count(p) AS n",
    )
    if n_enriched_pages > 20:
        print(f"[5/5] {n_enriched_pages} page-enriched products found — skip")
    else:
        print(f"[5/5] running page enrichment ({n_enriched_pages} enriched so far) ...")
        from scripts.enrich_product_graph import run as enrich_run
        enrich_run(argparse.Namespace(
            host=args.host, port=args.port, graph=args.graph,
        ))

    # ── 6. enrich_ingredient_knowledge — skip if ADDRESSES edges already exist ─
    graph = _get_graph(args)
    n_addresses = _count(graph, "MATCH ()-[:ADDRESSES]->() RETURN count(*) AS n")
    if n_addresses > 5:
        print(f"[6/6] {n_addresses} ADDRESSES edges found — skip ingredient enrichment")
    else:
        print(f"[6/6] enriching ingredient nodes with dermatological data ...")
        from scripts.enrich_ingredient_knowledge import run as ing_run
        ing_run(argparse.Namespace(
            host=args.host, port=args.port, graph=args.graph, cosing="",
        ))

    print(f"\n=== init complete in {time.time() - t0:.0f}s ===\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed the Dot & Key knowledge graph")
    parser.add_argument("--host",  default=os.getenv("FALKORDB_HOST", "localhost"))
    parser.add_argument("--port",  type=int, default=int(os.getenv("FALKORDB_PORT", "6379")))
    parser.add_argument("--graph", default=os.getenv("FALKORDB_GRAPH", "dotandkey"))
    parser.add_argument("--force-media-refresh", action="store_true",
                        help="Always refresh product_media.json from Shopify, even if the file exists")
    run(parser.parse_args())
