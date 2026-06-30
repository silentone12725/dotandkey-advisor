"""
backend/comparison_queries.py

Handlers for follow-up comparison questions.
Called by the router when it detects a comparison / explanation intent.

All answers are derived from graph traversal + structured explanation data —
never from free-form LLM reasoning. The LLM receives the structured answer
as context and formats it into one conversational sentence.
"""

import logging
import os
from typing import AsyncGenerator

_log = logging.getLogger(__name__)


def _get_graph():
    from falkordb import FalkorDB
    db = FalkorDB(
        host=os.getenv("FALKORDB_HOST", "localhost"),
        port=int(os.getenv("FALKORDB_PORT", 6379)),
    )
    return db.select_graph(os.getenv("FALKORDB_GRAPH", "dotandkey"))


# ---------------------------------------------------------------------------
# Core answer builders (return structured dicts — no LLM)
# ---------------------------------------------------------------------------

def answer_why_ranked_first(
    sku: str,
    profile_id: str,
) -> dict:
    """Return structured graph evidence for why this product was ranked first.

    Pulls the cached explanation from the last recommendation result,
    or re-derives it from the graph.
    """
    from backend.profile import load_profile, parse_profile
    from backend.explainability import build_ranking_explanation, _get_cap_scores
    from backend.retrieval import retrieve, PRICE_TIER_TO_MAX

    graph = _get_graph()
    raw = load_profile(profile_id)
    parsed = parse_profile(raw)

    # Fetch the product directly
    try:
        rows = graph.query(
            "MATCH (p:Product {sku: $sku}) "
            "OPTIONAL MATCH (p)-[:CONTAINS_INGREDIENT]->(i:Ingredient) "
            "OPTIONAL MATCH (p)-[:TARGETS_CONCERN]->(c:Concern) "
            "OPTIONAL MATCH (p)-[:FREE_FROM]->(af:AllergenClass) "
            "RETURN p.sku, p.title, p.price, p.category_raw, p.description, "
            "       collect(DISTINCT i.name), collect(DISTINCT c.name), "
            "       collect(DISTINCT af.name)",
            {"sku": sku},
        ).result_set
    except Exception as e:
        return {"error": str(e)}

    if not rows:
        return {"error": f"Product {sku} not found"}

    r = rows[0]
    product = {
        "sku": r[0], "title": r[1], "price": r[2], "category_raw": r[3],
        "description": r[4] or "",
        "ingredients": r[5] or [], "matched_concerns": r[6] or [], "free_from": r[7] or [],
    }

    explanation = build_ranking_explanation(
        product=product, rank=1, all_products=[product],
        user_profile=parsed, query_tokens=[],
    )
    return {"product": product["title"], "explanation": explanation}


def answer_what_do_i_gain(sku_from: str, sku_to: str) -> dict:
    """What do I gain by switching from sku_from to sku_to?"""
    from backend.product_relations import get_capability_comparison
    graph = _get_graph()
    comparison = get_capability_comparison(graph, sku_to, sku_from)
    return {
        "switching_to": comparison["product_a"]["title"],
        "switching_from": comparison["product_b"]["title"],
        "gains": comparison["advantages_a"],
        "losses": comparison["advantages_b"],
    }


def answer_what_do_i_lose(sku_from: str, sku_to: str) -> dict:
    """What do I lose by switching from sku_from to sku_to?"""
    return answer_what_do_i_gain(sku_to, sku_from)  # flip perspective


def answer_closest_fragrance_free(sku: str, category: str) -> dict:
    """Return the closest fragrance-free alternative to a given product."""
    from backend.product_relations import get_alternatives, get_similar_products
    graph = _get_graph()
    alts = get_alternatives(graph, sku, constraint="fragrance_free")
    if not alts:
        # Fall back to similar products; the caller can check free_from
        alts = get_similar_products(graph, sku)
    return {"alternatives": alts[:3]}


def answer_key_difference(sku_a: str, sku_b: str) -> dict:
    """Return the single most significant capability difference between two products."""
    from backend.product_relations import get_capability_comparison
    from graph.capability_schema import CAPABILITY_LABELS
    graph = _get_graph()
    comparison = get_capability_comparison(graph, sku_a, sku_b)
    caps_a = comparison["product_a"]["caps"]
    caps_b = comparison["product_b"]["caps"]

    biggest_axis = None
    biggest_delta = 0.0
    from graph.capability_schema import CAPABILITY_AXES
    for ax in CAPABILITY_AXES:
        delta = abs(caps_a.get(ax, 0) - caps_b.get(ax, 0))
        if delta > biggest_delta:
            biggest_delta = delta
            biggest_axis = ax

    if not biggest_axis:
        return {
            "title_a": comparison["product_a"]["title"],
            "title_b": comparison["product_b"]["title"],
            "difference": "Similar overall capability profiles",
        }

    label = CAPABILITY_LABELS.get(biggest_axis, biggest_axis.title())
    score_a = caps_a.get(biggest_axis, 0)
    score_b = caps_b.get(biggest_axis, 0)
    winner = comparison["product_a" if score_a > score_b else "product_b"]["title"]
    loser  = comparison["product_b" if score_a > score_b else "product_a"]["title"]
    return {
        "title_a": comparison["product_a"]["title"],
        "title_b": comparison["product_b"]["title"],
        "key_axis": label,
        "difference": (
            f"{winner} has higher {label} ({max(score_a, score_b):.1f}/10 vs "
            f"{min(score_a, score_b):.1f}/10)"
        ),
    }


# ---------------------------------------------------------------------------
# Streaming playbook entry point
# ---------------------------------------------------------------------------

async def run(
    profile_id: str,
    user_message: str,
    router_args: dict,
) -> AsyncGenerator[str, None]:
    """Playbook for comparison/explanation follow-up questions.

    router_args expected keys:
      intent: "why_ranked" | "compare_products" | "find_alternative" | "key_difference"
      sku:    primary product SKU
      sku_b:  secondary product SKU (for compare intents)
    """
    from backend.playbooks.base import build_system_prompt, stream_response, emit_ui_data

    intent = router_args.get("intent", "why_ranked")
    sku    = router_args.get("sku", "")
    sku_b  = router_args.get("sku_b", "")

    # Build the structured answer from graph
    answer: dict = {}
    try:
        if intent == "why_ranked":
            answer = answer_why_ranked_first(sku, profile_id)
        elif intent == "compare_products":
            answer = answer_what_do_i_gain(sku_b, sku) if sku_b else {}
        elif intent == "find_alternative":
            answer = answer_closest_fragrance_free(sku, router_args.get("category", ""))
        elif intent == "key_difference":
            answer = answer_key_difference(sku, sku_b) if sku_b else {}
    except Exception as e:
        _log.exception("comparison_queries.run failed: %s", e)

    # Inject structured answer into LLM context for one-sentence narration
    import json
    answer_json = json.dumps(answer, indent=2)
    system = build_system_prompt(profile_id, extra_context=(
        f"\nGraph evidence for this question:\n<evidence>\n{answer_json}\n</evidence>\n\n"
        "Instructions: narrate this evidence in ONE natural sentence. "
        "Do NOT add facts not in the evidence. Do NOT say 'based on the graph'."
    ))

    async for token in stream_response(system, profile_id, user_message):
        yield token

    # Emit the raw structured data for the widget to render
    yield emit_ui_data({"comparison": answer})
