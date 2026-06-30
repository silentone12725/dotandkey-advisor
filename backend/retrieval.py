"""
Phase 1 — retrieval queries.

Core query: given a partial user profile, return ALL matching candidate
products from the graph, ranked by match score. The top 3 are marked
as top_picks in the RetrievalResult; the rest are in remaining.

Some filters (season, texture, allergen_free, price) have sparse data
in this catalog, so they're applied as a fallback ladder: try the
strictest query first, progressively relax filters that returned 0
results, and report which filters were dropped so the caller (LLM
playbook) can be honest about it in its response to the user.

Profile shape (all keys optional except category):
{
    "category": "sunscreen",            # required - maps to Category.name
    "skin_types": ["oily", "combination"],
    "concerns": ["acne", "dark_spots"],
    "season": "summer",
    "texture": "lightweight",
    "allergen_free": ["fragrance"],      # list of AllergenClass.name
    "max_price": 600,
}
"""

from dataclasses import dataclass, field

# price_tier → max_price cap (mirrors chip_options.py values + combo_retrieval.py)
PRICE_TIER_TO_MAX: dict[str, float | None] = {
    "under_300":  300.0,
    "under_600":  600.0,
    "under_1000": 1000.0,
    "any":        None,
}

# Ordered list of budget tiers from tightest to most permissive.
# Used by the budget-expansion ladder: if no products exist at the requested
# tier, retrieve() automatically tries the next tier and flags the expansion
# so the caller can display a notice ("Expanded from ₹300 to ₹600").
BUDGET_TIER_LADDER = ["under_300", "under_600", "under_1000", "any"]

# size_pref → (min_g, max_g) range — None = no bound
SIZE_PREF_RANGES: dict[str, tuple[float | None, float | None]] = {
    "travel":   (None, 50.0),
    "standard": (51.0, 150.0),
    "value":    (151.0, None),
}


# Categories where body-care products (sprays, roll-ons, lotions) are a
# mismatch even if they happen to share skin_type/concern tags. The CSV's
# Type column lumps some body items under "Sunscreen", so this can't be
# filtered via the Category edge alone.
FACE_CATEGORIES = {"sunscreen", "moisturizer", "face_wash", "serum",
                   "toner", "mask", "eye_care"}
BODY_TITLE_MARKERS = ["body spray", "body lotion", "body wash",
                      "roll on", "roll-on", "underarm", "body cream"]


@dataclass
class RetrievalResult:
    top_picks: list           # top 3 by match_score — highlighted to user
    remaining: list           # everything else, same order
    dropped_filters: list = field(default_factory=list)
    query_used: str = ""
    # Set when the requested budget tier had no results and retrieval
    # automatically expanded to a looser tier (e.g. under_300 → under_600).
    # Empty string means the requested tier was used without change.
    expanded_budget_tier: str = ""
    # Structured ranking explanations for top_picks (same order).
    # Populated by recommend.py after retrieval if explainability is enabled.
    explanations: list = field(default_factory=list)

    @property
    def all_products(self):
        return self.top_picks + self.remaining

    @property
    def total(self):
        return len(self.top_picks) + len(self.remaining)


def dedupe_top_picks(products: list[dict], limit: int = 3) -> tuple[list, list]:
    """Split a match-score-ranked product list into (top_picks, remaining),
    collapsing same-product variants to a single card.

    Dedup key priority:
      1. url (non-empty) — tint and size variants of the same Shopify product
         share the same URL/handle; collapsing them to one card is correct
         because the lazy-loaded swatch/pill selector already covers variant
         selection.
      2. normalised title — fallback when no URL is stored.

    The first occurrence of each key wins the slot (products are pre-sorted
    match_score DESC, price ASC so this is the best-ranked / cheapest variant).
    Subsequent occurrences are dropped entirely — they must not appear in
    `remaining` either, otherwise a tinted sunscreen with 7 shades would print
    7 identical-looking cards.

    Pure function — no DB dependency, deterministic, directly testable.
    """
    seen_keys: set = set()
    top_picks: list = []
    remaining: list = []
    for p in products:
        url = (p.get("url") or "").strip()
        key = url if url else p["title"].strip().lower()
        if key in seen_keys:
            continue          # duplicate variant — swatch/pill covers selection
        seen_keys.add(key)
        if len(top_picks) < limit:
            top_picks.append(p)
        else:
            remaining.append(p)
    return top_picks, remaining


# -----------------------------------------------------------------------------
# Query builder
# -----------------------------------------------------------------------------

