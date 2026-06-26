"""
backend/context.py

Handles /context/product:
  - Receives {handle, title, tags} from widget.js
  - Looks up the product in FalkorDB by title
  - Fetches its graph edges (including FAQ nodes via HAS_FAQ)
  - Uses FAQ questions as the primary question chips (falls back to
    generate_product_questions when no FAQs exist for the product)
  - Queries similar products (same category + cross-category)
  - Returns the full context dict to the widget
"""

import os
from falkordb import FalkorDB
from backend.questions import generate_product_questions


# ---------------------------------------------------------------------------
# DB connection
# ---------------------------------------------------------------------------

def _get_graph():
    db = FalkorDB(
        host=os.getenv("FALKORDB_HOST", "localhost"),
        port=int(os.getenv("FALKORDB_PORT", 6379)),
    )
    return db.select_graph(os.getenv("FALKORDB_GRAPH", "dotandkey"))


# ---------------------------------------------------------------------------
# Graph queries
# ---------------------------------------------------------------------------

_PRODUCT_CONTEXT_QUERY = """
MATCH (p:Product)
WHERE p.title CONTAINS $title_fragment OR p.title = $title
OPTIONAL MATCH (p)-[:SUITS_SKIN_TYPE]->(st:SkinType)
OPTIONAL MATCH (p)-[:TARGETS_CONCERN]->(cn:Concern)
OPTIONAL MATCH (p)-[:CONTAINS_INGREDIENT]->(ing:Ingredient)
OPTIONAL MATCH (p)-[:FREE_FROM]->(af:AllergenClass)
OPTIONAL MATCH (p)-[:BEST_IN_SEASON]->(s:Season)
OPTIONAL MATCH (p)-[:HAS_TEXTURE]->(tx:Texture)
OPTIONAL MATCH (p)-[:IN_CATEGORY]->(cat:Category)
RETURN p.sku AS sku, p.title AS title, p.price AS price,
       p.description AS description,
       collect(DISTINCT st.name) AS skin_types,
       collect(DISTINCT cn.name) AS concerns,
       collect(DISTINCT ing.name) AS ingredients,
       collect(DISTINCT af.name) AS allergen_free,
       collect(DISTINCT s.name)  AS seasons,
       tx.name AS texture,
       cat.name AS category
LIMIT 1
"""

_SIMILAR_SAME_CAT_QUERY = """
MATCH (p:Product)-[:IN_CATEGORY]->(:Category {name: $category})
MATCH (p)-[:SUITS_SKIN_TYPE]->(st:SkinType)
WHERE st.name IN $skin_types AND p.sku <> $sku AND p.active = true
RETURN p.sku AS sku, p.title AS title, p.price AS price,
       p.url AS url, p.image_url AS image_url,
       $category AS category
ORDER BY p.price ASC
LIMIT 3
"""

_SIMILAR_CROSS_CAT_QUERY = """
MATCH (p:Product)-[:TARGETS_CONCERN]->(cn:Concern)
MATCH (p)-[:IN_CATEGORY]->(cat:Category)
MATCH (p)-[:SUITS_SKIN_TYPE]->(st:SkinType)
WHERE cn.name IN $concerns AND cat.name <> $category
  AND st.name IN $skin_types AND p.active = true
RETURN p.sku AS sku, p.title AS title, p.price AS price,
       p.url AS url, p.image_url AS image_url,
       cat.name AS category
ORDER BY p.price ASC
LIMIT 4
"""

_FAQ_QUERY = """
MATCH (p:Product {sku: $sku})-[:HAS_FAQ]->(faq:FAQ)
RETURN faq.question AS question, faq.answer_short AS answer
ORDER BY faq.question
LIMIT 8
"""


# ---------------------------------------------------------------------------
# Public: get_product_context
# ---------------------------------------------------------------------------

def get_product_context(title: str, current_season: str) -> dict:
    """Look up a product by title, return full context for the widget.

    Args:
        title:          Full product title from Shopify /products/{handle}.json
        current_season: From session.py (summer|monsoon|post_monsoon|winter)
    """
    graph = _get_graph()

    # Use first 4 words of title as a fragment for fuzzy matching
    title_fragment = " ".join(title.split()[:4])

    result = graph.query(
        _PRODUCT_CONTEXT_QUERY,
        {"title": title, "title_fragment": title_fragment},
    )

    if not result.result_set:
        return {"found": False, "title": title}

    row = result.result_set[0]
    ctx = {
        "found":        True,
        "sku":          row[0],
        "title":        row[1],
        "price":        row[2],
        "description":  (row[3] or "")[:300],
        "skin_types":   row[4] or [],
        "concerns":     row[5] or [],
        "ingredients":  row[6] or [],
        "allergen_free":row[7] or [],
        "seasons":      row[8] or [],
        "texture":      row[9] or "",
        "category":     row[10] or "",
    }

    # Fetch FAQs from graph (populated by scripts/ingest_faqs.py)
    faqs: list[dict] = []
    if ctx["sku"]:
        faq_res = graph.query(_FAQ_QUERY, {"sku": ctx["sku"]})
        faqs = [
            {"question": r[0], "answer": r[1]}
            for r in faq_res.result_set
            if r[0] and r[1]
        ]
    ctx["faqs"] = faqs

    # Question chips: prefer real FAQ questions from the graph (top 5);
    # fall back to the keyword-driven generator when no FAQs exist.
    if faqs:
        faq_questions = [faq["question"] for faq in faqs[:5]]
        faq_questions.append("Something else…")
        ctx["questions"] = faq_questions
    else:
        ctx["questions"] = generate_product_questions(ctx, current_season)

    # Similar products — same category
    similar_same = []
    if ctx["sku"] and ctx["category"] and ctx["skin_types"]:
        same_res = graph.query(
            _SIMILAR_SAME_CAT_QUERY,
            {
                "sku":       ctx["sku"],
                "category":  ctx["category"],
                "skin_types": ctx["skin_types"],
            },
        )
        similar_same = [
            {"sku": r[0], "title": r[1], "price": r[2],
             "url": r[3] or "", "image_url": r[4] or "", "category": r[5]}
            for r in same_res.result_set
        ]

    # Similar products — cross-category routine companions
    similar_cross = []
    if ctx["sku"] and ctx["category"] and ctx["concerns"] and ctx["skin_types"]:
        cross_res = graph.query(
            _SIMILAR_CROSS_CAT_QUERY,
            {
                "sku":       ctx["sku"],
                "category":  ctx["category"],
                "concerns":  ctx["concerns"],
                "skin_types": ctx["skin_types"],
            },
        )
        similar_cross = [
            {"sku": r[0], "title": r[1], "price": r[2],
             "url": r[3] or "", "image_url": r[4] or "", "category": r[5]}
            for r in cross_res.result_set
        ]

    ctx["similar_same_category"] = similar_same
    ctx["similar_routine"]       = similar_cross

    return ctx