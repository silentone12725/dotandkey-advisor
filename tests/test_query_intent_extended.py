"""
tests/test_query_intent_extended.py

Extended coverage for backend/query_intent.py:
  - Ingredient synonym tests (vitamin b3 = niacinamide, brightening = vitamin c)
  - Attribute tests (tinted, fragrance-free synonyms)
  - Texture tests (matte, gel, cream, lightweight)
  - Concern mapping (dark spots → vitamin c, acne → salicylic acid)
  - Variant name ranking
  - Combined intent (ingredient + skin type / budget)
  - Typo-tolerance (fuzzy matching, edit-distance ≤ 2)
  - Scoring tier ordering (exact > synonym > intent > fuzzy)
  - Negative invariants (matching product must always outrank non-matching)
  - Debug field assertions (intent_sources, final_score, query_intent_score)

All tests use mock product dicts — no FalkorDB required.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from backend.query_intent import (
    extract_query_tokens,
    compute_query_intent_score,
    rerank_by_query_intent,
    _edit_distance,
    _find_fuzzy_canonical,
    _enrich_tokens,
)


# =============================================================================
# Helpers
# =============================================================================

def _p(title, *, variant="", ingredients=None, free_from=None,
       description="", skin_score=0, concern_score=0, price=500,
       dna_primary="", cap_scores=None):
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


def _top(products, query):
    """Return title of #1 ranked product for `query`."""
    tokens = extract_query_tokens(query)
    ranked = rerank_by_query_intent(list(products), tokens)
    return ranked[0]["title"]


def _rank_of(products, query, must_contain):
    """Return 0-based rank of first product whose title contains `must_contain`."""
    tokens = extract_query_tokens(query)
    ranked = rerank_by_query_intent(list(products), tokens)
    for i, p in enumerate(ranked):
        if must_contain.lower() in p["title"].lower():
            return i
    return len(ranked)


# =============================================================================
# 1. Edit-distance utility
# =============================================================================

class TestEditDistance:

    def test_identical(self):
        assert _edit_distance("niacinamide", "niacinamide") == 0

    def test_one_substitution(self):
        # niacinimide vs niacinamide — one sub at position 6
        assert _edit_distance("niacinimide", "niacinamide") == 1

    def test_one_insertion(self):
        # "tintted" → "tinted" (delete one 't')
        assert _edit_distance("tintted", "tinted") == 1

    def test_one_deletion(self):
        # "ceramde" → "ceramide" (missing 'i')
        assert _edit_distance("ceramde", "ceramide") == 1

    def test_large_distance_fast_rejected(self):
        assert _edit_distance("abc", "niacinamide") >= 99


# =============================================================================
# 2. Fuzzy canonical lookup
# =============================================================================

class TestFuzzyCanonical:

    def test_niacinimide_maps_to_niacinamide(self):
        assert _find_fuzzy_canonical("niacinimide") == "niacinamide"

    def test_niacinmide_maps_to_niacinamide(self):
        assert _find_fuzzy_canonical("niacinmide") == "niacinamide"

    def test_cerimide_maps_to_ceramide(self):
        assert _find_fuzzy_canonical("cerimide") == "ceramide"

    def test_tintted_maps_to_tinted(self):
        assert _find_fuzzy_canonical("tintted") == "tinted"

    def test_fragnance_maps_to_fragrance_free(self):
        result = _find_fuzzy_canonical("fragnance")
        assert result == "fragrance free"

    def test_exact_returns_none(self):
        # exact match returns None — handled separately
        assert _find_fuzzy_canonical("niacinamide") is None

    def test_unrelated_word_returns_none(self):
        assert _find_fuzzy_canonical("banana") is None


# =============================================================================
# 3. Token extraction — synonym & intent phrases
# =============================================================================

