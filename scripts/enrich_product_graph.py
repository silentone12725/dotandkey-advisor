#!/usr/bin/env python3
"""
scripts/enrich_product_graph.py

Scrape each product's public Shopify page and enrich the existing product graph
with additional claim/benefit metadata extracted from product pages.

This is intentionally conservative:
- It never invents product facts.
- It only enriches from public product metadata already exposed on the website.
- It stores extracted claims in a structured, queryable way on Product nodes and
  creates Claim nodes when a claim is explicit.

Usage:
    python3 scripts/enrich_product_graph.py --host localhost --port 6379 --graph dotandkey
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parent.parent
MEDIA_PATH = ROOT / "data" / "product_media.json"
USER_AGENT = (
    "Mozilla/5.0 (compatible; DotKeyAdvisorEnricher/1.0; "
    "+https://github.com/dotandkey-advisor)"
)

_CLAIM_PATTERNS = [
    "brightening",
    "hydrating",
    "soothing",
    "fragrance free",
    "sulfate free",
    "paraben free",
    "alcohol free",
    "for sensitive skin",
    "for acne-prone skin",
    "for dry skin",
    "for oily skin",
    "for combination skin",
    "non-greasy",
    "lightweight",
    "pore care",
    "sun protection",
    "barrier repair",
    "anti-aging",
    "mattifying",
    "dewy",
    "glow",
    "hydration",
]

_SECTION_LABELS = [
    "ingredients",
    "key ingredients",
    "what it contains",
    "benefits",
    "why it works",
    "best for",
    "good for",
    "suitable for",
    "how to use",
    "who it's for",
    "what's inside",
    "what it does",
    "this serum is for",
]

_FAQ_PATTERN = re.compile(
    r"""
    (?:(?:^|\n)\s*\d+\.\s*)?      # optional numbered item
    ([A-Z][^?]{5,200}?\?)          # question
    \s*(?:Ans(?:wer)?|A)\s*[:\-]\s* # answer keyword
    (.*?)(?=\n\s*(?:\d+\.\s*[A-Z]|[A-Z][^?]{5,200}?\?)|$)
    """,
    re.VERBOSE | re.DOTALL,
)

_FAQ_FALLBACK_PATTERN = re.compile(
    r"""
    ([A-Z][^?]{5,200}?\?)
    \s*(?:Ans(?:wer)?|A|It|This|Yes|No|Use|Suitable|Works|Helps|Can|Will|Does|Is|How|When|Why)\b
    (.*?)(?=\n\s*(?:\d+\.\s*[A-Z]|[A-Z][^?]{5,200}?\?)|$)
    """,
    re.VERBOSE | re.DOTALL,
)


def normalize_space(s: Any) -> str:
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    s = html_lib.unescape(s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def strip_html_fragment(raw: str) -> str:
    if not raw:
        return ""
    text = html_lib.unescape(raw)
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_section_text(body_text: str, labels: list[str]) -> dict[str, str]:
    lower = body_text.lower()
    sections: dict[str, str] = {}
    for label in labels:
        idx = lower.find(label)
        if idx == -1:
            continue
        start = idx + len(label)
        window = body_text[start:start + 1000]
        tail = re.split(r"(?i)\b(?:" + "|".join(re.escape(x) for x in _SECTION_LABELS) + r")\b", window, maxsplit=1)[0]
        value = normalize_space(tail)
        if value:
            sections[label] = value
    return sections


def extract_ingredients(body_text: str) -> str:
    if not body_text:
        return ""
    candidates = []
    for label in ["ingredients", "key ingredients", "what it contains"]:
        idx = body_text.lower().find(label)
        if idx != -1:
            start = idx + len(label)
            snippet = body_text[start:start + 700]
            # keep only a reasonable chunk after the label before the next heading
            snippet = re.split(r"(?i)\b(?:benefits|why it works|how to use|good for|best for|suitable for)\b", snippet, maxsplit=1)[0]
            cleaned = normalize_space(snippet)
            if cleaned:
                candidates.append(cleaned)
    if not candidates:
        return normalize_space(body_text[:500])
    return " | ".join(candidates)[:2000]


def extract_benefits(body_text: str) -> str:
    if not body_text:
        return ""
    candidates = []
    labels = ["benefits", "why it works", "good for", "best for", "suitable for", "who it's for"]
    for label in labels:
        idx = body_text.lower().find(label)
        if idx != -1:
            start = idx + len(label)
            snippet = body_text[start:start + 800]
            snippet = re.split(r"(?i)\b(?:ingredients|how to use|what it contains|suitable for|good for|best for)\b", snippet, maxsplit=1)[0]
            cleaned = normalize_space(snippet)
            if cleaned:
                candidates.append(cleaned)
    if not candidates:
        return normalize_space(body_text[:500])
    return " | ".join(candidates)[:2000]


def extract_claims_from_text(text: str) -> list[str]:
    lowered = text.lower()
    matches: list[str] = []
    for claim in _CLAIM_PATTERNS:
        if claim in lowered:
            matches.append(claim)
    # de-duplicate while preserving order
    ordered: list[str] = []
    seen: set[str] = set()
    for claim in matches:
        if claim not in seen:
            ordered.append(claim)
            seen.add(claim)
    return ordered


def product_knowledge_from_json(product_data: dict[str, Any]) -> dict[str, Any]:
    body_html = product_data.get("body_html") or ""
    body_text = strip_html_fragment(body_html)
    if not body_text:
        body_text = normalize_space(product_data.get("description") or "")

    ingredients_text = extract_ingredients(body_text)
    benefits_text = extract_benefits(body_text)
    claims = extract_claims_from_text(f"{body_text} {' '.join(product_data.get('tags', []) or [])}")

    return {
        "ingredients_text": ingredients_text,
        "benefits_text": benefits_text,
        "claims": claims,
        "product_type": product_data.get("product_type") or "",
        "tags": list(product_data.get("tags", []) or []),
        "title": product_data.get("title") or "",
    }


def fetch_product_html(handle: str) -> str:
    url = f"https://www.dotandkey.com/products/{handle}"
    try:
        resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=15.0, follow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception:
        return ""


def _sentence_faq_fallback(text: str) -> list[dict[str, str]]:
    faq_pairs: list[dict[str, str]] = []
    seen: set[str] = set()
    sentences = re.split(r"(?<=[.?])\s+", text)
    for i in range(len(sentences) - 1):
        q = normalize_space(sentences[i])
        a = normalize_space(sentences[i + 1])
        if not q or not a:
            continue
        if "?" not in q or len(q) > 220:
            continue
        if len(a) < 8:
            continue
        if q.lower().startswith(("how to", "what is", "when should", "why is", "can i", "does it", "is it", "will it", "how often")):
            key = q[:120].lower()
            if key in seen:
                continue
            seen.add(key)
            faq_pairs.append({"question": q, "answer": a[:500], "merge_key": q[:120]})
    return faq_pairs


def extract_faqs_from_html(html_blob: str) -> list[dict[str, str]]:
    text = strip_html_fragment(html_blob)
    faq_pairs: list[dict[str, str]] = []
    seen: set[str] = set()

    for pattern in (_FAQ_PATTERN, _FAQ_FALLBACK_PATTERN):
        for match in pattern.finditer(text):
            q = normalize_space(match.group(1))
            a = normalize_space(match.group(2))
            if not q or not a:
                continue
            key = q[:120].lower()
            if key in seen:
                continue
            seen.add(key)
            faq_pairs.append({"question": q, "answer": a[:500], "merge_key": q[:120]})

        if faq_pairs:
            break

    if not faq_pairs:
        faq_pairs = _sentence_faq_fallback(text)
    return faq_pairs


def extract_html_sections(html_blob: str) -> dict[str, str]:
    text = strip_html_fragment(html_blob)
    sections = extract_section_text(text, _SECTION_LABELS)
    ingredients_text = extract_ingredients(text)
    benefits_text = extract_benefits(text)
    if ingredients_text:
        sections["ingredients"] = ingredients_text
    if benefits_text:
        sections["benefits"] = benefits_text
    if text:
        sections["page_context"] = text[:30000]
    return sections


def fetch_product_json(handle: str) -> dict[str, Any] | None:
    url = f"https://www.dotandkey.com/products/{handle}.js"
    try:
        resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=15.0, follow_redirects=True)
        resp.raise_for_status()
        payload = resp.json()
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def ensure_claim_edges(graph: Any, sku: str, claims: list[str]) -> None:
    for claim in claims:
        graph.query(
            "MERGE (c:Claim {name: $name}) "
            "WITH c "
            "MATCH (p:Product {sku: $sku}) "
            "MERGE (p)-[:HAS_CLAIM]->(c)",
            {"name": claim, "sku": sku},
        )


def ensure_faq_edges(graph: Any, sku: str, faqs: list[dict[str, str]]) -> None:
    for faq in faqs:
        graph.query(
            "MERGE (f:FAQ {merge_key: $merge_key}) "
            "ON CREATE SET f.question = $question, f.answer = $answer, f.answer_short = $answer_short "
            "WITH f "
            "MATCH (p:Product {sku: $sku}) "
            "MERGE (p)-[:HAS_FAQ]->(f)",
            {
                "sku": sku,
                "merge_key": faq["merge_key"],
                "question": faq["question"],
                "answer": faq["answer"],
                "answer_short": faq["answer"][:200],
            },
        )


def run(args):
    if not MEDIA_PATH.exists():
        print("[enrich] no product_media.json found — nothing to enrich")
        return

    media = json.loads(MEDIA_PATH.read_text())
    if not media:
        print("[enrich] product_media.json is empty — nothing to enrich")
        return

    from falkordb import FalkorDB

    db = FalkorDB(host=args.host, port=args.port)
    graph = db.select_graph(args.graph)

    processed = 0
    enriched = 0
    claim_count = 0
    faq_count = 0

    for sku, info in media.items():
        handle = (info.get("handle") or "").strip()
        if not handle:
            continue
        product_data = fetch_product_json(handle)
        page_html = fetch_product_html(handle)
        if not product_data and not page_html:
            continue

        knowledge = product_knowledge_from_json(product_data) if product_data else {"ingredients_text": "", "benefits_text": "", "claims": []}
        page_sections = extract_html_sections(page_html) if page_html else {}
        if page_sections.get("ingredients"):
            knowledge["ingredients_text"] = page_sections["ingredients"]
        if page_sections.get("benefits"):
            knowledge["benefits_text"] = page_sections["benefits"]
        knowledge["claims"] = list(dict.fromkeys(knowledge["claims"] + extract_claims_from_text(
            " ".join(page_sections.values()) + " " + (page_html or "")
        )))

        page_context = page_sections.get("page_context") or strip_html_fragment(page_html or "")[:30000]
        faqs = extract_faqs_from_html(page_html) if page_html else []
        if not knowledge["ingredients_text"] and not knowledge["benefits_text"] and not knowledge["claims"] and not faqs:
            continue

        graph.query(
            "MATCH (p:Product {sku: $sku}) "
            "SET p.ingredients_text = $ingredients_text, "
            "p.benefits_text = $benefits_text, "
            "p.claims = $claims, "
            "p.page_context = $page_context, "
            "p.enriched_from_page = true, "
            "p.page_enriched_at = timestamp()",
            {
                "sku": sku,
                "ingredients_text": knowledge["ingredients_text"],
                "benefits_text": knowledge["benefits_text"],
                "claims": " | ".join(knowledge["claims"]),
                "page_context": page_context,
            },
        )

        ensure_claim_edges(graph, sku, knowledge["claims"])
        ensure_faq_edges(graph, sku, faqs)
        processed += 1
        claim_count += len(knowledge["claims"])
        faq_count += len(faqs)
        if knowledge["claims"] or knowledge["benefits_text"] or knowledge["ingredients_text"] or faqs:
            enriched += 1

        time.sleep(0.25)

    print(f"[enrich] processed {processed} products; enriched {enriched}; created {claim_count} claim links and {faq_count} FAQ links")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape public product-page metadata and enrich the graph")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--graph", default="dotandkey")
    run(parser.parse_args())
