"""
tests/test_keyword_extract.py

Tests for backend/playbooks/intake_profile.keyword_extract().

The "acne" vs "dark spots" false positive (generic 'spot' keyword
matching acne) was a real bug caught during manual testing — there's
an explicit regression test for it below.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from backend.playbooks.intake_profile import keyword_extract


class TestSkinTypeExtraction:

    def test_oily(self):
        out = keyword_extract("my skin is oily")
        assert out["skin_types"] == ["oily"]

    def test_dry(self):
        out = keyword_extract("I have really dry skin")
        assert "dry" in out["skin_types"]

    def test_combination(self):
        out = keyword_extract("I think I have combination skin")
        assert out["skin_types"] == ["combination"]

    def test_sensitive(self):
        out = keyword_extract("my skin is sensitive and reactive")
        assert out["skin_types"] == ["sensitive"]

    def test_multiple_skin_types(self):
        out = keyword_extract("dry and sensitive skin")
        assert set(out["skin_types"]) == {"dry", "sensitive"}

    def test_no_skin_type_mentioned(self):
        out = keyword_extract("looking for a sunscreen")
        assert "skin_types" not in out


class TestConcernExtraction:

    def test_acne_from_breakout(self):
        out = keyword_extract("I get a lot of breakouts")
        assert "acne" in out["concerns"]

    def test_acne_from_pimple(self):
        out = keyword_extract("I have pimples")
        assert "acne" in out["concerns"]

    def test_dark_spots_does_not_trigger_acne(self):
        """Regression test: 'spots' alone used to false-positive into
        the acne bucket via a generic 'spot' keyword. Fixed by requiring
        'acne spot' specifically, not bare 'spot'."""
        out = keyword_extract("I have dark spots on my skin")
        assert "dark_spots" in out["concerns"]
        assert "acne" not in out["concerns"]

    def test_acne_and_spots_together_still_works(self):
        out = keyword_extract("I have spots and acne")
        assert "acne" in out["concerns"]

    def test_dullness(self):
        out = keyword_extract("my skin looks dull and tired")
        assert "dullness" in out["concerns"]

    def test_pigmentation_and_tanning(self):
        out = keyword_extract("I want something for tan removal and pigmentation")
        assert "pigmentation" in out["concerns"]
        assert "tanning" in out["concerns"]

    def test_multiple_concerns(self):
        out = keyword_extract("dark spots and dullness are my main issues")
        assert set(out["concerns"]) >= {"dark_spots", "dullness"}


class TestCategoryExtraction:

    def test_sunscreen(self):
        out = keyword_extract("looking for a sunscreen")
        assert out["category"] == "sunscreen"

    def test_moisturizer_variant_spelling(self):
        out = keyword_extract("need a moisturiser")
        assert out["category"] == "moisturizer"

    def test_face_wash(self):
        out = keyword_extract("recommend a face wash")
        assert out["category"] == "face_wash"

    def test_serum(self):
        out = keyword_extract("need a serum")
        assert out["category"] == "serum"

    def test_no_category_mentioned(self):
        out = keyword_extract("my skin is oily")
        assert "category" not in out


class TestTextureExtraction:

    def test_lightweight(self):
        out = keyword_extract("something lightweight and oil-free")
        assert out["texture"] == "lightweight"

    def test_rich(self):
        out = keyword_extract("I want a rich, nourishing cream")
        assert out["texture"] == "rich"


class TestAllergenExtraction:

    def test_fragrance_free(self):
        out = keyword_extract("I want fragrance free products only")
        assert "fragrance" in out["allergen_free"]

    def test_multiple_allergens(self):
        out = keyword_extract("fragrance free and alcohol free please")
        assert set(out["allergen_free"]) == {"fragrance", "alcohol"}

    def test_no_allergen_mentioned(self):
        out = keyword_extract("oily skin with acne")
        assert "allergen_free" not in out


class TestPriceTierExtraction:

    def test_numeric_under_300(self):
        out = keyword_extract("looking for something under 250 rupees")
        assert out["price_tier"] == "under_300"

    def test_numeric_under_600(self):
        out = keyword_extract("budget is around ₹500")
        assert out["price_tier"] == "under_600"

    def test_numeric_under_1000(self):
        out = keyword_extract("budget is under 800")
        assert out["price_tier"] == "under_1000"

    def test_keyword_affordable(self):
        out = keyword_extract("something affordable please")
        assert out["price_tier"] == "under_600"

    def test_keyword_any(self):
        out = keyword_extract("no budget limit, show me the best")
        assert out["price_tier"] == "any"

    def test_no_price_mentioned(self):
        out = keyword_extract("oily skin, need a sunscreen")
        assert "price_tier" not in out


class TestSizePrefExtraction:

    def test_travel_size(self):
        out = keyword_extract("looking for a travel size moisturizer")
        assert out["size_pref"] == "travel"

    def test_value_pack(self):
        out = keyword_extract("pack of 2 preferred, value pack")
        assert out["size_pref"] == "value"

    def test_standard_size(self):
        out = keyword_extract("standard size is fine")
        assert out["size_pref"] == "standard"

    def test_no_size_mentioned(self):
        out = keyword_extract("oily skin, need a sunscreen")
        assert "size_pref" not in out


class TestCombinedExtraction:

    def test_full_sentence_extracts_everything(self):
        """The exact message that triggered the recommend-handoff bug —
        all three fields must extract correctly in one pass."""
        out = keyword_extract("oily acne-prone skin, recommend a sunscreen")
        assert out["skin_types"] == ["oily"]
        assert "acne" in out["concerns"]
        assert out["category"] == "sunscreen"

    def test_complex_multi_field_sentence(self):
        out = keyword_extract(
            "I have dry sensitive skin with dark spots and want "
            "fragrance free products, lightweight texture, under 500"
        )
        assert set(out["skin_types"]) == {"dry", "sensitive"}
        assert "dark_spots" in out["concerns"]
        assert out["allergen_free"] == ["fragrance"]
        assert out["texture"] == "lightweight"
        assert out["price_tier"] == "under_600"

    def test_empty_message_returns_empty_dict(self):
        out = keyword_extract("hello")
        assert out == {}




if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))