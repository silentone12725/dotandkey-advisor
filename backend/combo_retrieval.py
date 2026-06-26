"""
backend/combo_retrieval.py

Queries the product graph for Combo nodes that match the user's skin type
and concerns.  Returns up to MAX_COMBOS combos, each enriched with the
metadata needed to render a combo card in the widget:

  sku, title, price, compare_at_price, url, image_url,
  matched_skin_types, matched_concerns,
  components: [{sku, title, image_url, url}]   ← each included product

Combos are ranked by skin-type match score (descending), price (ascending).
Results are limited to combos whose price fits the user's price_tier when set.

The Combo nodes and their INCLUDES / SUITS_SKIN_TYPE / TARGETS_CONCERN edges
are populated during ingest by scripts/csv_to_graph.py (build_combos).
"""

import os

MAX_COMBOS = 3

# price_tier -> max price cap (mirrors retrieval.py's PRICE_TIER_TO_MAX)
PRICE_TIER_TO_MAX: dict[str, float | None] = {
    "under_300":  300.0,
    "under_600":  600.0,
    "under_1000": 1000.0,
    "any":        None,
}


def retrieve_combos(graph, profile: dict) -> list[dict]:
    """Return up to MAX_COMBOS Combo dicts that match the user's skin type.

    Falls back gracefully to an empty list if the Combo nodes haven't been
    ingested yet (the graph node label simply won't match any results).

    Args:
        graph:   FalkorDB graph object (the product graph, not user graph).
        profile: Parsed profile dict from profile.py's parse_profile().
                 Keys used: skin_types (list), concerns (list), price_tier (str).
    """
    skin_types = profile.get("skin_types") or []
    concerns   = profile.get("concerns") or []
    price_tier = profile.get("price_tier", "any")
    max_price  = PRICE_TIER_TO_MAX.get(price_tier)

    if not skin_types:
        return []

    params: dict = {"skin_types": skin_types}

    # ── Query: find combos matching skin type, collect concern matches ────────
    # Structure:
    #  1. MATCH combos that SUIT at least one of the user's skin types
    #  2. OPTIONAL MATCH concern overlap
    #  3. MATCH component products (INCLUDES)
    #  4. Filter: active, price cap, at least 2 components
    #  5. Order by skin score DESC, price ASC
    #
    # FalkorDB rule: all hard MATCHes before OPTIONAL MATCHes.
    lines = [
        "MATCH (c:Combo)-[:SUITS_SKIN_TYPE]->(st:SkinType) "
        "WHERE st.name IN $skin_types",
    ]

    if concerns:
        params["concerns"] = concerns
        lines.append(
            "OPTIONAL MATCH (c)-[:TARGETS_CONCERN]->(cn:Concern) "
            "WHERE cn.name IN $concerns"
        )
    else:
        lines.append("OPTIONAL MATCH (c)-[:TARGETS_CONCERN]->(cn:Concern) "
                     "WHERE false")   # no concerns → score stays 0

    # collect component product info
    lines.append(
        "OPTIONAL MATCH (c)-[:INCLUDES]->(comp:Product)"
    )

    agg = (
        "WITH c, "
        "count(DISTINCT st) AS skin_score, "
        "collect(DISTINCT st.name) AS matched_skins, "
        "count(DISTINCT cn) AS concern_score, "
        "collect(DISTINCT cn.name) AS matched_concerns, "
        "collect(DISTINCT {sku: comp.sku, title: comp.title, "
        "  image_url: coalesce(comp.image_url,''), "
        "  url: coalesce(comp.url,'')}) AS components"
    )
    lines.append(agg)

    where_parts = [
        "c.active = true",
        "size(components) >= 2",     # only real multi-product combos
    ]
    if max_price is not None:
        params["max_price"] = max_price
        where_parts.append("c.price <= $max_price")

    lines.append("WHERE " + " AND ".join(where_parts))

    lines.append(
        "RETURN c.sku AS sku, c.title AS title, "
        "c.price AS price, coalesce(c.compare_at_price, 0) AS compare_at_price, "
        "coalesce(c.url,'') AS url, coalesce(c.image_url,'') AS image_url, "
        "(skin_score + concern_score) AS match_score, "
        "matched_skins, matched_concerns, components "
        f"ORDER BY match_score DESC, c.price ASC LIMIT {MAX_COMBOS}"
    )

    query = "\n".join(lines)

    try:
        result = graph.query(query, params)
    except Exception:
        return []

    combos = []
    for r in result.result_set:
        # components list may contain nulls if no products matched INCLUDES
        raw_components = r[9] or []
        components = [
            c for c in raw_components
            if isinstance(c, dict) and c.get("sku")
        ]
        if len(components) < 2:
            continue

        combos.append({
            "sku":              r[0],
            "title":            r[1],
            "price":            r[2],
            "compare_at_price": r[3] or 0,
            "url":              r[4],
            "image_url":        r[5],
            "match_score":      r[6],
            "matched_skin_types": r[7] or [],
            "matched_concerns":   r[8] or [],
            "components":       components,
        })

    return combos
