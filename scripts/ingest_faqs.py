"""
scripts/ingest_faqs.py

Extracts FAQ Q&A pairs from Product description blobs and stores them as
FAQ nodes in FalkorDB, linked to their products via HAS_FAQ edges.

Description FAQ format (Dot & Key):
  "1. Can I use this daily? Ans: Yes, it is suitable for daily use.
   2. Will it dry my skin? Ans: No, it is gentle."

Also handles:
  - "Question? Answer: ..."
  - "Question? Ans: ..."
  - "Question? A: ..."
  - Un-numbered variants

MERGE key: first 120 chars of the question (case-insensitive normalised),
so the same question appearing across multiple products shares one FAQ node.

Usage:
    python3 scripts/ingest_faqs.py [--dry-run] [--host localhost] [--port 6379] [--graph dotandkey]
"""

import argparse
import re
from pathlib import Path



# ---------------------------------------------------------------------------
# FAQ extraction regex
# ---------------------------------------------------------------------------

# Matches numbered or un-numbered questions followed by Ans/Answer/A:
# Group 1: question text (including the trailing ?)
# Group 2: answer text (up to next numbered item or end of "block")
_FAQ_PATTERN = re.compile(
    r"""
    (?:(?:^|\n)\s*\d+\.\s*)?        # optional leading number "1. "
    ([A-Z][^?]{5,198}?\?)           # question: starts with capital, ends with ?
    \s*                              # optional whitespace / newline
    (?:Ans(?:wer)?|A)\s*[:\-]\s*   # "Ans:", "Answer:", "A:"
    (.*?)                            # answer text (non-greedy)
    (?=                              # lookahead: stop at next Q or end
        \n?\s*\d+\.\s*[A-Z]         # next numbered question
      | \n?\s*[A-Z][^?]{5,198}?\?  # next un-numbered question
      | $                            # end of string
    )
    """,
    re.VERBOSE | re.DOTALL,
)

# Simpler fallback: any sentence ending in "?" followed by Ans/Answer/A:
_FAQ_FALLBACK = re.compile(
    r"([A-Z][^\n?]{5,198}?\?)\s*(?:Ans(?:wer)?|A)\s*[:\-]\s*(.*?)(?=[A-Z][^\n?]{5,198}?\?|$)",
    re.DOTALL,
)

_MAX_Q_LEN = 200
_MAX_A_LEN = 500


def _clean(text: str) -> str:
    """Strip whitespace and normalise internal whitespace."""
    return re.sub(r"\s+", " ", text.strip())


def extract_faqs(description: str) -> list[dict]:
    """Return list of {question, answer, answer_short} dicts from a description blob."""
    if not description:
        return []

    pairs = []
    seen_qs: set[str] = set()

    for pattern in (_FAQ_PATTERN, _FAQ_FALLBACK):
        for m in pattern.finditer(description):
            q = _clean(m.group(1))
            a = _clean(m.group(2))

            # Strip leading numeric "2." artefacts that crept into the question
            q = re.sub(r"^\d+\.\s*", "", q)

            if not q or not a:
                continue
            if len(q) > _MAX_Q_LEN or len(a) > _MAX_A_LEN:
                continue
            # deduplicate within the same description
            q_key = q[:120].lower()
            if q_key in seen_qs:
                continue
            seen_qs.add(q_key)

            pairs.append(
                {
                    "question": q,
                    "answer": a,
                    "answer_short": a[:200],
                    "merge_key": q[:120],  # MERGE key used in Cypher
                }
            )

        if pairs:
            # First pattern succeeded — no need for fallback
            break

    return pairs


# ---------------------------------------------------------------------------
# Cypher queries
# ---------------------------------------------------------------------------

FETCH_PRODUCTS = """
MATCH (p:Product)
WHERE p.description IS NOT NULL AND p.description <> ""
RETURN p.sku AS sku, p.description AS description
"""

# Create/merge FAQ node by the first 120 chars of the question, then set
# properties and link to the product.
MERGE_FAQ = """
MATCH (p:Product {sku: $sku})
MERGE (faq:FAQ {merge_key: $merge_key})
ON CREATE SET
    faq.question     = $question,
    faq.answer       = $answer,
    faq.answer_short = $answer_short
MERGE (p)-[:HAS_FAQ]->(faq)
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(args):
    from falkordb import FalkorDB

    db = FalkorDB(host=args.host, port=args.port)
    graph = db.select_graph(args.graph)

    result = graph.query(FETCH_PRODUCTS)
    products = result.result_set
    print(f"Products with descriptions: {len(products)}")

    products_processed = 0
    faqs_extracted = 0
    nodes_created = 0
    rels_created = 0

    # Track FAQ nodes created across all products (for --dry-run display)
    seen_merge_keys: set[str] = set()

    for row in products:
        sku, description = row[0], row[1]
        desc = description or ""

        pairs = extract_faqs(desc)
        if not pairs:
            continue

        products_processed += 1
        faqs_extracted += len(pairs)

        if args.verbose:
            print(f"\n  {sku}  ({len(pairs)} FAQs)")
            for p in pairs:
                print(f"    Q: {p['question'][:80]}")
                print(f"    A: {p['answer_short'][:80]}")

        if not args.dry_run:
            for pair in pairs:
                mk = pair["merge_key"]
                is_new_node = mk not in seen_merge_keys
                seen_merge_keys.add(mk)

                graph.query(
                    MERGE_FAQ,
                    {
                        "sku":          sku,
                        "merge_key":    mk,
                        "question":     pair["question"],
                        "answer":       pair["answer"],
                        "answer_short": pair["answer_short"],
                    },
                )
                if is_new_node:
                    nodes_created += 1
                rels_created += 1

    print(f"\n{'--- DRY RUN ---' if args.dry_run else '--- RESULTS ---'}")
    print(f"  Products with FAQs extracted : {products_processed}")
    print(f"  Q&A pairs extracted          : {faqs_extracted}")
    if args.dry_run:
        unique = len({p['merge_key'] for row in products
                      for p in extract_faqs(row[1] or "")})
        print(f"  Unique FAQ nodes (would create): {unique}")
        print(f"  HAS_FAQ edges (would create)   : {faqs_extracted}")
    else:
        print(f"  FAQ nodes created (unique)   : {nodes_created}")
        print(f"  HAS_FAQ edges created        : {rels_created}")

    # Show samples from first matching product
    if args.verbose or args.dry_run:
        print("\n--- Sample FAQs (first 3 matched products) ---")
        shown = 0
        for row in products:
            if shown >= 3:
                break
            sku, desc = row[0], row[1] or ""
            pairs = extract_faqs(desc)
            if not pairs:
                continue
            shown += 1
            print(f"\n  Product: {sku}")
            for p in pairs[:3]:
                print(f"    Q: {p['question']}")
                print(f"    A: {p['answer_short'][:120]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest FAQ nodes from Product descriptions")
    parser.add_argument("--dry-run",  action="store_true", help="Parse & print, no DB writes")
    parser.add_argument("--verbose",  action="store_true", help="Print per-product details")
    parser.add_argument("--host",     default="localhost")
    parser.add_argument("--port",     type=int, default=6379)
    parser.add_argument("--graph",    default="dotandkey")
    args = parser.parse_args()
    run(args)