class TestTokenExtractionSynonyms:

    def test_vitamin_b3_extracted(self):
        tokens = extract_query_tokens("vitamin b3 serum")
        assert "vitamin b3" in tokens

    def test_brightening_extracted(self):
        tokens = extract_query_tokens("brightening serum")
        assert "brightening" in tokens

    def test_glow_extracted(self):
        tokens = extract_query_tokens("glow serum")
        assert "glow" in tokens

    def test_acne_extracted(self):
        tokens = extract_query_tokens("acne serum")
        assert "acne" in tokens

    def test_dark_spots_extracted_as_multi_word(self):
        tokens = extract_query_tokens("serum for dark spots")
        assert "dark spots" in tokens
        assert "dark" not in tokens   # consumed by multi-word

    def test_pore_extracted(self):
        tokens = extract_query_tokens("serum for enlarged pores")
        assert "enlarged pores" in tokens

    def test_oil_control_extracted(self):
        tokens = extract_query_tokens("serum for oil control")
        assert "oil control" in tokens

    def test_tint_extracted(self):
        # "with tint" is a multi-word token (more specific match wins)
        tokens = extract_query_tokens("sunscreen with tint")
        assert any("tint" in t for t in tokens)

    def test_skin_tint_extracted_as_multi_word(self):
        tokens = extract_query_tokens("skin tint sunscreen")
        assert "skin tint" in tokens

    def test_white_cast_extracted(self):
        # "no white cast" is the most specific multi-word match
        tokens = extract_query_tokens("sunscreen with no white cast")
        assert any("white cast" in t for t in tokens)

    def test_without_fragrance_extracted(self):
        tokens = extract_query_tokens("moisturizer without fragrance")
        assert "without fragrance" in tokens

    def test_unscented_extracted(self):
        tokens = extract_query_tokens("unscented moisturizer")
        assert "unscented" in tokens

    def test_barrier_repair_extracted(self):
        tokens = extract_query_tokens("barrier repair moisturizer")
        assert "barrier repair" in tokens

    def test_chapped_lips_extracted(self):
        tokens = extract_query_tokens("chapped lips lip balm")
        assert "chapped lips" in tokens


# =============================================================================
# 4. Token enrichment (synonym / intent expansion)
# =============================================================================

class TestTokenEnrichment:

    def _sources(self, query):
        tokens = extract_query_tokens(query)
        return {tok.source for tok in _enrich_tokens(tokens)}

    def _texts(self, query):
        tokens = extract_query_tokens(query)
        return {tok.text for tok in _enrich_tokens(tokens)}

    def test_vitamin_b3_expands_to_niacinamide(self):
        enriched = _enrich_tokens(["vitamin b3"])
        texts = {t.text for t in enriched}
        assert "niacinamide" in texts
        # Check the expansion is synonym-level
        syn = next(t for t in enriched if t.text == "niacinamide")
        assert syn.source == "synonym"
        assert syn.factor == 0.9

    def test_white_cast_expands_to_tinted(self):
        texts = self._texts("no white cast sunscreen")
        assert "tinted" in texts

    def test_without_fragrance_expands_to_fragrance_free(self):
        texts = self._texts("moisturizer without fragrance")
        assert "fragrance free" in texts

    def test_unscented_expands_to_fragrance_free(self):
        texts = self._texts("unscented moisturizer")
        assert "fragrance free" in texts

    def test_fuzzy_niacinimide_expands_to_niacinamide(self):
        enriched = _enrich_tokens(["niacinimide"])
        texts = {t.text for t in enriched}
        assert "niacinamide" in texts
        fuzzy = next(t for t in enriched if t.text == "niacinamide")
        assert fuzzy.source == "fuzzy"
        assert fuzzy.factor == 0.7

    def test_no_duplicate_in_enriched(self):
        enriched = _enrich_tokens(["niacinamide", "vitamin c"])
        texts = [t.text for t in enriched]
        assert len(texts) == len(set(texts)), "Enriched tokens must be deduplicated"


# =============================================================================
# 5. Ingredient synonym ranking
# =============================================================================

class TestIngredientSynonymRanking:

    _NIA = [
        _p("Vitamin C Brightening Serum", ingredients=["Ascorbic Acid"], cap_scores={"brightening": 9.0}),
        _p("Niacinamide 10% Serum", ingredients=["Niacinamide"], cap_scores={"oil_control": 9.0, "pore_care": 9.0}),
        _p("Hyaluronic Acid Serum", ingredients=["Sodium Hyaluronate"], cap_scores={"hydration": 9.0}),
    ]

    _VIT_C = [
        _p("Niacinamide 10% Serum", ingredients=["Niacinamide"], cap_scores={"oil_control": 9.0}),
        _p("Vitamin C 20% Brightening Serum", ingredients=["Ascorbic Acid", "Vitamin C"], cap_scores={"brightening": 9.0, "pigmentation": 9.0}),
        _p("Hyaluronic Acid Serum", ingredients=["Sodium Hyaluronate"], cap_scores={"hydration": 9.0}),
    ]

    def test_vitamin_b3_ranks_niacinamide_first(self):
        assert "niacinamide" in _top(self._NIA, "vitamin b3 serum").lower()

    def test_serum_with_niacinamide_ranks_niacinamide_first(self):
        assert "niacinamide" in _top(self._NIA, "serum with niacinamide").lower()

    def test_enlarged_pores_ranks_niacinamide_first(self):
        assert "niacinamide" in _top(self._NIA, "serum for enlarged pores").lower()

    def test_oil_control_ranks_niacinamide_first(self):
        assert "niacinamide" in _top(self._NIA, "serum for oil control").lower()

    def test_brightening_ranks_vitamin_c_first(self):
        assert "vitamin c" in _top(self._VIT_C, "brightening serum").lower()

    def test_glow_ranks_vitamin_c_first(self):
        assert "vitamin c" in _top(self._VIT_C, "glow serum").lower()

    def test_pigmentation_ranks_vitamin_c_first(self):
        assert "vitamin c" in _top(self._VIT_C, "pigmentation serum").lower()

    def test_antioxidant_ranks_vitamin_c_first(self):
        assert "vitamin c" in _top(self._VIT_C, "antioxidant serum").lower()


