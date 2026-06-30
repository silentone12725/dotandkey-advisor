"""
tests/test_query_intent.py

Tests for backend/query_intent.py: token extraction and query-intent ranking.

All tests use mock product dicts — no FalkorDB connection required.
Each ranking test asserts that the query-matching product outranks others
after rerank_by_query_intent() is applied.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from backend.query_intent import (
    extract_query_tokens,
    compute_query_intent_score,
    rerank_by_query_intent,
)


# =============================================================================
# Helpers
# =============================================================================

def _p(title, *, variant="", ingredients=None, free_from=None,
       description="", skin_score=0, concern_score=0, price=500,
       dna_primary="", cap_scores=None):
    """Minimal product dict for ranking tests."""
    cap_scores = cap_scores or {}
    p = {
        "sku": title[:8].upper().replace(" ", "_"),
        "title": title,
        "variant": variant,
        "ingredients": ingredients or [],
        "free_from": free_from or [],
        "description": description,
        "skin_score": skin_score,
        "concern_score": concern_score,
        "price": price,
        "match_score": skin_score + concern_score,
        "dna_primary": dna_primary,
        "url": "",
    }
    for k, v in cap_scores.items():
        p[f"cap_{k}"] = v
        p[f"cap_{k}_conf"] = 1.0
    return p


# =============================================================================
# 1. Token extraction
# =============================================================================

class TestExtractQueryTokens:

    def test_niacinamide_serum_extracts_niacinamide(self):
        tokens = extract_query_tokens("suggest me a niacinamide serum")
        assert "niacinamide" in tokens
        assert "serum" not in tokens       # category word stripped

    def test_tinted_sunscreen_extracts_tinted(self):
        tokens = extract_query_tokens("suggest me a tinted sunscreen")
        assert "tinted" in tokens
        assert "sunscreen" not in tokens   # category word stripped

    def test_vitamin_c_extracted_as_multi_word(self):
        tokens = extract_query_tokens("vitamin c serum")
        assert "vitamin c" in tokens
        assert "vitamin" not in tokens     # consumed by multi-word match
        assert "c" not in tokens

    def test_fragrance_free_extracted_as_multi_word(self):
        tokens = extract_query_tokens("recommend a fragrance free moisturizer")
        assert "fragrance free" in tokens
        assert "fragrance" not in tokens   # consumed by multi-word match

    def test_ceramide_lip_balm_extracts_both_tokens(self):
        tokens = extract_query_tokens("ceramide lip balm")
        assert "ceramide" in tokens
        assert "lip balm" in tokens

    def test_hyphenated_fragrance_free_normalised(self):
        tokens = extract_query_tokens("fragrance-free moisturizer")
        assert "fragrance free" in tokens

    def test_empty_message_returns_empty(self):
        assert extract_query_tokens("") == []

    def test_generic_filler_yields_no_tokens(self):
        tokens = extract_query_tokens("suggest me something good please")
        assert tokens == []

    def test_peptide_extracted(self):
        tokens = extract_query_tokens("suggest a peptide serum")
        assert "peptide" in tokens

    def test_matte_extracted(self):
        tokens = extract_query_tokens("I want a matte sunscreen")
        assert "matte" in tokens


# =============================================================================
# 2. Query-intent scoring
# =============================================================================

class TestComputeQueryIntentScore:

    def test_title_match_awards_boost(self):
        p = _p("Niacinamide Brightening Serum")
        score = compute_query_intent_score(p, ["niacinamide"])
        assert score >= 100   # at minimum the BOOST_TITLE

    def test_ingredient_match_awards_boost(self):
        p = _p("Brightening Serum", ingredients=["Niacinamide"])
        score = compute_query_intent_score(p, ["niacinamide"])
        assert score >= 100   # BOOST_INGREDIENT

    def test_no_match_scores_zero(self):
        p = _p("Watermelon Cooling Sunscreen")
        score = compute_query_intent_score(p, ["niacinamide"])
        assert score == 0

    def test_free_from_claim_match(self):
        p = _p("Ceramide Moisturizer", free_from=["Fragrance"])
        score = compute_query_intent_score(p, ["fragrance free"])
        assert score >= 80   # BOOST_FREE_FROM

    def test_variant_match_awards_boost(self):
        p = _p("Sunscreen SPF 50", variant="Tinted Beige")
        score = compute_query_intent_score(p, ["tinted"])
        assert score >= 100  # BOOST_VARIANT

    def test_hyphenated_title_normalised_before_matching(self):
        """'Fragrance-Free' in title must match the 'fragrance free' token."""
        p = _p("Fragrance-Free Ceramide Moisturizer")
        score = compute_query_intent_score(p, ["fragrance free"])
        assert score >= 100  # BOOST_TITLE (after hyphen normalisation)

    def test_empty_tokens_scores_zero(self):
        p = _p("Niacinamide Serum", ingredients=["Niacinamide"])
        assert compute_query_intent_score(p, []) == 0


# =============================================================================
# 3. Ranking: the 5 required cases
# =============================================================================

class TestRankingByQueryIntent:

    def test_niacinamide_query_tops_niacinamide_product(self):
        """'niacinamide serum' → Niacinamide product must rank #1."""
        products = [
            _p("Vitamin C Brightening Serum", ingredients=["Ascorbic Acid"]),
            _p("Niacinamide Brightening Serum", ingredients=["Niacinamide"]),
            _p("Hyaluronic Acid Hydrating Serum", ingredients=["Sodium Hyaluronate"]),
        ]
        tokens = extract_query_tokens("suggest me a niacinamide serum")
        assert "niacinamide" in tokens
        ranked = rerank_by_query_intent(products, tokens)
        assert "niacinamide" in ranked[0]["title"].lower(), (
            f"Top result should contain niacinamide. Got: {ranked[0]['title']}"
        )

    def test_tinted_query_tops_tinted_sunscreen(self):
        """'tinted sunscreen' → tinted product must rank #1, even with lower
        skin_score than the plain sunscreens."""
        products = [
            _p("Watermelon Cooling Sunscreen SPF 50", skin_score=2),
            _p("Cica + Niacinamide Sunscreen SPF 50", skin_score=2),
            _p("Strawberry Dew Tinted Sunscreen SPF 50", skin_score=1),
        ]
        tokens = extract_query_tokens("suggest me a tinted sunscreen")
        assert "tinted" in tokens
        ranked = rerank_by_query_intent(products, tokens)
        assert "tinted" in ranked[0]["title"].lower(), (
            f"Top result should be a tinted sunscreen. Got: {ranked[0]['title']}"
        )

    def test_ceramide_lip_balm_tops_ceramide_product(self):
        """'ceramide lip balm' → ceramide+peptide lip balm must rank #1."""
        products = [
            _p("Gloss Boss High Shine Lip Gloss", ingredients=["Jojoba Oil"]),
            _p("Ceramide + Peptide Lip Balm SPF 50", ingredients=["Ceramide", "Peptide"]),
            _p("Watermelon Tinted Lip Balm", ingredients=["Watermelon Extract"]),
        ]
        tokens = extract_query_tokens("ceramide lip balm")
        assert "ceramide" in tokens
        ranked = rerank_by_query_intent(products, tokens)
        assert "ceramide" in ranked[0]["title"].lower(), (
            f"Top result should contain ceramide. Got: {ranked[0]['title']}"
        )

    def test_vitamin_c_query_tops_vitamin_c_product(self):
        """'vitamin c serum' → Vitamin C product must rank above others."""
        products = [
            _p("Niacinamide 10% + Zinc Serum", ingredients=["Niacinamide", "Zinc"]),
            _p("Vitamin C 20% Brightening Serum", ingredients=["Ascorbic Acid", "Vitamin C"]),
            _p("Hyaluronic Acid Hydrating Serum", ingredients=["Sodium Hyaluronate"]),
        ]
        tokens = extract_query_tokens("vitamin c serum")
        assert "vitamin c" in tokens
        ranked = rerank_by_query_intent(products, tokens)
        assert "vitamin c" in ranked[0]["title"].lower(), (
            f"Top result should contain Vitamin C. Got: {ranked[0]['title']}"
        )

    def test_fragrance_free_query_tops_fragrance_free_product(self):
        """'fragrance free moisturizer' → fragrance-free product must rank #1."""
        products = [
            _p("Peptide Face Cream", skin_score=2),
            _p("Ceramide Daily Moisturizer", skin_score=2),
            _p("Barrier Repair Fragrance-Free Moisturizer",
               free_from=["Fragrance"], skin_score=1),
        ]
        tokens = extract_query_tokens("fragrance free moisturizer")
        assert any("fragrance" in t for t in tokens)
        ranked = rerank_by_query_intent(products, tokens)
        top = ranked[0]
        assert top.get("free_from") and any(
            "fragrance" in ff.lower() for ff in top["free_from"]
        ), f"Top result should be fragrance-free. Got: {top['title']}"

    # ── Additional invariant tests ────────────────────────────────────────────

    def test_no_tokens_preserves_original_order(self):
        """Empty query_tokens → list unchanged (Cypher ordering preserved)."""
        products = [
            _p("Product A", skin_score=3),
            _p("Product B", skin_score=2),
            _p("Product C", skin_score=1),
        ]
        original = [p["title"] for p in products]
        ranked = rerank_by_query_intent(products, [])
        assert [p["title"] for p in ranked] == original

    def test_query_intent_overcomes_higher_skin_score(self):
        """Explicit ingredient request must outrank a better profile-matched
        product that doesn't contain the requested ingredient."""
        products = [
            _p("Generic Moisturizing Serum", skin_score=3, concern_score=2),
            _p("Niacinamide 10% Brightening Serum",
               skin_score=1, ingredients=["Niacinamide"]),
        ]
        tokens = extract_query_tokens("niacinamide serum")
        ranked = rerank_by_query_intent(products, tokens)
        assert "niacinamide" in ranked[0]["title"].lower(), (
            "Niacinamide product must outrank the higher skin-score generic serum"
        )

    def test_variant_tinted_match_boosts_product(self):
        """'tinted' in variant field gives the variant boost."""
        products = [
            _p("Watermelon Sunscreen SPF 50", variant=""),
            _p("Strawberry Dew Sunscreen SPF 50", variant="Tinted Rose"),
        ]
        tokens = extract_query_tokens("tinted sunscreen")
        ranked = rerank_by_query_intent(products, tokens)
        assert ranked[0]["variant"] == "Tinted Rose", (
            "Product with 'tinted' in variant should rank first"
        )

    def test_final_score_field_set_on_products(self):
        """rerank_by_query_intent must set 'final_score' on each product."""
        products = [_p("Niacinamide Serum"), _p("Vitamin C Serum")]
        rerank_by_query_intent(products, ["niacinamide"])
        assert all("final_score" in p for p in products)
        assert all("query_intent_score" in p for p in products)

    def test_top3_all_contain_niacinamide(self):
        """When 3 of 5 products contain niacinamide, top 3 must be those 3."""
        products = [
            _p("Niacinamide + Zinc 10% Serum", ingredients=["Niacinamide", "Zinc"]),
            _p("Watermelon Hydrating Serum"),
            _p("Niacinamide Brightening Serum", ingredients=["Niacinamide"]),
            _p("Hyaluronic Acid Serum"),
            _p("Niacinamide Glow Serum", ingredients=["Niacinamide"]),
        ]
        tokens = extract_query_tokens("niacinamide serum")
        ranked = rerank_by_query_intent(products, tokens)
        top3_titles = [p["title"].lower() for p in ranked[:3]]
        assert all("niacinamide" in t for t in top3_titles), (
            f"All top 3 should contain niacinamide. Got: {top3_titles}"
        )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