def _build_query(profile, include_season, include_texture,
                 include_allergen, include_price):
    """Build the Cypher + params for a given combination of optional filters.

    Hard rules:
    - category is always a hard MATCH filter
    - season and texture (when included) are hard MATCH filters
    - skin type and concern are OPTIONAL MATCH (scored, not excluded)
    - allergen_free is OPTIONAL MATCH + post-aggregation size check

    FalkorDB requires all hard MATCHes before any OPTIONAL MATCH in the
    same clause chain, hence the ordering below.

    Also always collects ingredient names, texture name, free-from
    allergen names, and matched skin-type/concern names — not just
    counts. This data isn't used for filtering/scoring beyond what it
    already was, but feeds dedupe_top_picks' sibling function
    build_highlights() in recommend.py, which turns it into short
    keyword chips ("Niacinamide", "Sulphate-free") shown under the top
    3 cards — deterministically, without an extra LLM call.

    No LIMIT — we return every matching product so the caller can decide
    how many to surface. Top 3 are marked in retrieve(), not here.
    """

    params = {"category": profile["category"]}

    lines = ["MATCH (p:Product)-[:IN_CATEGORY]->(:Category {name: $category})"]

    # hard MATCH clauses first
    if include_season and profile.get("season"):
        params["season"] = profile["season"]
        lines.append("MATCH (p)-[:BEST_IN_SEASON]->(:Season {name: $season})")

    if include_texture and profile.get("texture"):
        params["texture"] = profile["texture"]
        lines.append("MATCH (p)-[:HAS_TEXTURE]->(:Texture {name: $texture})")

    # optional MATCH clauses (scoring + highlight material — always collected
    # so highlight keywords are available no matter which fallback round wins)
    skin_types = profile.get("skin_types") or []
    if skin_types:
        params["skin_types"] = skin_types
        lines.append(
            "OPTIONAL MATCH (p)-[:SUITS_SKIN_TYPE]->(st:SkinType) "
            "WHERE st.name IN $skin_types"
        )

    concerns = profile.get("concerns") or []
    if concerns:
        params["concerns"] = concerns
        lines.append(
            "OPTIONAL MATCH (p)-[:TARGETS_CONCERN]->(cn:Concern) "
            "WHERE cn.name IN $concerns"
        )

    if include_allergen and profile.get("allergen_free"):
        params["allergens"] = profile["allergen_free"]
        lines.append(
            "OPTIONAL MATCH (p)-[:FREE_FROM]->(af:AllergenClass) "
            "WHERE af.name IN $allergens"
        )

    # always-on collections for highlight keywords (not filters)
    lines.append("OPTIONAL MATCH (p)-[:CONTAINS_INGREDIENT]->(ing:Ingredient)")
    lines.append("OPTIONAL MATCH (p)-[:HAS_TEXTURE]->(disp_tx:Texture)")
    # Unfiltered skin-type collection — unlike matched_skin_types (gated to the
    # profile's requested types), this captures EVERY skin type the product
    # supports, so the UI can detect "suits all skin types" regardless of which
    # single skin type the user has on file. See match_keywords.py's
    # "All Skin Types" chip — without this, a product tagged for all 5 types
    # would only ever show the 1 type that happens to match the user's profile.
    lines.append("OPTIONAL MATCH (p)-[:SUITS_SKIN_TYPE]->(all_st:SkinType)")

    # single aggregation step
    with_parts = ["p"]
    if skin_types:
        with_parts.append("count(DISTINCT st) AS skin_score")
        with_parts.append("collect(DISTINCT st.name) AS matched_skin_types")
    else:
        with_parts.append("0 AS skin_score")
        with_parts.append("[] AS matched_skin_types")

    if concerns:
        with_parts.append("count(DISTINCT cn) AS concern_score")
        with_parts.append("collect(DISTINCT cn.name) AS matched_concerns")
    else:
        with_parts.append("0 AS concern_score")
        with_parts.append("[] AS matched_concerns")

    if include_allergen and profile.get("allergen_free"):
        with_parts.append("collect(DISTINCT af.name) AS free_from")
    else:
        with_parts.append("[] AS free_from")

    with_parts.append("collect(DISTINCT ing.name) AS ingredients")
    with_parts.append("collect(DISTINCT disp_tx.name) AS texture_names")
    with_parts.append("collect(DISTINCT all_st.name) AS all_skin_types")

    lines.append("WITH " + ", ".join(with_parts))

    # post-aggregation WHERE
    where_clauses = ["p.active = true"]

    # Exclude obvious body/area-of-use mismatches when the user is shopping
    # a face-care category. The CSV's Type column lumps "Watermelon Cooling
    # Sunscreen Body Spray" under the same Sunscreen type as face sunscreens,
    # so this can't be filtered via the Category edge alone — title-based
    # exclusion is the pragmatic fix until products get a proper
    # APPLICATION_AREA edge at ingest.
    if profile["category"] in FACE_CATEGORIES:
        for marker in BODY_TITLE_MARKERS:
            where_clauses.append(f"NOT toLower(p.title) CONTAINS '{marker}'")

    if include_allergen and profile.get("allergen_free"):
        n_required = len(profile["allergen_free"])
        where_clauses.append(f"size(free_from) = {n_required}")

    if include_price and profile.get("max_price"):
        params["max_price"] = profile["max_price"]
        where_clauses.append("p.price <= $max_price")

    # size filter — only applies when size_g is populated on product nodes
    # (requires re-ingest with updated csv_to_graph.py)
    size_pref = profile.get("size_pref")
    if size_pref and size_pref in SIZE_PREF_RANGES:
        lo, hi = SIZE_PREF_RANGES[size_pref]
        if lo is not None:
            params["size_lo"] = lo
            where_clauses.append("(p.size_g IS NULL OR p.size_g >= $size_lo)")
        if hi is not None:
            params["size_hi"] = hi
            where_clauses.append("(p.size_g IS NULL OR p.size_g <= $size_hi)")

    lines.append("WHERE " + " AND ".join(where_clauses))

    # Capability score props — coalesce to 0.0 when not yet computed
    _cap_axes = [
        "oil_control", "hydration", "barrier_repair", "brightening",
        "pigmentation", "acne", "pore_care", "sensitivity",
        "sun_protection", "lip_repair",
    ]
    _cap_cols = ", ".join(
        f"coalesce(p.cap_{ax}, 0.0) AS cap_{ax}" for ax in _cap_axes
    )
    lines.append(
        "RETURN p.sku AS sku, p.title AS title, p.price AS price, "
        "p.category_raw AS category_raw, p.description AS description, "
        "(skin_score + concern_score) AS match_score, "
        "coalesce(p.url, '') AS url, coalesce(p.image_url, '') AS image_url, "
        "matched_skin_types, matched_concerns, free_from, ingredients, "
        "texture_names, coalesce(p.compare_at_price, 0) AS compare_at_price, "
        f"coalesce(p.variant, '') AS variant, skin_score, concern_score, {_cap_cols}, "
        "all_skin_types"
    )
    lines.append("ORDER BY match_score DESC, p.price ASC")
    # no LIMIT — return all matches

    return "\n".join(lines), params