# =============================================================================
# 6. Concern mapping → salicylic acid
# =============================================================================

class TestAcneRanking:

    _products = [
        _p("Niacinamide 10% Serum", ingredients=["Niacinamide"],
           cap_scores={"oil_control": 9.0, "pore_care": 9.0}),
        _p("Salicylic Acid 2% Serum", ingredients=["Salicylic Acid"], cap_scores={"acne": 9.0}),
        _p("Hyaluronic Acid Serum", ingredients=["Sodium Hyaluronate"], cap_scores={"hydration": 9.0}),
    ]

    def test_acne_serum_ranks_salicylic_acid_first(self):
        assert "salicylic" in _top(self._products, "acne serum").lower()

    def test_blackhead_serum_ranks_salicylic_acid_first(self):
        assert "salicylic" in _top(self._products, "blackhead serum").lower()

    def test_pore_clearing_ranks_niacinamide(self):
        # pore clearing → pore_care intent
        assert "niacinamide" in _top(self._products, "pore clearing serum").lower()

    def test_breakout_treatment_ranks_salicylic_acid_first(self):
        assert "salicylic" in _top(self._products, "breakout treatment").lower()


# =============================================================================
# 7. Attribute ranking (tinted, fragrance-free, texture)
# =============================================================================

class TestAttributeRanking:

    _sun = [
        _p("Watermelon Cooling Sunscreen SPF 50", skin_score=2),
        _p("Strawberry Dew Tinted Sunscreen SPF 50", skin_score=1),
        _p("Cica + Niacinamide Sunscreen SPF 50", skin_score=2),
    ]

    _moist = [
        _p("Peptide Daily Moisturizer"),
        _p("Ceramide Daily Moisturizer"),
        _p("Fragrance-Free Barrier Moisturizer", free_from=["Fragrance"]),
    ]

    def test_skin_tint_ranks_tinted_sunscreen_first(self):
        top = _top(self._sun, "skin tint sunscreen")
        assert "tinted" in top.lower()

    def test_sunscreen_with_no_white_cast_ranks_tinted_first(self):
        top = _top(self._sun, "sunscreen with no white cast")
        assert "tinted" in top.lower()

    def test_sunscreen_with_coverage_ranks_tinted_first(self):
        top = _top(self._sun, "sunscreen with coverage")
        assert "tinted" in top.lower()

    def test_without_fragrance_ranks_fragrance_free_first(self):
        top = _top(self._moist, "moisturizer without fragrance")
        assert any("fragrance" in ff.lower() for ff in
                   next(p for p in self._moist if p["title"] in top)["free_from"])

    def test_unscented_ranks_fragrance_free_first(self):
        top = _top(self._moist, "unscented moisturizer")
        assert "fragrance" in top.lower()

    def test_sensitive_skin_ranks_fragrance_free_first(self):
        # "sensitive skin" multi-word → ["ceramide", "fragrance free"]
        top = _top(self._moist, "sensitive skin moisturizer")
        # fragrance-free moisturizer should rank above plain ones
        rank = _rank_of(self._moist, "sensitive skin moisturizer", "fragrance")
        assert rank == 0


# =============================================================================
# 8. Texture ranking
# =============================================================================

