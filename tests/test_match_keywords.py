"""
tests/test_match_keywords.py

Tests for backend/match_keywords.build_keywords() — the deterministic
"why this matches" tag generator shown under top-pick product cards.

Priority order matters here: all-skin-types > concern > texture >
ingredient > allergen-free, capped at 3, deduplicated. These tests pin
that behavior down since it's a visible, user-facing ordering decision,
not an implementation detail.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from backend.match_keywords import (
    build_keywords,
    CONCERN_LABELS,
    TEXTURE_LABELS,
    INGREDIENT_LABELS,
    FREE_FROM_LABELS,
    MAX_KEYWORDS,
)


class TestAllSkinTypesChip:
    """A product titled e.g. '...for Oily Skin' can still be graph-tagged
    SUITS_SKIN_TYPE for all 5 core types — the title is marketing copy,
    not the actual match set. This chip surfaces that so users on a
    different skin type aren't misled by the title alone. Regression
    coverage for the live-site case: 'Cica + 10% Niacinamide Face Serum
    for Oily Skin' tagged for oily/dry/normal/combination/sensitive."""

    def test_all_five_core_types_shows_chip(self):
        tags = build_keywords(
            matched_concerns=[], texture="", key_ingredients=[], free_from=[],
            all_skin_types=["oily", "dry", "combination", "normal", "sensitive"],
        )
        assert "All Skin Types" in tags

    def test_explicit_all_tag_shows_chip(self):
        tags = build_keywords(
            matched_concerns=[], texture="", key_ingredients=[], free_from=[],
            all_skin_types=["all"],
        )
        assert "All Skin Types" in tags

    def test_four_of_five_types_does_not_show_chip(self):
        """Missing 'sensitive' — must not claim All Skin Types."""
        tags = build_keywords(
            matched_concerns=[], texture="", key_ingredients=[], free_from=[],
            all_skin_types=["oily", "dry", "combination", "normal"],
        )
        assert "All Skin Types" not in tags

    def test_single_skin_type_does_not_show_chip(self):
        tags = build_keywords(
            matched_concerns=[], texture="", key_ingredients=[], free_from=[],
            all_skin_types=["dry"],
        )
        assert "All Skin Types" not in tags

    def test_all_skin_types_ranks_before_concern(self):
        tags = build_keywords(
            matched_concerns=["acne"], texture="", key_ingredients=[], free_from=[],
            all_skin_types=["oily", "dry", "combination", "normal", "sensitive"],
        )
        assert tags[0] == "All Skin Types"
        assert tags[1] == "Anti-acne"

    def test_all_skin_types_counts_toward_max_keywords_cap(self):
        tags = build_keywords(
            matched_concerns=["acne"], texture="lightweight",
            key_ingredients=["niacinamide"], free_from=[],
            all_skin_types=["oily", "dry", "combination", "normal", "sensitive"],
        )
        assert len(tags) == MAX_KEYWORDS
        assert tags == ["All Skin Types", "Anti-acne", "Lightweight"]

    def test_none_all_skin_types_does_not_crash(self):
        tags = build_keywords(
            matched_concerns=["acne"], texture="", key_ingredients=[], free_from=[],
            all_skin_types=None,
        )
        assert tags == ["Anti-acne"]

    def test_missing_all_skin_types_arg_defaults_safely(self):
        """Backward compatible — existing callers that don't pass
        all_skin_types must keep working unchanged."""
        tags = build_keywords(
            matched_concerns=["acne"], texture="", key_ingredients=[], free_from=[],
        )
        assert tags == ["Anti-acne"]

    def test_empty_all_skin_types_list_does_not_show_chip(self):
        tags = build_keywords(
            matched_concerns=[], texture="", key_ingredients=[], free_from=[],
            all_skin_types=[],
        )
        assert tags == []