# -----------------------------------------------------------------------------
# Fallback ladder
# -----------------------------------------------------------------------------

_CAP_AXES = [
    "oil_control", "hydration", "barrier_repair", "brightening",
    "pigmentation", "acne", "pore_care", "sensitivity",
    "sun_protection", "lip_repair",
]
_CAP_BASE_IDX = 17   # first capability column index in result row


def _rows_to_products(rows: list) -> list[dict]:
    """Convert raw Cypher result rows into product dicts.

    Columns 0-16 are fixed fields; columns 17+ are cap_* props.
    """
    products = []
    for r in rows:
        p = {
            "sku":          r[0],
            "title":        r[1],
            "price":        r[2],
            "category_raw": r[3],
            "description":  r[4][:400] if r[4] else "",
            "match_score":  r[5],
            "url":          r[6] if len(r) > 6 else "",
            "image_url":    r[7] if len(r) > 7 else "",
            "matched_skin_types": (r[8] or []) if len(r) > 8 else [],
            "matched_concerns":   (r[9] or []) if len(r) > 9 else [],
            "free_from":          (r[10] or []) if len(r) > 10 else [],
            "ingredients":        (r[11] or []) if len(r) > 11 else [],
            "texture":            (r[12][0] if r[12] else "") if len(r) > 12 else "",
            "compare_at_price":   (r[13] or 0) if len(r) > 13 else 0,
            "variant":            r[14] if len(r) > 14 else "",
            "skin_score":         int(r[15]) if len(r) > 15 else 0,
            "concern_score":      int(r[16]) if len(r) > 16 else 0,
        }
        # Capability scores (columns 17-26)
        for i, ax in enumerate(_CAP_AXES):
            idx = _CAP_BASE_IDX + i
            p[f"cap_{ax}"] = float(r[idx] or 0.0) if len(r) > idx else 0.0
        # Unfiltered skin-type support (column 27, right after cap scores)
        all_skin_idx = _CAP_BASE_IDX + len(_CAP_AXES)
        p["all_skin_types"] = (r[all_skin_idx] or []) if len(r) > all_skin_idx else []
        products.append(p)
    return products