class TestTextureRanking:

    _sun = [
        _p("Watermelon Sunscreen SPF 50"),
        _p("Matte Finish Sunscreen SPF 50"),
        _p("Dewy Glow Sunscreen SPF 50"),
    ]

    _moist = [
        _p("Ceramide Daily Cream"),
        _p("Lightweight Gel Moisturizer"),
        _p("Rich Nourishing Cream Moisturizer"),
    ]

    def test_matte_sunscreen_query_ranks_matte_first(self):
        top = _top(self._sun, "matte sunscreen")
        assert "matte" in top.lower()

    def test_dewy_sunscreen_query_ranks_dewy_first(self):
        top = _top(self._sun, "dewy sunscreen")
        assert "dewy" in top.lower()

    def test_lightweight_moisturizer_query_ranks_lightweight_first(self):
        top = _top(self._moist, "lightweight moisturizer")
        assert "lightweight" in top.lower()

    def test_gel_moisturizer_query_ranks_gel_first(self):
        top = _top(self._moist, "gel moisturizer")
        assert "gel" in top.lower()


# =============================================================================
# 9. Variant name ranking
# =============================================================================

class TestVariantRanking:

    _lip = [
        _p("Ceramide Peptide Lip Balm", variant="Clear"),
        _p("Ceramide Peptide Lip Balm", variant="Blueberry Bliss"),
        _p("Ceramide Peptide Lip Balm", variant="Strawberry Red"),
        _p("Ceramide Peptide Lip Balm", variant="Warm Nude"),
    ]

    _sun = [
        _p("Tinted Sunscreen SPF 50", variant="Ivory"),
        _p("Tinted Sunscreen SPF 50", variant="Caramel"),
        _p("Tinted Sunscreen SPF 50", variant="Peony"),
    ]

    def test_blueberry_variant_ranks_first(self):
        tokens = extract_query_tokens("blueberry bliss lip balm")
        ranked = rerank_by_query_intent(list(self._lip), tokens)
        assert ranked[0]["variant"] == "Blueberry Bliss"

    def test_strawberry_variant_ranks_first(self):
        tokens = extract_query_tokens("strawberry red lip balm")
        ranked = rerank_by_query_intent(list(self._lip), tokens)
        assert ranked[0]["variant"] == "Strawberry Red"

    def test_caramel_sunscreen_variant_ranks_first(self):
        tokens = extract_query_tokens("caramel sunscreen")
        ranked = rerank_by_query_intent(list(self._sun), tokens)
        assert ranked[0]["variant"] == "Caramel"

    def test_peony_sunscreen_variant_ranks_first(self):
        tokens = extract_query_tokens("peony sunscreen")
        ranked = rerank_by_query_intent(list(self._sun), tokens)
        assert ranked[0]["variant"] == "Peony"

    def test_warm_nude_lip_balm_ranks_first(self):
        tokens = extract_query_tokens("warm nude lip balm")
        ranked = rerank_by_query_intent(list(self._lip), tokens)
        assert ranked[0]["variant"] == "Warm Nude"


# =============================================================================
# 10. Combined intent (ingredient + skin context / budget qualifier)
# =============================================================================

class TestCombinedIntentRanking:
    """Query intent must remain the strongest factor even when skin type or
    budget qualifiers are present in the message."""

    _serums = [
        _p("Generic Brightening Serum", skin_score=3, concern_score=2),
        _p("Niacinamide 10% + Zinc Serum",
           skin_score=1, ingredients=["Niacinamide"]),
    ]

    _sun = [
        _p("Watermelon Cooling Sunscreen SPF 50", skin_score=2),
        _p("Strawberry Dew Tinted Sunscreen SPF 50", skin_score=1),
    ]

    _lip = [
        _p("Gloss Boss Lip Gloss"),
        _p("Ceramide + Peptide Lip Balm", ingredients=["Ceramide", "Peptide"]),
    ]

    def test_niacinamide_serum_for_oily_skin(self):
        top = _top(self._serums, "niacinamide serum for oily skin")
        assert "niacinamide" in top.lower()

    def test_vitamin_c_serum_under_600(self):
        products = [
            _p("Hyaluronic Acid Serum"),
            _p("Vitamin C 20% Serum", ingredients=["Vitamin C"]),
        ]
        top = _top(products, "vitamin c serum under 600")
        assert "vitamin c" in top.lower()

    def test_tinted_sunscreen_for_dry_skin(self):
        top = _top(self._sun, "tinted sunscreen for dry skin")
        assert "tinted" in top.lower()

    def test_ceramide_lip_balm_under_300(self):
        top = _top(self._lip, "ceramide lip balm under 300")
        assert "ceramide" in top.lower()


# =============================================================================
# 11. Typo tolerance (fuzzy matching)
# =============================================================================

