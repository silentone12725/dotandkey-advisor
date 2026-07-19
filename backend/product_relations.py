"""
backend/product_relations.py

Graph query helpers for product-to-product relationship traversal.

Used by:
  - backend/comparison_queries.py  (follow-up question handlers)
  - backend/playbooks/recommend.py (alternative suggestions)
"""

import logging

from backend.retrieval import get_graph as _get_graph

_log = logging.getLogger(__name__)

_COMPARATIVE_RELS = [
    "MORE_HYDRATING_THAN",
    "BETTER_FOR_OILY_SKIN_THAN",
    "BETTER_BARRIER_REPAIR_THAN",
    "MORE_BRIGHTENING_THAN",
    "BETTER_FOR_PIGMENTATION_THAN",
    "BETTER_FOR_ACNE_THAN",
    "BETTER_PORE_CARE_THAN",
    "GENTLER_THAN",
    "FRAGRANCE_FREE_ALTERNATIVE_TO",
    "BUDGET_ALTERNATIVE_TO",
    "PREMIUM_ALTERNATIVE_TO",
    "SIMILAR_TO",
]

# Relationship → human label (for explanation)
REL_LABELS: dict[str, str] = {
    "MORE_HYDRATING_THAN":          "More hydrating than",
    "BETTER_FOR_OILY_SKIN_THAN":    "Better for oily skin than",
    "BETTER_BARRIER_REPAIR_THAN":   "Better for barrier repair than",
    "MORE_BRIGHTENING_THAN":        "More brightening than",
    "BETTER_FOR_PIGMENTATION_THAN": "Better for pigmentation than",
    "BETTER_FOR_ACNE_THAN":         "Better for acne than",
    "BETTER_PORE_CARE_THAN":        "Better pore care than",
    "GENTLER_THAN":                 "Gentler than",
    "FRAGRANCE_FREE_ALTERNATIVE_TO":"Fragrance-free alternative to",
    "BUDGET_ALTERNATIVE_TO":        "Budget alternative to",
    "PREMIUM_ALTERNATIVE_TO":       "Premium alternative to",
    "SIMILAR_TO":                   "Similar to",
}


def get_comparative_edges(graph, sku: str, rel_types: list[str] | None = None) -> list[dict]:
    """Return outgoing comparative edges from a product.

    Returns list of dicts: {rel_type, target_sku, target_title, reason, confidence}
    """
    rels = rel_types or _COMPARATIVE_RELS
    # FalkorDB requires specifying each rel type separately (no dynamic rel names in Cypher)
    results = []
    for rel in rels:
        try:
            rows = graph.query(
                f"MATCH (a:Product {{sku: $sku}})-[r:{rel}]->(b:Product) "
                "RETURN b.sku, b.title, r.reason, r.confidence",
                {"sku": sku},
            ).result_set
            for b_sku, b_title, reason, confidence in rows:
                results.append({
                    "rel_type": rel,
                    "rel_label": REL_LABELS.get(rel, rel),
                    "target_sku": b_sku,
                    "target_title": b_title or "",
                    "reason": reason or "",
                    "confidence": float(confidence or 0),
                })
        except Exception as e:
            _log.debug("product_relations: %s edge query failed: %s", rel, e)
    return results


def get_alternatives(graph, sku: str, constraint: str = "fragrance_free") -> list[dict]:
    """Return alternative products filtered by constraint.

    constraint: "fragrance_free" | "budget" | "premium"
    """
    rel_map = {
        "fragrance_free": "FRAGRANCE_FREE_ALTERNATIVE_TO",
        "budget": "BUDGET_ALTERNATIVE_TO",
        "premium": "PREMIUM_ALTERNATIVE_TO",
    }
    rel = rel_map.get(constraint, "SIMILAR_TO")
    try:
        rows = graph.query(
            f"MATCH (a:Product {{sku: $sku}})-[r:{rel}]->(b:Product) "
            "RETURN b.sku, b.title, b.price, b.url, b.image_url, r.reason",
            {"sku": sku},
        ).result_set
        return [
            {
                "sku": r[0], "title": r[1], "price": r[2],
                "url": r[3] or "", "image_url": r[4] or "",
                "reason": r[5] or "",
            }
            for r in rows
        ]
    except Exception as e:
        _log.debug("get_alternatives failed: %s", e)
        return []


def get_similar_products(graph, sku: str, limit: int = 3) -> list[dict]:
    """Return the most similar products by shared ingredients."""
    try:
        rows = graph.query(
            "MATCH (a:Product {sku: $sku})-[r:SIMILAR_TO]->(b:Product) "
            "RETURN b.sku, b.title, b.price, b.url, b.image_url, r.confidence "
            "ORDER BY r.confidence DESC",
            {"sku": sku},
        ).result_set
        return [
            {
                "sku": r[0], "title": r[1], "price": r[2],
                "url": r[3] or "", "image_url": r[4] or "",
                "confidence": float(r[5] or 0),
            }
            for r in rows[:limit]
        ]
    except Exception as e:
        _log.debug("get_similar_products failed: %s", e)
        return []


def get_capability_comparison(graph, sku_a: str, sku_b: str) -> dict:
    """Return capability scores for both products for direct comparison."""
    from graph.capability_schema import CAPABILITY_AXES, cap_prop, CAPABILITY_LABELS

    def _fetch_caps(sku: str) -> tuple[str, dict]:
        try:
            props = ", ".join(f"p.{cap_prop(ax)} AS {cap_prop(ax)}" for ax in CAPABILITY_AXES)
            rows = graph.query(
                f"MATCH (p:Product {{sku: $sku}}) RETURN p.title, {props}",
                {"sku": sku},
            ).result_set
            if not rows:
                return "", {}
            row = rows[0]
            title = row[0] or ""
            caps = {ax: float(row[i + 1] or 0) for i, ax in enumerate(CAPABILITY_AXES)}
            return title, caps
        except Exception:
            return "", {}

    title_a, caps_a = _fetch_caps(sku_a)
    title_b, caps_b = _fetch_caps(sku_b)

    advantages_a, advantages_b = [], []
    for ax in CAPABILITY_AXES:
        a_score = caps_a.get(ax, 0)
        b_score = caps_b.get(ax, 0)
        label = CAPABILITY_LABELS.get(ax, ax.title())
        if a_score - b_score >= 1.5:
            advantages_a.append(f"{label}: {a_score:.1f} vs {b_score:.1f}")
        elif b_score - a_score >= 1.5:
            advantages_b.append(f"{label}: {b_score:.1f} vs {a_score:.1f}")

    return {
        "product_a": {"sku": sku_a, "title": title_a, "caps": caps_a},
        "product_b": {"sku": sku_b, "title": title_b, "caps": caps_b},
        "advantages_a": advantages_a,
        "advantages_b": advantages_b,
    }