def _run_relaxation_rounds(
    graph, profile: dict, include_price: bool
) -> tuple[list, list, str]:
    """Run the season → texture → allergen relaxation ladder at a fixed price setting.

    Returns (products, dropped_filters, last_query).  If include_price=True, the
    price filter is always active; caller owns the budget-tier loop.  If
    include_price=False, price is never applied (final fallback round).
    """
    rounds = [
        {"season": True,  "texture": True,  "allergen": True},
        {"season": False, "texture": True,  "allergen": True},
        {"season": False, "texture": False, "allergen": True},
        {"season": False, "texture": False, "allergen": False},
    ]
    dropped: list[str] = []
    last_query = ""

    for i, flags in enumerate(rounds):
        query, params = _build_query(
            profile,
            include_season=flags["season"],
            include_texture=flags["texture"],
            include_allergen=flags["allergen"],
            include_price=include_price,
        )
        last_query = query
        try:
            rows = graph.query(query, params).result_set
        except Exception:
            # Graph unavailable (timeout, connection error, etc.) — degrade
            # gracefully by returning empty rather than propagating the exception.
            return [], dropped, last_query

        if rows:
            return _rows_to_products(rows), dropped, query

        if i == 0 and profile.get("season"):
            dropped.append("season")
        elif i == 1 and profile.get("texture"):
            dropped.append("texture")
        elif i == 2 and profile.get("allergen_free"):
            dropped.append("allergen_free")

    return [], dropped, last_query


import logging as _logging
_log = _logging.getLogger(__name__)

from backend.query_intent import extract_query_tokens, rerank_by_query_intent
from backend.sensitivity_memory import apply_sensitivity_ranking
from backend.behavioral_learning import apply_behavioral_ranking