class TestPriorityOrder:

    def test_concern_comes_before_texture(self):
        tags = build_keywords(
            matched_concerns=["acne"], texture="lightweight",
            key_ingredients=[], free_from=[],
        )
        assert tags[0] == "Anti-acne"
        assert tags[1] == "Lightweight"

    def test_texture_comes_before_ingredient(self):
        tags = build_keywords(
            matched_concerns=[], texture="rich",
            key_ingredients=["niacinamide"], free_from=[],
        )
        assert tags[0] == "Rich texture"
        assert tags[1] == "Niacinamide"

    def test_ingredient_comes_before_allergen_free(self):
        tags = build_keywords(
            matched_concerns=[], texture="",
            key_ingredients=["cica"], free_from=["fragrance"],
        )
        assert tags[0] == "Cica"
        assert tags[1] == "Fragrance-free"

    def test_full_priority_chain(self):
        tags = build_keywords(
            matched_concerns=["dullness"], texture="dewy",
            key_ingredients=["vitamin_c"], free_from=["sulfate"],
        )
        # capped at 3 — allergen-free claim gets dropped since it's lowest
        # priority and concern+texture+ingredient already filled the cap
        assert tags == ["Brightening", "Dewy finish", "Vitamin C"]


class TestCapping:

    def test_never_exceeds_max_keywords(self):
        tags = build_keywords(
            matched_concerns=["acne", "dark_spots", "ageing"],
            texture="lightweight",
            key_ingredients=["niacinamide", "salicylic"],
            free_from=["fragrance", "alcohol"],
        )
        assert len(tags) == MAX_KEYWORDS

    def test_fewer_than_max_when_data_sparse(self):
        tags = build_keywords(
            matched_concerns=["acne"], texture="",
            key_ingredients=[], free_from=[],
        )
        assert tags == ["Anti-acne"]

    def test_empty_everything_returns_empty_list(self):
        tags = build_keywords(
            matched_concerns=[], texture="", key_ingredients=[], free_from=[],
        )
        assert tags == []

    def test_none_inputs_dont_crash(self):
        """Graph query results can come back as None for empty
        collections in some edge cases — must not raise."""
        tags = build_keywords(
            matched_concerns=None, texture=None,
            key_ingredients=None, free_from=None,
        )
        assert tags == []


class TestDeduplication:

    def test_two_concerns_mapping_to_same_label_dedupe(self):
        """dryness and dehydration both map to 'Hydrating' — should
        only appear once, not twice."""
        tags = build_keywords(
            matched_concerns=["dryness", "dehydration"], texture="",
            key_ingredients=[], free_from=[],
        )
        assert tags == ["Hydrating"]

    def test_concern_and_separately_matching_synonym_still_dedupes(self):
        tags = build_keywords(
            matched_concerns=["ageing", "fine_lines"], texture="",
            key_ingredients=[], free_from=[],
        )
        assert tags.count("Anti-aging") == 1


class TestUnknownValuesIgnoredGracefully:

    def test_unknown_concern_name_is_skipped_not_crashed(self):
        tags = build_keywords(
            matched_concerns=["some_future_concern_not_in_map"],
            texture="lightweight", key_ingredients=[], free_from=[],
        )
        assert tags == ["Lightweight"]

    def test_unknown_ingredient_is_skipped(self):
        """Ingredients not in INGREDIENT_LABELS (e.g. watermelon,
        which is a marketing ingredient, not a notable active) should
        be silently dropped — not every ingredient is a highlight."""
        tags = build_keywords(
            matched_concerns=[], texture="",
            key_ingredients=["watermelon", "niacinamide"], free_from=[],
        )
        assert tags == ["Niacinamide"]

    def test_unknown_texture_is_skipped(self):
        tags = build_keywords(
            matched_concerns=["acne"], texture="some_unmapped_texture",
            key_ingredients=[], free_from=[],
        )
        assert tags == ["Anti-acne"]


class TestLabelMapsAreWellFormed:

    def test_all_label_values_are_short(self):
        """Subheading text must stay short (it's a small line under a
        card, not a sentence) — guard against future entries that are
        too long for the UI."""
        all_labels = (
            list(CONCERN_LABELS.values())
            + list(TEXTURE_LABELS.values())
            + list(INGREDIENT_LABELS.values())
            + list(FREE_FROM_LABELS.values())
        )
        for label in all_labels:
            assert len(label) <= 20, f"label too long for a card subheading: {label!r}"

    def test_no_empty_labels(self):
        all_labels = (
            list(CONCERN_LABELS.values())
            + list(TEXTURE_LABELS.values())
            + list(INGREDIENT_LABELS.values())
            + list(FREE_FROM_LABELS.values())
        )
        assert all(label.strip() for label in all_labels)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))