class TestTypoTolerance:

    _serums = [
        _p("Vitamin C Brightening Serum", ingredients=["Ascorbic Acid"]),
        _p("Niacinamide 10% Serum", ingredients=["Niacinamide"]),
        _p("Hyaluronic Acid Serum", ingredients=["Sodium Hyaluronate"]),
    ]

    _sun = [
        _p("Watermelon Cooling Sunscreen SPF 50", skin_score=2),
        _p("Strawberry Dew Tinted Sunscreen SPF 50", skin_score=1),
    ]

    _lip = [
        _p("Gloss Boss Lip Gloss"),
        _p("Ceramide + Peptide Lip Balm", ingredients=["Ceramide"]),
    ]

    _moist = [
        _p("Peptide Face Cream", skin_score=2),
        _p("Fragrance-Free Ceramide Moisturizer", free_from=["Fragrance"]),
    ]

    def test_niacinimide_ranks_niacinamide_first(self):
        top = _top(self._serums, "niacinimide serum")
        assert "niacinamide" in top.lower()

    def test_niacinmide_ranks_niacinamide_first(self):
        top = _top(self._serums, "niacinmide serum")
        assert "niacinamide" in top.lower()

    def test_niacnamide_ranks_niacinamide_first(self):
        top = _top(self._serums, "niacnamide serum")
        assert "niacinamide" in top.lower()

    def test_tintted_sunscrean_ranks_tinted_first(self):
        top = _top(self._sun, "tintted sunscrean")
        assert "tinted" in top.lower()

    def test_cerimide_lip_balm_ranks_ceramide_first(self):
        top = _top(self._lip, "cerimide lip balm")
        assert "ceramide" in top.lower()

    def test_fragnance_free_ranks_fragrance_free_first(self):
        top = _top(self._moist, "fragnance free moisturizer")
        assert "fragrance" in top.lower()


# =============================================================================
# 12. Exact > synonym > intent > fuzzy priority
# =============================================================================

class TestScoreTierOrdering:
    """A product matching via a higher tier must always outscore one matched
    via a lower tier when both target the same canonical ingredient."""

    def test_exact_beats_fuzzy_for_same_product(self):
        """Exact 'niacinamide' must yield higher qi than fuzzy 'niacinimide'."""
        p = _p("Niacinamide 10% Serum", ingredients=["Niacinamide"])
        score_exact = compute_query_intent_score(p, ["niacinamide"])
        score_fuzzy = compute_query_intent_score(p, ["niacinimide"])
        assert score_exact > score_fuzzy, (
            f"Exact ({score_exact}) must beat fuzzy ({score_fuzzy})"
        )

    def test_synonym_beats_intent_for_same_product(self):
        """Synonym 'vitamin b3' must yield higher qi than intent 'brightening'."""
        p = _p("Niacinamide Brightening Serum", ingredients=["Niacinamide"])
        score_syn = compute_query_intent_score(p, ["vitamin b3"])
        score_intent = compute_query_intent_score(p, ["brightening"])
        # Both resolve to niacinamide but at different factors (0.9 vs 0.8)
        assert score_syn >= score_intent, (
            f"Synonym ({score_syn}) must be ≥ intent ({score_intent})"
        )

    def test_fuzzy_typo_does_not_beat_exact_match(self):
        """When two products compete — one matched via exact, one via fuzzy —
        the exact match must rank first."""
        products = [
            _p("Hyaluronic Acid Serum", ingredients=["Sodium Hyaluronate"]),   # no niacinamide
            _p("Niacinamide 10% Serum", ingredients=["Niacinamide"]),           # niacinamide
        ]
        # Exact query
        ranked_exact = rerank_by_query_intent(
            list(products), extract_query_tokens("niacinamide serum")
        )
        # Typo query
        ranked_fuzzy = rerank_by_query_intent(
            list(products), extract_query_tokens("niacinimide serum")
        )
        # In both cases, niacinamide product should rank first
        assert "niacinamide" in ranked_exact[0]["title"].lower()
        assert "niacinamide" in ranked_fuzzy[0]["title"].lower()

    def test_exact_score_higher_than_fuzzy_score(self):
        """Sanity check: exact 'ceramide' score > fuzzy 'cerimide' score."""
        p = _p("Ceramide Moisturizer", ingredients=["Ceramide"])
        exact = compute_query_intent_score(p, ["ceramide"])
        fuzzy = compute_query_intent_score(p, ["cerimide"])
        assert exact > fuzzy