def retrieve(graph, profile, user_message: str = "", vague_tokens: "list[str] | tuple[str, ...]" = (), sensitivity: dict = {}, behavioral_prefs: dict = {}) -> RetrievalResult:
    """Run the profile-match query against FalkorDB.

    When a budget tier is set (price_tier field), tries the requested tier
    first, then automatically expands to the next tier if no results are
    found (under_300 → under_600 → under_1000 → any).  Sets
    RetrievalResult.expanded_budget_tier to the tier actually used when it
    differs from the requested tier, so callers can display a notice.

    Within each budget tier, relaxes non-price filters in this order:
      season → texture → allergen_free
    The price filter is only fully dropped after all budget tiers fail.

    When price_tier is absent (profile has only max_price or no price info),
    falls back to the original 5-round ladder for full backward compatibility.
    """
    requested_tier = (profile.get("price_tier") or "").strip()
    _base = extract_query_tokens(user_message)
    # Merge Tier-3 LLM tokens (vague_tokens) without duplicating exact tokens
    _seen = set(_base)
    query_tokens = _base + [t for t in vague_tokens if t not in _seen]

    # ── Budget-tier expansion path ──────────────────────────────────────────
    if requested_tier in BUDGET_TIER_LADDER:
        start_idx = BUDGET_TIER_LADDER.index(requested_tier)
        tiers_to_try = BUDGET_TIER_LADDER[start_idx:]

        for tier in tiers_to_try:
            tier_profile = {
                **profile,
                "price_tier": tier,
                "max_price":  PRICE_TIER_TO_MAX[tier],
            }
            products, dropped, query = _run_relaxation_rounds(
                graph, tier_profile, include_price=True
            )

            _log.debug(
                "BUDGET_DEBUG | requested=%s | tried=%s | found=%d | dropped=%s",
                requested_tier, tier, len(products), dropped,
            )

            if products:
                expanded = tier if tier != requested_tier else ""
                products = rerank_by_query_intent(products, query_tokens)
                products = apply_sensitivity_ranking(products, sensitivity)
                products = apply_behavioral_ranking(products, behavioral_prefs)
                top_picks, remaining = dedupe_top_picks(products, limit=3)
                _log.debug(
                    "BUDGET_DEBUG | final tier=%s | top_picks=%d | remaining=%d",
                    tier, len(top_picks), len(remaining),
                )
                return RetrievalResult(
                    top_picks=top_picks,
                    remaining=remaining,
                    dropped_filters=dropped,
                    query_used=query,
                    expanded_budget_tier=expanded,
                )

        # All budget tiers exhausted — drop price filter entirely as last resort
        products, dropped, query = _run_relaxation_rounds(
            graph, profile, include_price=False
        )
        if products:
            products = rerank_by_query_intent(products, query_tokens)
            products = apply_sensitivity_ranking(products, sensitivity)
            top_picks, remaining = dedupe_top_picks(products, limit=3)
            return RetrievalResult(
                top_picks=top_picks,
                remaining=remaining,
                dropped_filters=dropped + ["max_price"],
                query_used=query,
                expanded_budget_tier="any",
            )
        return RetrievalResult(top_picks=[], remaining=[], dropped_filters=dropped, query_used=query)

    # ── Legacy path (no price_tier set) — original 5-round ladder ──────────
    rounds = [
        {"season": True,  "texture": True,  "allergen": True,  "price": True},
        {"season": False, "texture": True,  "allergen": True,  "price": True},
        {"season": False, "texture": False, "allergen": True,  "price": True},
        {"season": False, "texture": False, "allergen": False, "price": True},
        {"season": False, "texture": False, "allergen": False, "price": False},
    ]
    dropped: list[str] = []
    last_query = ""

    for i, flags in enumerate(rounds):
        query, params = _build_query(
            profile,
            include_season=flags["season"],
            include_texture=flags["texture"],
            include_allergen=flags["allergen"],
            include_price=flags["price"],
        )
        last_query = query
        try:
            rows = graph.query(query, params).result_set
        except Exception:
            return RetrievalResult(top_picks=[], remaining=[], dropped_filters=dropped,
                                   query_used=last_query)

        if rows:
            products = rerank_by_query_intent(_rows_to_products(rows), query_tokens)
            products = apply_sensitivity_ranking(products, sensitivity)
            top_picks, remaining = dedupe_top_picks(products, limit=3)
            return RetrievalResult(
                top_picks=top_picks,
                remaining=remaining,
                dropped_filters=dropped,
                query_used=query,
            )

        if i == 0 and profile.get("season"):
            dropped.append("season")
        elif i == 1 and profile.get("texture"):
            dropped.append("texture")
        elif i == 2 and profile.get("allergen_free"):
            dropped.append("allergen_free")
        elif i == 3 and profile.get("max_price"):
            dropped.append("max_price")

    return RetrievalResult(
        top_picks=[], remaining=[], dropped_filters=dropped, query_used=last_query
    )


# -----------------------------------------------------------------------------
# CLI test harness
# -----------------------------------------------------------------------------

def _print_result(profile, result):
    import json
    print("=" * 70)
    print("PROFILE:", json.dumps(profile, indent=2))
    print(f"dropped filters : {result.dropped_filters}")
    print(f"total results   : {result.total}")

    if result.top_picks:
        print("\n  ★ TOP PICKS")
        for p in result.top_picks:
            print(f"    [{p['match_score']}] {p['sku']:16s} ₹{p['price']:<7} {p['title']}")

    if result.remaining:
        print("\n  · ALL OTHERS")
        for p in result.remaining:
            print(f"    [{p['match_score']}] {p['sku']:16s} ₹{p['price']:<7} {p['title']}")

    print()


if __name__ == "__main__":
    import argparse
    from falkordb import FalkorDB

    parser = argparse.ArgumentParser()
    parser.add_argument("--host",  default="localhost")
    parser.add_argument("--port",  type=int, default=6379)
    parser.add_argument("--graph", default="dotandkey")
    args = parser.parse_args()

    db    = FalkorDB(host=args.host, port=args.port)
    graph = db.select_graph(args.graph)

    test_profiles = [
        {
            "category":    "sunscreen",
            "skin_types":  ["oily", "combination"],
            "concerns":    ["acne", "excess_oil"],
            "season":      "monsoon",
            "allergen_free": ["fragrance"],
            "max_price":   600,
        },
        {
            "category":   "moisturizer",
            "skin_types": ["dry"],
            "concerns":   ["dryness", "dullness"],
        },
        {
            "category":    "serum",
            "skin_types":  ["sensitive"],
            "concerns":    ["redness_irritation"],
            "allergen_free": ["fragrance", "alcohol"],
        },
    ]

    for profile in test_profiles:
        _print_result(profile, retrieve(graph, profile))