"""
tests/test_router.py

Tests for backend/router._fast_classify() — the keyword pre-classification
layer that handles unambiguous routing without an LLM call.

Only the synchronous fast-path is tested here (no network/API key needed).
The LLM fallback layer (classify() when fast-path returns None) is an
integration concern, exercised manually via curl against a live NIM key.

Regression coverage: "looking for a sunscreen" used to route to
general_qa instead of intake_profile because the router didn't know
the profile state — that's the test_category_mention_routes_to_intake
case below.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from backend.router import _fast_classify


EMPTY_PROFILE = {}
PROFILE_WITH_SKIN = {"skin_types": ["oily"], "concerns": ["acne", "excess_oil"]}
PROFILE_READY = {"skin_types": ["oily"], "concerns": ["acne"], "category": "sunscreen"}


class TestIntakeRouting:

    def test_skin_info_routes_to_intake(self):
        assert _fast_classify("my skin is oily and I get breakouts", EMPTY_PROFILE) == "intake_profile"

    def test_category_mention_routes_to_intake(self):
        """Regression: this used to fall through to the LLM router and
        sometimes land on general_qa instead of intake_profile."""
        assert _fast_classify("looking for a sunscreen", PROFILE_WITH_SKIN) == "intake_profile"

    def test_category_mention_with_empty_profile(self):
        assert _fast_classify("I need a moisturizer", EMPTY_PROFILE) == "intake_profile"

    def test_concern_keyword_routes_to_intake(self):
        assert _fast_classify("I have dark spots and dullness", EMPTY_PROFILE) == "intake_profile"

    def test_something_else_free_text_routes_to_intake(self):
        assert _fast_classify("something else, I have combination skin", EMPTY_PROFILE) == "intake_profile"


class TestRecommendRouting:

    def test_explicit_recommend_with_ready_profile(self):
        assert _fast_classify("show me what works", PROFILE_READY) == "recommend"

    def test_recommend_keyword_with_ready_profile(self):
        assert _fast_classify("recommend something for me", PROFILE_READY) == "recommend"

    def test_recommend_keyword_without_ready_profile_falls_through(self):
        """'recommend' alone isn't enough — profile must be ready
        (category + skin data) or this should NOT fast-path to recommend.
        It should fall through to category-mention/skin-keyword routing
        or ambiguous (None) -> LLM router."""
        result = _fast_classify("recommend a sunscreen", EMPTY_PROFILE)
        # category keyword "sunscreen" present -> should route to intake,
        # NOT recommend, since profile isn't ready yet
        assert result == "intake_profile"

    def test_recommend_without_profile_and_no_category(self):
        result = _fast_classify("what do you recommend", EMPTY_PROFILE)
        # no category keyword, no skin keyword, profile not ready
        # -> ambiguous, falls through to LLM router
        assert result is None

    def test_category_word_overrides_recommend_trigger_on_stale_profile(self):
        """Regression: 'suggest me some lip balms' with a stale sunscreen profile
        must route to intake_profile so the category is updated before retrieval.
        Category is the strongest constraint — it must never be skipped."""
        stale = {"category": "sunscreen", "skin_types": ["dry"],
                 "allergen_free": ["none"], "price_tier": "any"}
        assert _fast_classify("suggest me some lip balms under 300", stale) == "intake_profile"

    def test_category_word_overrides_recommend_trigger_lip_care_variant(self):
        """'show me lip care options' — 'lip care' is a category keyword even
        though 'show me' is a recommend trigger."""
        stale = {"category": "sunscreen", "skin_types": ["oily"]}
        assert _fast_classify("show me lip care options", stale) == "intake_profile"

    def test_recommend_with_ready_profile_and_no_category_word(self):
        """Pure recommend phrase with no new category → recommend is still correct."""
        assert _fast_classify("show me what works", PROFILE_READY) == "recommend"


class TestAllergenRouting:

    def test_fragrance_free_question(self):
        assert _fast_classify("is this fragrance free?", PROFILE_READY) == "allergen_check"

    def test_contains_ingredient_question(self):
        assert _fast_classify("does this contain alcohol?", PROFILE_READY) == "allergen_check"

    def test_allergen_word_without_question_mark_or_free_contain(self):
        """'ingredient' alone without '?' shouldn't force allergen_check."""
        result = _fast_classify("the ingredient list looks interesting", PROFILE_READY)
        assert result != "allergen_check"

    def test_allergen_preference_chip_routes_to_intake(self):
        """Regression: 'Fragrance-free, No sulfates' (a preference statement,
        not a product question) was being routed to allergen_check because
        'free' in t matched. It must route to intake_profile so the values
        get saved to the profile and auto-recommend can trigger."""
        result = _fast_classify("Fragrance-free, No sulfates", PROFILE_READY)
        assert result == "intake_profile"

    def test_allergen_preference_without_question_mark(self):
        """'I want fragrance-free' is a preference, not a product query."""
        assert _fast_classify("I want fragrance-free products", EMPTY_PROFILE) == "intake_profile"