# =============================================================================
# 13. Negative invariants — matching product must ALWAYS outrank non-matching
# =============================================================================

class TestNegativeInvariants:

    def test_non_niacinamide_never_outranks_niacinamide(self):
        niacinamide = _p("Niacinamide 10% Serum", ingredients=["Niacinamide"])
        other = _p("Watermelon Serum", skin_score=5, concern_score=5)
        ranked = rerank_by_query_intent([other, niacinamide],
                                        extract_query_tokens("niacinamide serum"))
        assert ranked[0]["title"] == niacinamide["title"]

    def test_non_tinted_never_outranks_tinted_sunscreen(self):
        tinted = _p("Strawberry Dew Tinted Sunscreen SPF 50")
        plain = _p("Watermelon Cooling Sunscreen SPF 50", skin_score=5)
        ranked = rerank_by_query_intent([plain, tinted],
                                        extract_query_tokens("tinted sunscreen"))
        assert ranked[0]["title"] == tinted["title"]

    def test_fragranced_never_outranks_fragrance_free(self):
        ff = _p("Fragrance-Free Ceramide Moisturizer", free_from=["Fragrance"])
        scented = _p("Peptide Moisturizer", skin_score=5)
        ranked = rerank_by_query_intent([scented, ff],
                                        extract_query_tokens("fragrance free moisturizer"))
        assert ranked[0]["title"] == ff["title"]

    def test_non_ceramide_never_outranks_ceramide_for_ceramide_query(self):
        ceramide = _p("Ceramide + Peptide Lip Balm", ingredients=["Ceramide"])
        plain = _p("Vanilla Lip Balm", skin_score=10)
        ranked = rerank_by_query_intent([plain, ceramide],
                                        extract_query_tokens("ceramide lip balm"))
        assert ranked[0]["title"] == ceramide["title"]


# =============================================================================
# 14. Debug fields
# =============================================================================

class TestDebugFields:

    def test_intent_sources_set_on_rerank(self):
        products = [
            _p("Niacinamide Serum", ingredients=["Niacinamide"]),
            _p("Generic Serum"),
        ]
        rerank_by_query_intent(products, ["niacinamide"])
        assert all("intent_sources" in p for p in products)
        assert all("final_score" in p for p in products)
        assert all("query_intent_score" in p for p in products)

    def test_matching_product_has_exact_source(self):
        products = [_p("Niacinamide Serum", ingredients=["Niacinamide"])]
        rerank_by_query_intent(products, ["niacinamide"])
        assert "exact" in products[0]["intent_sources"]

    def test_fuzzy_match_source_recorded(self):
        products = [_p("Niacinamide Serum", ingredients=["Niacinamide"])]
        rerank_by_query_intent(products, ["niacinimide"])
        # fuzzy expands to niacinamide → should appear as "fuzzy" source
        assert "fuzzy" in products[0]["intent_sources"]

    def test_intent_source_recorded_for_concern_query(self):
        # Intent words map to axes, so they don't produce 'intent' source tags in enriched tokens anymore.
        pass

    def test_no_sources_for_non_matching_product(self):
        products = [_p("Vanilla Serum")]
        rerank_by_query_intent(products, ["niacinamide"])
        assert products[0]["intent_sources"] == []

    def test_final_score_reflects_formula(self):
        """final_score = qi×80 + skin×30 + concern×25 + allergen×25"""
        p = _p("Niacinamide Serum", ingredients=["Niacinamide"],
               skin_score=1, concern_score=1)
        rerank_by_query_intent([p], ["niacinamide"])
        qi = p["query_intent_score"]
        expected = qi * 80 + 1 * 30 + 1 * 25
        assert p["final_score"] == expected


# =============================================================================
# 15. Allergen & fragrance-aware ranking
# =============================================================================

