"""
tests/test_retrieval.py

Tests for backend/retrieval.py. Split into three groups:

1. dedupe_top_picks() — pure function, no DB needed
2. _build_query() — Cypher string assembly, asserts on generated text
3. retrieve() fallback ladder — uses a FakeGraph stub instead of a real
   FalkorDB connection, so these run without docker/network.

These two specific regressions are covered explicitly because they were
caught in live testing, not hypothetically:
  - same product title appearing twice in top_picks (pack-size variants)
  - body-care products (sprays, roll-ons) surfacing for face categories
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from backend.retrieval import (
    RetrievalResult,
    dedupe_top_picks,
    _build_query,
    retrieve,
    FACE_CATEGORIES,
    BODY_TITLE_MARKERS,
)


# =============================================================================
# 1. dedupe_top_picks() — pure function tests
# =============================================================================

class TestDedupeTopPicks:

    def test_no_duplicates_passthrough(self):
        """Three distinct titles — all should land in top_picks unchanged."""
        products = [
            {"sku": "A", "title": "Cica Sunscreen", "price": 445, "match_score": 3},
            {"sku": "B", "title": "Watermelon Sunscreen", "price": 445, "match_score": 2},
            {"sku": "C", "title": "Vitamin C Sunscreen", "price": 445, "match_score": 1},
        ]
        top, rest = dedupe_top_picks(products, limit=3)
        assert len(top) == 3
        assert len(rest) == 0
        assert [p["sku"] for p in top] == ["A", "B", "C"]

    def test_duplicate_title_dropped_not_pushed_to_remaining(self):
        """Same title, two SKUs (size variants of the same product).
        First occurrence wins; the duplicate is dropped entirely — NOT added
        to remaining — because the card's swatch/pill selector covers variant
        selection without needing a second identical-looking card."""
        products = [
            {"sku": "DK_CCMS", "title": "Cica + Niacinamide Sunscreen", "price": 445, "match_score": 3},
            {"sku": "DK_CCNS", "title": "Cica + Niacinamide Sunscreen", "price": 595, "match_score": 3},
            {"sku": "DK_WMCS", "title": "Watermelon Cooling Sunscreen", "price": 445, "match_score": 2},
            {"sku": "DK_VCES", "title": "Vitamin C + E Sunscreen", "price": 445, "match_score": 2},
        ]
        top, rest = dedupe_top_picks(products, limit=3)

        top_titles = [p["title"].lower() for p in top]
        assert len(top_titles) == len(set(top_titles)), "top_picks must have unique titles"
        assert len(top) == 3
        assert top[0]["sku"] == "DK_CCMS"              # cheaper/best-ranked variant wins
        assert "DK_CCNS" not in [p["sku"] for p in rest]  # duplicate dropped, not in remaining

    def test_case_insensitive_title_match(self):
        """Title comparison must be case-insensitive — 'Cica Sunscreen' and
        'CICA SUNSCREEN' are the same product for dedup purposes.
        The duplicate is dropped entirely, not pushed to remaining."""
        products = [
            {"sku": "A", "title": "Cica Sunscreen", "price": 445, "match_score": 2},
            {"sku": "B", "title": "CICA SUNSCREEN", "price": 595, "match_score": 2},
        ]
        top, rest = dedupe_top_picks(products, limit=3)
        assert len(top) == 1
        assert len(rest) == 0

    def test_whitespace_insensitive_title_match(self):
        """Trailing/leading whitespace shouldn't create false-distinct titles.
        The duplicate is dropped entirely, not pushed to remaining."""
        products = [
            {"sku": "A", "title": "Cica Sunscreen", "price": 445, "match_score": 2},
            {"sku": "B", "title": "  Cica Sunscreen  ", "price": 595, "match_score": 2},
        ]
        top, rest = dedupe_top_picks(products, limit=3)
        assert len(top) == 1
        assert len(rest) == 0

    def test_limit_respected_with_many_unique_titles(self):
        """More than `limit` unique titles — only `limit` go to top_picks,
        rest go to remaining, none dropped entirely."""
        products = [
            {"sku": str(i), "title": f"Product {i}", "price": 100, "match_score": 10 - i}
            for i in range(7)
        ]
        top, rest = dedupe_top_picks(products, limit=3)
        assert len(top) == 3
        assert len(rest) == 4
        assert len(top) + len(rest) == len(products)   # nothing lost

    def test_empty_input(self):
        top, rest = dedupe_top_picks([], limit=3)
        assert top == []
        assert rest == []

    def test_three_way_duplicate(self):
        """Three SKUs sharing one title (e.g. three tint shades of the same product) —
        only the first goes to top_picks; the other two are dropped entirely."""
        products = [
            {"sku": "A", "title": "Multi Pack Sunscreen", "price": 300, "match_score": 5},
            {"sku": "B", "title": "Multi Pack Sunscreen", "price": 500, "match_score": 5},
            {"sku": "C", "title": "Multi Pack Sunscreen", "price": 700, "match_score": 5},
        ]
        top, rest = dedupe_top_picks(products, limit=3)
        assert len(top) == 1
        assert len(rest) == 0

    def test_url_dedup_collapses_tint_variants(self):
        """Seven tint variants of the same product share the same URL.
        Only the first should appear; the other six are dropped.
        This is the exact Strawberry Dew Tinted Sunscreen regression."""
        shades = [
            {"sku": f"DK_STRAW_{s}", "title": "Strawberry Dew Tinted Sunscreen SPF 50+",
             "price": 549, "match_score": 3,
             "url": "/products/strawberry-dew-tinted-sunscreen-spf-50-pa"}
            for s in ["PEONY", "ROSE", "IVORY", "SAND", "CARAMEL", "BEIGE", "DEEP"]
        ]
        top, rest = dedupe_top_picks(shades, limit=3)
        assert len(top) == 1
        assert len(rest) == 0
        assert top[0]["sku"] == "DK_STRAW_PEONY"

    def test_url_dedup_keeps_different_url_products(self):
        """Two products with different URLs but the same title (unlikely but
        possible) are treated as distinct and both kept."""
        products = [
            {"sku": "A", "title": "Vitamin C Serum", "price": 445, "match_score": 3,
             "url": "/products/vitamin-c-serum-30ml"},
            {"sku": "B", "title": "Vitamin C Serum", "price": 595, "match_score": 2,
             "url": "/products/vitamin-c-serum-50ml"},
        ]
        top, rest = dedupe_top_picks(products, limit=3)
        assert len(top) == 2
        assert len(rest) == 0


# =============================================================================
# 2. _build_query() — Cypher assembly tests
# =============================================================================

class TestBuildQuery:

    def test_category_is_always_present(self):
        profile = {"category": "sunscreen"}
        q, params = _build_query(profile, False, False, False, False)
        assert "IN_CATEGORY" in q
        assert params["category"] == "sunscreen"

    def test_body_exclusion_applied_for_face_categories(self):
        """Regression test: sunscreen is a FACE_CATEGORIES member, so the
        query must exclude body-product title markers."""
        profile = {"category": "sunscreen"}
        q, _ = _build_query(profile, False, False, False, False)
        for marker in BODY_TITLE_MARKERS:
            assert marker in q, f"missing body exclusion for: {marker}"
        assert "body spray" in q
        assert "Sunscreen" in profile["category"].title() or True  # sanity no-op

    def test_body_exclusion_not_applied_for_body_care_category(self):
        """body_care category should NOT exclude body products — that
        would filter out everything."""
        profile = {"category": "body_care"}
        q, _ = _build_query(profile, False, False, False, False)
        assert "body spray" not in q
        assert "roll on" not in q

    def test_all_face_categories_get_exclusion(self):
        for cat in FACE_CATEGORIES:
            profile = {"category": cat}
            q, _ = _build_query(profile, False, False, False, False)
            assert "NOT toLower(p.title) CONTAINS 'body spray'" in q, f"failed for {cat}"

    def test_no_limit_in_query(self):
        """retrieval.py deliberately returns ALL matches, not top-N —
        ranking/truncation happens in dedupe_top_picks(), not Cypher."""
        profile = {"category": "sunscreen"}
        q, _ = _build_query(profile, True, True, True, True)
        assert "LIMIT" not in q

    def test_season_filter_only_when_flagged(self):
        profile = {"category": "sunscreen", "season": "monsoon"}
        q_with, _ = _build_query(profile, True, False, False, False)
        q_without, _ = _build_query(profile, False, False, False, False)
        assert "BEST_IN_SEASON" in q_with
        assert "BEST_IN_SEASON" not in q_without

    def test_allergen_requires_exact_count_match(self):
        """size(free_from) = N — product must be free of ALL requested
        allergens, not just one of them."""
        profile = {"category": "sunscreen", "allergen_free": ["fragrance", "alcohol"]}
        q, params = _build_query(profile, False, False, True, False)
        assert "size(free_from) = 2" in q
        assert params["allergens"] == ["fragrance", "alcohol"]

    def test_price_filter_only_when_flagged(self):
        profile = {"category": "sunscreen", "max_price": 500}
        q_with, params = _build_query(profile, False, False, False, True)
        q_without, _ = _build_query(profile, False, False, False, False)
        assert "p.price <= $max_price" in q_with
        assert params["max_price"] == 500
        assert "max_price" not in q_without

    def test_match_clauses_precede_optional_matches(self):
        """FalkorDB requires a WITH between any OPTIONAL MATCH and a
        subsequent MATCH. This was a live bug — regression guard."""
        profile = {
            "category": "sunscreen",
            "season": "summer",
            "texture": "lightweight",
            "skin_types": ["oily"],
            "concerns": ["acne"],
        }
        q, _ = _build_query(profile, True, True, True, True)
        lines = [l for l in q.split("\n") if l.startswith("MATCH") or l.startswith("OPTIONAL MATCH")]
        seen_optional = False
        for line in lines:
            if line.startswith("OPTIONAL MATCH"):
                seen_optional = True
            elif line.startswith("MATCH") and seen_optional:
                pytest.fail(f"hard MATCH found after OPTIONAL MATCH: {line}")


# =============================================================================
# 3. retrieve() fallback ladder — using a FakeGraph stub
# =============================================================================

class FakeResult:
    def __init__(self, rows):
        self.result_set = rows


class FakeGraph:
    """Stub that mimics FalkorDB's graph.query() interface.

    `responses` is a list of row-lists, consumed in order — one per
    query() call. This lets a test script exactly which round of the
    fallback ladder should "find" results, without touching a real DB.
    """
    def __init__(self, responses):
        self.responses = list(responses)
        self.queries_seen = []

    def query(self, cypher, params=None):
        self.queries_seen.append((cypher, params))
        if self.responses:
            rows = self.responses.pop(0)
        else:
            rows = []
        return FakeResult(rows)


def _row(sku, title, price, score, url="", image_url="",
        matched_skin_types=None, matched_concerns=None,
        free_from=None, ingredients=None, texture_names=None,
        compare_at_price=0, variant=""):
    return (sku, title, price, "Sunscreen", "a description here", score,
            url, image_url,
            matched_skin_types or [], matched_concerns or [],
            free_from or [], ingredients or [], texture_names or [],
            compare_at_price, variant)


class TestRetrieveFallbackLadder:

    def test_matched_attributes_map_through_for_highlights(self):
        """Regression test: the highlight-keyword feature needs matched
        concern/skin-type names, ingredients, free_from claims, and
        texture — confirm these survive the row->dict mapping intact."""
        graph = FakeGraph(responses=[
            [_row("A", "Cica Sunscreen", 445, 3,
                  matched_skin_types=["oily"],
                  matched_concerns=["acne", "excess_oil"],
                  free_from=["fragrance"],
                  ingredients=["cica", "niacinamide"],
                  texture_names=["lightweight"])],
        ])
        result = retrieve(graph, {"category": "sunscreen"})
        p = result.top_picks[0]
        assert p["matched_skin_types"] == ["oily"]
        assert set(p["matched_concerns"]) == {"acne", "excess_oil"}
        assert p["free_from"] == ["fragrance"]
        assert set(p["ingredients"]) == {"cica", "niacinamide"}
        assert p["texture"] == "lightweight"

    def test_missing_matched_attributes_default_safely(self):
        """A 6-column legacy-shaped row (no highlight columns) must not
        crash — all new fields should default to empty."""
        graph = FakeGraph(responses=[[("A", "Cica Sunscreen", 445,
                                       "Sunscreen", "desc", 3)]])
        result = retrieve(graph, {"category": "sunscreen"})
        p = result.top_picks[0]
        assert p["matched_skin_types"] == []
        assert p["matched_concerns"] == []
        assert p["free_from"] == []
        assert p["ingredients"] == []
        assert p["texture"] == ""

    def test_empty_texture_names_list_yields_empty_string(self):
        graph = FakeGraph(responses=[[_row("A", "X", 445, 1, texture_names=[])]])
        result = retrieve(graph, {"category": "sunscreen"})
        assert result.top_picks[0]["texture"] == ""

    def test_compare_at_price_maps_through(self):
        """Needed for strikethrough-price display matching the real
        dotandkey.com card style (e.g. ₹476 struck ₹595)."""
        graph = FakeGraph(responses=[[_row("A", "X", 445, 1, compare_at_price=595)]])
        result = retrieve(graph, {"category": "sunscreen"})
        assert result.top_picks[0]["compare_at_price"] == 595

    def test_url_and_image_url_map_through(self):
        """Regression test: product cards need a real product_url and
        image_url to render hyperlinked cards with actual images —
        confirm these fields survive the row->dict mapping."""
        graph = FakeGraph(responses=[
            [_row("A", "Cica Sunscreen", 445, 3,
                  url="https://www.dotandkey.com/products/cica-sunscreen",
                  image_url="https://cdn.shopify.com/files/cica.jpg")],
        ])
        profile = {"category": "sunscreen"}
        result = retrieve(graph, profile)
        assert result.top_picks[0]["url"] == "https://www.dotandkey.com/products/cica-sunscreen"
        assert result.top_picks[0]["image_url"] == "https://cdn.shopify.com/files/cica.jpg"

    def test_missing_url_and_image_url_default_to_empty_string(self):
        """Products that haven't been through the enrichment script yet
        (no url/image_url set on the node) must not crash — the Cypher
        query's coalesce() handles the NULL case, this confirms the
        Python-side mapping does too."""
        graph = FakeGraph(responses=[[_row("A", "Cica Sunscreen", 445, 3)]])
        result = retrieve(graph, {"category": "sunscreen"})
        assert result.top_picks[0]["url"] == ""
        assert result.top_picks[0]["image_url"] == ""

    def test_variant_shade_name_maps_through(self):
        """Regression: p.variant (shade name, e.g. 'Plush Pink') was not in
        the RETURN clause and therefore never reached the frontend. The widget
        was pre-selecting Shopify's first variant instead of the recommended
        shade. Confirm the field survives the row->dict mapping."""
        graph = FakeGraph(responses=[
            [_row("DK_BRPP", "Ceramide Lip Balm", 249, 3,
                  url="/products/spf-50-barrier-repair-lip-balm",
                  variant="Plush Pink")],
        ])
        result = retrieve(graph, {"category": "lip_care"})
        assert result.top_picks[0]["variant"] == "Plush Pink"

    def test_variant_defaults_to_empty_string_when_absent(self):
        """Products without a variant property (non-tinted, single SKU) must
        not crash — the Cypher coalesce() and Python-side len() guard both
        handle the missing column."""
        graph = FakeGraph(responses=[[_row("A", "Cica Sunscreen", 445, 3)]])
        result = retrieve(graph, {"category": "sunscreen"})
        assert result.top_picks[0]["variant"] == ""

    def test_first_round_success_no_filters_dropped(self):
        """If the strictest query returns results immediately, no filters
        should be reported as dropped."""
        graph = FakeGraph(responses=[
            [_row("A", "Product A", 445, 3)],
        ])
        profile = {
            "category": "sunscreen", "skin_types": ["oily"], "concerns": ["acne"],
            "season": "monsoon", "allergen_free": ["fragrance"], "max_price": 600,
        }
        result = retrieve(graph, profile)
        assert result.dropped_filters == []
        assert result.total == 1
        assert len(graph.queries_seen) == 1   # only one query needed

    def test_season_dropped_when_first_round_empty(self):
        """Round 1 (with season) returns nothing -> round 2 (no season)
        succeeds -> 'season' must be reported as dropped."""
        graph = FakeGraph(responses=[
            [],                                    # round 1: season included, empty
            [_row("A", "Product A", 445, 3)],       # round 2: season dropped, found
        ])
        profile = {
            "category": "sunscreen", "skin_types": ["oily"], "concerns": ["acne"],
            "season": "monsoon",
        }
        result = retrieve(graph, profile)
        assert result.dropped_filters == ["season"]
        assert result.total == 1

    def test_multiple_filters_dropped_in_order(self):
        """season, then texture, then allergen all empty before price-only
        round succeeds -> all three reported as dropped, in that order."""
        graph = FakeGraph(responses=[
            [],   # round 1: season+texture+allergen+price
            [],   # round 2: texture+allergen+price
            [],   # round 3: allergen+price
            [_row("A", "Product A", 445, 1)],   # round 4: price only
        ])
        profile = {
            "category": "sunscreen", "skin_types": ["oily"],
            "season": "monsoon", "texture": "lightweight",
            "allergen_free": ["fragrance"], "max_price": 600,
        }
        result = retrieve(graph, profile)
        assert result.dropped_filters == ["season", "texture", "allergen_free"]
        assert result.total == 1

    def test_all_rounds_empty_returns_empty_result(self):
        """Even the most relaxed query (category only) finds nothing —
        should return an empty RetrievalResult, not raise."""
        graph = FakeGraph(responses=[[], [], [], [], []])
        profile = {"category": "sunscreen", "season": "monsoon"}
        result = retrieve(graph, profile)
        assert result.total == 0
        assert result.top_picks == []
        assert result.remaining == []

    def test_dedup_applied_within_fallback_result(self):
        """The fallback ladder's successful round still runs through
        dedupe_top_picks — duplicate titles shouldn't leak into top_picks,
        and the duplicate variant is dropped (not pushed to remaining)."""
        graph = FakeGraph(responses=[
            [
                _row("A", "Cica Sunscreen", 445, 3),
                _row("B", "Cica Sunscreen", 595, 3),   # duplicate — dropped
                _row("C", "Watermelon Sunscreen", 445, 2),
                _row("D", "Vitamin C Sunscreen", 445, 1),
            ],
        ])
        profile = {"category": "sunscreen", "skin_types": ["oily"], "concerns": ["acne"]}
        result = retrieve(graph, profile)
        top_titles = [p["title"].lower() for p in result.top_picks]
        assert len(top_titles) == len(set(top_titles))
        assert result.total == 3   # B dropped as duplicate of A


class TestBudgetTierExpansion:

    def test_budget_expansion_ladder_under_300_to_600(self):
        """When under_300 finds nothing, retrieval should automatically
        expand to under_600 and set expanded_budget_tier."""
        graph = FakeGraph(responses=[
            [], [], [], [],   # 4 rounds at under_300 → empty
            [_row("A", "Cica Sunscreen", 550, 3)],  # round 1 at under_600 → found
        ])
        profile = {
            "category": "sunscreen", "skin_types": ["oily"],
            "price_tier": "under_300", "max_price": 300,
        }
        result = retrieve(graph, profile)
        assert result.total == 1
        assert result.expanded_budget_tier == "under_600", (
            "retrieval must expand to under_600 and record the expansion"
        )

    def test_no_expansion_when_requested_tier_has_results(self):
        """When the requested tier finds results, expanded_budget_tier is empty."""
        graph = FakeGraph(responses=[
            [_row("A", "Cica Sunscreen", 250, 3)],  # under_300 → found immediately
        ])
        profile = {
            "category": "sunscreen", "skin_types": ["oily"],
            "price_tier": "under_300", "max_price": 300,
        }
        result = retrieve(graph, profile)
        assert result.total == 1
        assert result.expanded_budget_tier == "", (
            "expanded_budget_tier must be empty when requested tier has results"
        )

    def test_budget_expansion_skips_to_any_when_all_tiers_fail(self):
        """When under_300, under_600, and under_1000 all return empty,
        retrieval falls back to the no-price-filter round and returns products."""
        # 4 rounds × 3 explicit tiers = 12 empty responses, then 1 result with no price
        graph = FakeGraph(responses=[[], [], [], []] * 3 + [[_row("A", "X", 1500, 1)]])
        profile = {
            "category": "sunscreen", "skin_types": ["oily"],
            "price_tier": "under_300", "max_price": 300,
        }
        result = retrieve(graph, profile)
        assert result.total == 1
        assert result.expanded_budget_tier == "any"

    def test_legacy_path_no_price_tier_unchanged(self):
        """Profiles without price_tier use the original 5-round ladder
        (backward-compatible path)."""
        graph = FakeGraph(responses=[
            [],                                    # round 1 empty
            [_row("A", "Product A", 445, 3)],       # round 2 succeeds
        ])
        profile = {"category": "sunscreen", "skin_types": ["oily"], "season": "monsoon"}
        result = retrieve(graph, profile)
        assert result.total == 1
        assert result.expanded_budget_tier == ""   # legacy path never sets this


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))