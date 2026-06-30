"""
scripts/build_graph_pipeline.py

Master pipeline — builds the full knowledge graph from scratch.
Run after csv_to_graph.py has populated Product nodes.

Steps:
  1. Ingredient knowledge (ingredient→concern + ingredient→capability edges)
  2. Ingredient synergy (SYNERGIZES_WITH edges)
  3. Product type ontology (ProductType nodes + routine relationship edges)
  4. Capability scores (cap_* properties on all Product nodes)
  5. Product-product relations (comparative edges within each category)

Usage:
    python3 scripts/build_graph_pipeline.py [--dry-run] [--graph dotandkey]
    python3 scripts/build_graph_pipeline.py --step capability_scores  # one step only
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def step(name: str, fn, args):
    print(f"\n{'='*60}")
    print(f"  STEP: {name}")
    print(f"{'='*60}")
    t0 = time.time()
    fn(args)
    elapsed = time.time() - t0
    print(f"\n  ✓ {name} complete ({elapsed:.1f}s)")


def run_ingredient_knowledge(args):
    from scripts.build_ingredient_knowledge import run
    run(args)


def run_synergy_graph(args):
    from scripts.build_synergy_graph import run
    run(args)


def run_product_types(args):
    from scripts.build_product_types import run
    run(args)


def run_capability_scores(args):
    from scripts.generate_capability_scores import run
    run(args)


def run_product_relations(args):
    from scripts.generate_product_relations import run
    run(args)


def run_ingredient_roles(args):
    from scripts.backfill_ingredient_roles import run
    run(args)


def run_product_dna(args):
    from scripts.generate_product_dna import run
    run(args)


def run_differentiation(args):
    from scripts.generate_differentiation import run
    run(args)


def run_product_identity(args):
    from scripts.generate_product_identity import run
    run(args)


ALL_STEPS = [
    ("ingredient_knowledge", run_ingredient_knowledge),
    ("synergy_graph",        run_synergy_graph),
    ("product_types",        run_product_types),
    ("ingredient_roles",     run_ingredient_roles),
    ("capability_scores",    run_capability_scores),
    ("product_dna",          run_product_dna),
    ("differentiation",      run_differentiation),
    ("product_identity",     run_product_identity),
    ("product_relations",    run_product_relations),
]


def main():
    parser = argparse.ArgumentParser(
        description="Build the Dot & Key knowledge graph pipeline"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be done without writing to DB")
    parser.add_argument("--host",  default="localhost")
    parser.add_argument("--port",  type=int, default=6379)
    parser.add_argument("--graph", default="dotandkey")
    parser.add_argument("--step",  default=None,
                        choices=[s[0] for s in ALL_STEPS],
                        help="Run only this step (default: all)")
    parser.add_argument("--category", default=None,
                        help="Limit product_relations step to one category")
    args = parser.parse_args()

    t_start = time.time()
    print(f"\nDot & Key Knowledge Graph Pipeline")
    print(f"Graph:   {args.graph} @ {args.host}:{args.port}")
    print(f"Dry run: {args.dry_run}")

    steps_to_run = ALL_STEPS if not args.step else [
        s for s in ALL_STEPS if s[0] == args.step
    ]

    for name, fn in steps_to_run:
        step(name, fn, args)

    total = time.time() - t_start
    print(f"\n{'='*60}")
    print(f"  Pipeline complete in {total:.1f}s")
    print(f"  Steps run: {[s[0] for s, _ in [(s, None) for s in steps_to_run]]}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