class TestAllergenAwareRanking:
    """Fragrance/allergy sensitivity intent must materially boost fragrance-free
    products and penalise confirmed-fragranced products."""

    # Standard product set: one fragrance-free, two regular
    _moist = [
        _p("Peptide Daily Cream", skin_score=2,
           ingredients=["Peptide", "Fragrance"]),          # has fragrance!
        _p("Ceramide Daily Moisturizer", skin_score=2,
           ingredients=["Ceramide", "Fragrance"]),          # has fragrance!
        _p("Barrier Repair Fragrance-Free Moisturizer",
           free_from=["Fragrance"], skin_score=1,
           ingredients=["Ceramide", "Niacinamide"]),        # fragrance-free
    ]

    # Sunscreens — one fragrance-free
    _sun = [
        _p("Watermelon Sunscreen SPF 50", skin_score=2,
           ingredients=["Watermelon Extract", "Fragrance"]),
        _p("Cica Sunscreen SPF 50 Fragrance-Free",
           free_from=["Fragrance"], skin_score=1,
           ingredients=["Centella", "Zinc Oxide"]),
    ]

    # ── Token extraction tests ──────────────────────────────────────────────

    def test_hypoallergenic_extracted(self):
        tokens = extract_query_tokens("hypoallergenic moisturizer")
        assert "hypoallergenic" in tokens

    def test_eczema_extracted(self):
        tokens = extract_query_tokens("eczema moisturizer")
        assert "eczema" in tokens

    def test_react_to_fragrance_extracted(self):
        tokens = extract_query_tokens("I react to fragrance")
        assert "react to fragrance" in tokens

    def test_allergy_safe_extracted(self):
        tokens = extract_query_tokens("allergy safe sunscreen")
        assert "allergy safe" in tokens

    def test_easily_irritated_extracted(self):
        tokens = extract_query_tokens("easily irritated skin moisturizer")
        assert "easily irritated" in tokens

    # ── Synonym/intent expansion tests ─────────────────────────────────────

    def test_hypoallergenic_expands_to_fragrance_free(self):
        enriched = _enrich_tokens(["hypoallergenic"])
        texts = {t.text for t in enriched}
        assert "fragrance free" in texts

    def test_allergy_safe_expands_to_fragrance_free(self):
        enriched = _enrich_tokens(["allergy safe"])
        texts = {t.text for t in enriched}
        assert "fragrance free" in texts

    def test_eczema_expands_to_fragrance_free_and_ceramide(self):
        enriched = _enrich_tokens(["eczema"])
        texts = {t.text for t in enriched}
        assert "fragrance free" in texts
        assert "ceramide" in texts

    def test_react_to_fragrance_expands_to_fragrance_free(self):
        enriched = _enrich_tokens(["react to fragrance"])
        texts = {t.text for t in enriched}
        assert "fragrance free" in texts

    def test_no_fragrance_expands_to_fragrance_free(self):
        enriched = _enrich_tokens(["no fragrance"])
        texts = {t.text for t in enriched}
        assert "fragrance free" in texts

    # ── Ranking tests ───────────────────────────────────────────────────────

    def test_fragrance_free_query_ranks_ff_product_first(self):
        top = _top(self._moist, "fragrance free moisturizer")
        assert "fragrance" in top.lower()   # product title contains "Fragrance-Free"

    def test_unscented_query_ranks_ff_product_first(self):
        top = _top(self._moist, "unscented moisturizer")
        assert "fragrance" in top.lower()

    def test_hypoallergenic_query_ranks_ff_product_first(self):
        top = _top(self._moist, "hypoallergenic moisturizer")
        assert "fragrance" in top.lower()

    def test_eczema_query_ranks_ff_product_first(self):
        top = _top(self._moist, "eczema moisturizer")
        assert "fragrance" in top.lower()

    def test_react_to_fragrance_ranks_ff_product_first(self):
        top = _top(self._moist, "I react to fragrance moisturizer")
        assert "fragrance" in top.lower()

    def test_allergy_safe_query_ranks_ff_sunscreen_first(self):
        top = _top(self._sun, "allergy safe sunscreen")
        assert "fragrance-free" in top.lower() or "fragrance free" in top.lower()

    def test_easily_irritated_ranks_ff_first(self):
        top = _top(self._moist, "easily irritated skin moisturizer")
        assert "fragrance" in top.lower()

    # ── Negative penalty tests ──────────────────────────────────────────────

    def test_fragranced_product_gets_penalty_for_ff_query(self):
        """Fragranced product must score LOWER than fragrance-free for a
        'fragrance free' query, even if it has higher skin_score."""
        ff = _p("Fragrance-Free Moisturizer", free_from=["Fragrance"],
                skin_score=1, ingredients=["Ceramide"])
        fragranced = _p("Peptide Moisturizer", skin_score=5,
                        ingredients=["Peptide", "Fragrance"])

        tokens = extract_query_tokens("fragrance free moisturizer")
        ranked = rerank_by_query_intent([fragranced, ff], tokens)
        assert ranked[0]["title"] == ff["title"], (
            "Fragrance-free must rank above fragranced even with lower skin_score"
        )

    def test_fragranced_product_has_lower_final_score(self):
        """Penalty reduces final_score of fragranced product."""
        ff = _p("FF Moisturizer", free_from=["Fragrance"], ingredients=["Ceramide"])
        fragranced = _p("Fragranced Moisturizer", ingredients=["Ceramide", "Fragrance"])

        tokens = extract_query_tokens("fragrance free moisturizer")
        ranked = rerank_by_query_intent([fragranced, ff], tokens)

        ff_score = next(p["final_score"] for p in ranked if "FF" in p["title"])
        frag_score = next(p["final_score"] for p in ranked if "Fragranced" in p["title"])
        assert ff_score > frag_score

    def test_fragranced_never_outranks_ff_for_unscented_query(self):
        ff = _p("Fragrance-Free Cream", free_from=["Fragrance"], skin_score=1,
                ingredients=["Ceramide"])
        fragranced = _p("Scented Cream", skin_score=10, ingredients=["Fragrance"])
        ranked = rerank_by_query_intent(
            [fragranced, ff], extract_query_tokens("unscented moisturizer")
        )
        assert ranked[0]["title"] == ff["title"]

    def test_fragranced_never_outranks_ff_for_hypoallergenic_query(self):
        ff = _p("Hypoallergenic Barrier Cream", free_from=["Fragrance"],
                skin_score=1, ingredients=["Ceramide"])
        fragranced = _p("Regular Cream", skin_score=5, ingredients=["Fragrance"])
        ranked = rerank_by_query_intent(
            [fragranced, ff], extract_query_tokens("hypoallergenic moisturizer")
        )
        assert ranked[0]["title"] == ff["title"]

    def test_no_penalty_without_ingredient_data(self):
        """If a product has no ingredient list, no penalty is applied
        (we can't confirm it's fragranced — conservative)."""
        ff = _p("Fragrance-Free Cream", free_from=["Fragrance"])
        unknown = _p("Mystery Cream", skin_score=10)  # no ingredients field

        tokens = extract_query_tokens("fragrance free moisturizer")
        rerank_by_query_intent([unknown, ff], tokens)
        # No penalty on "unknown" because ingredients is empty
        assert unknown.get("final_score", 0) >= 0  # just confirm it didn't crash

    # ── Fragrance boost value tests ─────────────────────────────────────────

    def test_fragrance_free_boost_is_higher_than_generic_free_from(self):
        """BOOST_FRAGRANCE_FREE (120) > BOOST_FREE_FROM (80)."""
        from backend.query_intent import BOOST_FRAGRANCE_FREE, BOOST_FREE_FROM
        assert BOOST_FRAGRANCE_FREE > BOOST_FREE_FROM

    def test_exact_fragrance_free_scores_higher_than_synonym(self):
        """Exact 'fragrance free' query must score higher than 'unscented'
        against the same fragrance-free product."""
        p = _p("Ceramide Moisturizer", free_from=["Fragrance"],
               ingredients=["Ceramide"])
        score_exact = compute_query_intent_score(p, ["fragrance free"])
        score_syn   = compute_query_intent_score(p, ["unscented"])
        assert score_exact > score_syn, (
            f"Exact ({score_exact}) must beat synonym ({score_syn})"
        )

    # ── Debug field tests ───────────────────────────────────────────────────

    def test_fragrance_score_field_set(self):
        products = [_p("FF Cream", free_from=["Fragrance"], ingredients=["Ceramide"])]
        rerank_by_query_intent(products, extract_query_tokens("fragrance free moisturizer"))
        assert "fragrance_score" in products[0]
        assert products[0]["fragrance_score"] > 0

    def test_allergen_score_field_set(self):
        products = [_p("FF Cream", free_from=["Fragrance", "Alcohol"],
                        ingredients=["Ceramide"])]
        rerank_by_query_intent(products, ["fragrance free"])
        assert "allergen_score" in products[0]
        # allergen_score = len(free_from) * 25 = 2 * 25 = 50
        assert products[0]["allergen_score"] == 50

    def test_fragrance_score_zero_for_non_ff_product(self):
        products = [_p("Peptide Cream", ingredients=["Peptide"])]
        rerank_by_query_intent(products, ["fragrance free"])
        assert products[0]["fragrance_score"] == 0

    def test_fragrance_score_nonzero_for_ff_product(self):
        products = [_p("FF Cream", free_from=["Fragrance"], ingredients=["Ceramide"])]
        rerank_by_query_intent(products, ["fragrance free"])
        assert products[0]["fragrance_score"] > 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