class TestRoutineRouting:

    def test_routine_order_question(self):
        assert _fast_classify("what goes first in my routine?", PROFILE_READY) == "routine_build"

    def test_layering_question(self):
        assert _fast_classify("how do I layer these products?", PROFILE_READY) == "routine_build"

    def test_am_pm_question(self):
        assert _fast_classify("what's my morning routine?", PROFILE_READY) == "routine_build"


class TestHandoffRouting:

    def test_return_request(self):
        assert _fast_classify("I want to return my order", PROFILE_READY) == "handoff"

    def test_refund_request(self):
        assert _fast_classify("can I get a refund", PROFILE_READY) == "handoff"

    def test_speak_to_human(self):
        assert _fast_classify("I want to speak to someone", PROFILE_READY) == "handoff"

    def test_order_mention_alone_not_handoff(self):
        """Bare 'order' shouldn't trigger handoff — only specific
        return/refund/exchange/delivery/human-contact phrases should.
        'order a sunscreen' type phrasing must not misfire."""
        result = _fast_classify("I want to order a sunscreen", PROFILE_READY)
        assert result != "handoff"

    def test_tracking_phrases_route_to_track_order_not_handoff(self):
        """Regression: 'where is my order' / 'track my order' used to fall
        into the generic handoff playbook ('email support@dotandkey.com'),
        a dead end for a question that has a real answer (the ClickPost
        tracking link). These must route to track_order instead."""
        for msg in [
            "where is my order", "track my order", "track order",
            "order status", "tracking number", "track my package",
        ]:
            assert _fast_classify(msg, PROFILE_READY) == "track_order", (
                f"{msg!r} should route to track_order, not handoff"
            )


class TestTrackingRouting:

    def test_where_is_my_order(self):
        assert _fast_classify("where is my order?", PROFILE_READY) == "track_order"

    def test_track_my_package(self):
        assert _fast_classify("can you track my package", PROFILE_READY) == "track_order"

    def test_order_status(self):
        assert _fast_classify("what's my order status", PROFILE_READY) == "track_order"

    def test_return_request_still_routes_to_handoff(self):
        """Tracking and handoff are now separate playbooks — confirm
        non-tracking handoff reasons (returns, refunds, human escalation)
        are unaffected by splitting tracking out."""
        assert _fast_classify("I want to return my order", PROFILE_READY) == "handoff"


class TestAmbiguousFallsThroughToLLM:

    def test_ingredient_education_question_is_ambiguous(self):
        """No clear keyword match -> should return None so the caller
        knows to invoke the LLM router."""
        result = _fast_classify("what does niacinamide do?", EMPTY_PROFILE)
        assert result is None

    def test_greeting_message_is_ambiguous(self):
        result = _fast_classify("hello", EMPTY_PROFILE)
        assert result is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))