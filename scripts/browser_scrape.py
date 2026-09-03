#!/usr/bin/env python3
"""Browser-based product page scraping utilities for Dot & Key storefront pages.

This is intentionally lightweight and deterministic: it opens a Chrome/Chromium
page via CDP (or Playwright) to render Shopify content, extracts visible text,
and returns structured product/FAQ data suitable for enrichment.
"""

from __future__ import annotations

import argparse
import json
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
MEDIA_PATH = ROOT / "data" / "product_media.json"


def load_product_handles(media: dict[str, Any] | None = None) -> list[str]:
    """Return unique product handles from the catalog metadata JSON."""
    if media is None:
        if not MEDIA_PATH.exists():
            return []
        try:
            media = json.loads(MEDIA_PATH.read_text())
        except Exception:
            return []

    handles: list[str] = []
    seen: set[str] = set()
    for info in media.values():
        handle = (info or {}).get("handle")
        if not handle:
            continue
        handle = str(handle).strip()
        if not handle or handle in seen:
            continue
        seen.add(handle)
        handles.append(handle)
    return handles


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def extract_product_text(html: str) -> str:
    """Extract a readable product description from a product page with a focus on main content."""
    soup = BeautifulSoup(html, "html.parser")
    for selector in ["main", "article", ".product", "#MainContent", "body"]:
        node = soup.select_one(selector)
        if node:
            text = " ".join(node.stripped_strings)
            cleaned = clean_text(text)
            if cleaned:
                return cleaned[:20000]
    return clean_text(soup.get_text())[:20000]


def extract_faq_pairs(html: str) -> list[dict[str, str]]:
    """Extract Q/A pairs from faq blocks, question headings, and answer paragraphs."""
    soup = BeautifulSoup(html, "html.parser")
    faqs: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(question: str, answer: str) -> None:
        if not question or not answer:
            return
        key = question.lower()[:200]
        if key in seen:
            return
        seen.add(key)
        faqs.append({"question": question, "answer": answer[:1000],
                     "merge_key": question[:120]})

    # Structured pass: themes that mark question and answer with paired class
    # names (Shopify accordions do this — dotandkey uses
    # collapsable-faq__question / __answer). Must run before the heading-based
    # heuristics below, which only look inside h1-h4/strong/summary/button and
    # therefore miss questions marked up as <p>.
    for q_el in soup.select("[class*='question']"):
        question = clean_text(q_el.get_text(" "))
        if not question or "?" not in question:
            continue
        answer = ""
        for sib in q_el.find_next_siblings():
            classes = " ".join(sib.get("class") or [])
            if "answer" in classes.lower():
                answer = clean_text(sib.get_text(" "))
                break
        if not answer:
            parent = q_el.parent
            if parent:
                a_el = parent.select_one("[class*='answer']")
                if a_el:
                    answer = clean_text(a_el.get_text(" "))
        _add(question, answer)

    if faqs:
        return faqs

    blocks = soup.select(".faq-item, .faq, details, .accordion-item, [class*='faq']")
    for block in blocks:
        question = None
        answer = None
        for tag in block.find_all(["h1", "h2", "h3", "h4", "strong", "summary", "button"]):
            q = clean_text(tag.get_text(" "))
            if q and "?" in q:
                question = q
                break

        if question:
            answer_candidates = []
            for tag in block.find_all(["p", "div", "li", "span"]):
                val = clean_text(tag.get_text(" "))
                if val and val != question:
                    answer_candidates.append(val)
            answer = " ".join(answer_candidates)[:1000] if answer_candidates else ""
        else:
            question_nodes = block.find_all(["h1", "h2", "h3", "h4"])
            if not question_nodes:
                continue
            for node in question_nodes:
                q = clean_text(node.get_text(" "))
                if q and "?" in q:
                    question = q
                    break
            if not question:
                continue
            answer = clean_text(block.get_text(" ").replace(question, ""))[:1000]

        if not question or not answer:
            continue

        key = question.lower()[:200]
        if key in seen:
            continue
        seen.add(key)
        faqs.append({"question": question, "answer": answer, "merge_key": question[:120]})

    if faqs:
        return faqs

    text = extract_product_text(html)
    pairs = []
    for match in re.finditer(r"([A-Z][^?]{5,200}\?)\s*(.*?)(?=(?:[A-Z][^?]{5,200}\?)|$)", text, flags=re.DOTALL):
        q = clean_text(match.group(1))
        a = clean_text(match.group(2))
        if q and a and len(a) > 10:
            pairs.append({"question": q, "answer": a[:1000], "merge_key": q[:120]})
    return pairs[:10]


_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _render(page: Any, url: str, timeout: int = 45) -> str:
    """Navigate an existing page to url and return settled HTML.

    Shopify pages often keep background network activity alive, so waiting for
    "networkidle" is unreliable. We instead wait for the main product DOM and
    then capture the page content after a short settle period.
    """
    page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)

    for selector in ("main", "article", "h1", ".product", "#MainContent", "body"):
        try:
            page.wait_for_selector(selector, timeout=10_000)
            break
        except Exception:
            continue

    page.wait_for_timeout(2000)
    return page.content()


@contextmanager
def browser_session(headless: bool = True):
    """Yield a fetch(url) -> html callable backed by ONE browser instance.

    Bulk scraping reuses a single Chromium across every product page. Launching
    per page costs roughly a second of startup each, which dominates a
    150-product run and buys nothing.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Playwright is not installed. Run: pip install playwright && "
            "python -m playwright install chromium"
        ) from exc

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(viewport={"width": 1440, "height": 2200})
        page.set_extra_http_headers({"User-Agent": _UA})
        try:
            yield lambda url: _render(page, url)
        finally:
            browser.close()


def fetch_page_via_browser(url: str, headless: bool = True, timeout: int = 45) -> str:
    """Render a single page in a throwaway browser and return the final HTML."""
    with browser_session(headless=headless) as fetch:
        return fetch(url)


def scrape_all_products(
    limit: int | None = None,
    headless: bool = True,
    out_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Scrape every product URL enumerated in product_media.json.

    When out_path is given, results are written after every page so an
    interrupted run keeps what it already fetched.
    """
    if not MEDIA_PATH.exists():
        return []

    try:
        media = json.loads(MEDIA_PATH.read_text())
    except Exception:
        return []

    handles = load_product_handles(media)
    if limit is not None:
        handles = handles[:limit]

    results: list[dict[str, Any]] = []
    with browser_session(headless=headless) as fetch:
        for idx, handle in enumerate(handles, start=1):
            url = f"https://www.dotandkey.com/products/{handle}"
            try:
                html = fetch(url)
            except Exception as exc:  # pragma: no cover - live-browser dependent path
                print(f"  [{idx}/{len(handles)}] {handle} — FAILED: {exc}")
                results.append({"url": url, "error": str(exc),
                                "product_text": "", "faqs": [], "index": idx})
                continue

            entry = {
                "url": url,
                "product_text": extract_product_text(html),
                "faqs": extract_faq_pairs(html),
                "index": idx,
            }
            results.append(entry)
            print(f"  [{idx}/{len(handles)}] {handle} — "
                  f"{len(entry['product_text'])} chars, {len(entry['faqs'])} FAQs")

            # Checkpoint after every page: a 150-page live scrape that dies at
            # page 140 must not lose the first 139.
            if out_path is not None:
                _write_results(out_path, results)

    return results


def _write_results(out_path: Path, results: list[dict[str, Any]]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Browser scrape Dot & Key product pages")
    parser.add_argument("--url", help="Single product URL to scrape")
    parser.add_argument("--all-products", action="store_true", help="Scrape every product in data/product_media.json")
    parser.add_argument("--limit", type=int, default=None, help="Optional limit for bulk scraping")
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    parser.add_argument(
        "--out",
        help="Write results to this file (checkpointed after every page). "
             "Prefer this over shell redirection for bulk runs.",
    )
    args = parser.parse_args()

    if args.all_products:
        out_path = Path(args.out) if args.out else None
        results = scrape_all_products(
            limit=args.limit, headless=args.headless, out_path=out_path,
        )
        if out_path:
            _write_results(out_path, results)
            ok = sum(1 for r in results if not r.get("error"))
            faqs = sum(len(r.get("faqs") or []) for r in results)
            print(f"\nWrote {len(results)} entries ({ok} ok, {faqs} FAQs) → {out_path}")
        elif args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            for item in results:
                print(f"{item.get('index', '')} | {item['url']}")
                text = item.get("product_text", "")
                if text:
                    print(text[:400])
                if item.get("faqs"):
                    print(f" FAQs: {len(item['faqs'])}")
        return

    if not args.url:
        parser.error("Either --url or --all-products is required")

    html = fetch_page_via_browser(args.url, headless=args.headless)
    product_text = extract_product_text(html)
    faqs = extract_faq_pairs(html)

    payload = {
        "url": args.url,
        "product_text": product_text,
        "faqs": faqs,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(product_text[:3000])
        if faqs:
            print("\n--- FAQS ---")
            for faq in faqs[:5]:
                print(f"Q: {faq['question']}\nA: {faq['answer']}\n")


if __name__ == "__main__":
    main()
