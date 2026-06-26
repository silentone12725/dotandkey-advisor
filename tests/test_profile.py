"""
tests/test_profile.py

Tests for backend/profile.py using fakeredis — an in-memory Redis-compatible
stub, so these run without a real Redis/FalkorDB instance.

Regression coverage: parse_profile() used to crash with AttributeError
when called on an already-parsed dict (list fields, not comma-separated
strings) — this happened in production when profile_is_ready() was
called on the output of an earlier parse_profile() call. See
test_parse_profile_idempotent below.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fakeredis
import pytest

import backend.profile as profile_mod


@pytest.fixture(scope="session")
def _fake_redis_singleton():
    """One FakeRedis instance for the entire test session.

    fakeredis pays a real one-time internal setup cost (observed ~3-4s
    on Python 3.14) on the FIRST command issued against a given instance
    — everything after that is fast. Constructing a fresh instance per
    test (the original design) repaid that cost on every test that
    touched Redis, turning a sub-second suite into ~49s. Session scope
    pays it exactly once; flushall() in the per-test fixture below
    keeps tests isolated without reconstructing the instance.
    """
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture(autouse=True)
def fake_redis(_fake_redis_singleton, monkeypatch):
    """Point backend.profile's Redis singleton at the shared fake
    instance, and clear all data before each test for isolation."""
    _fake_redis_singleton.flushall()
    monkeypatch.setattr(profile_mod, "_redis", _fake_redis_singleton)
    yield _fake_redis_singleton


class TestParseProfile:

    def test_parses_comma_separated_strings_to_lists(self):
        raw = {"skin_types": "oily,combination", "concerns": "acne,excess_oil",
               "allergen_free": "", "category": "sunscreen"}
        out = profile_mod.parse_profile(raw)
        assert out["skin_types"] == ["oily", "combination"]
        assert out["concerns"] == ["acne", "excess_oil"]
        assert out["allergen_free"] == []

    def test_empty_string_field_becomes_empty_list(self):
        raw = {"skin_types": "", "concerns": "", "allergen_free": ""}
        out = profile_mod.parse_profile(raw)
        assert out["skin_types"] == []
        assert out["concerns"] == []
        assert out["allergen_free"] == []

    def test_parse_profile_idempotent(self):
        """Regression test: calling parse_profile() twice (once directly,
        once indirectly via profile_is_ready) used to crash with
        AttributeError: 'list' object has no attribute 'split'."""
        raw = {"skin_types": "oily", "concerns": "acne,excess_oil",
               "category": "sunscreen", "allergen_free": ""}
        once = profile_mod.parse_profile(raw)
        twice = profile_mod.parse_profile(once)   # must NOT raise
        assert once == twice

    def test_single_value_field_still_a_list(self):
        raw = {"skin_types": "oily", "concerns": "", "allergen_free": ""}
        out = profile_mod.parse_profile(raw)
        assert out["skin_types"] == ["oily"]

    def test_preserves_non_list_fields(self):
        raw = {"skin_types": "oily", "concerns": "", "allergen_free": "",
               "category": "sunscreen", "price_tier": "under_600"}
        out = profile_mod.parse_profile(raw)
        assert out["category"] == "sunscreen"
        assert out["price_tier"] == "under_600"


class TestProfileIsReady:

    def test_ready_when_all_five_required_fields_present(self):
        raw = {
            "category": "sunscreen", "skin_types": "oily",
            "price_tier": "under_600", "allergen_free": "none",
            "size_pref": "standard",
        }
        assert profile_mod.profile_is_ready(raw) is True

    def test_not_ready_without_category(self):
        raw = {"category": "", "skin_types": "oily",
               "price_tier": "under_600", "size_pref": "standard"}
        assert profile_mod.profile_is_ready(raw) is False

    def test_not_ready_without_skin_types(self):
        raw = {"category": "sunscreen", "skin_types": "",
               "price_tier": "under_600", "size_pref": "standard"}
        assert profile_mod.profile_is_ready(raw) is False

    def test_not_ready_without_price_tier(self):
        raw = {"category": "sunscreen", "skin_types": "oily",
               "price_tier": "", "size_pref": "standard"}
        assert profile_mod.profile_is_ready(raw) is False

    def test_not_ready_without_size_pref(self):
        raw = {"category": "sunscreen", "skin_types": "oily",
               "price_tier": "under_600", "size_pref": ""}
        assert profile_mod.profile_is_ready(raw) is False

    def test_not_ready_without_allergen_free(self):
        raw = {"category": "sunscreen", "skin_types": "oily",
               "price_tier": "under_600", "allergen_free": "",
               "size_pref": "standard"}
        assert profile_mod.profile_is_ready(raw) is False

    def test_ready_accepts_already_parsed_input(self):
        """profile_is_ready must work whether given a raw Redis dict
        (comma-separated strings) or an already-parsed dict (lists) —
        this is the exact double-parse regression."""
        raw = {"category": "sunscreen", "skin_types": "oily",
               "price_tier": "under_600", "allergen_free": "none",
               "size_pref": "standard"}
        parsed_once = profile_mod.parse_profile(raw)
        assert profile_mod.profile_is_ready(parsed_once) is True   # must not crash


class TestProfileMissingFields:

    def test_empty_profile_missing_all_required(self):
        raw = {}
        missing = profile_mod.profile_missing_fields(raw)
        assert "category" in missing
        assert "skin_types" in missing
        assert "price_tier" in missing
        assert "allergen_free" in missing
        assert "size_pref" in missing

    def test_allergen_free_required_before_price_tier(self):
        """allergen_free is a required field that appears between
        skin_types and price_tier (before budget)."""
        raw = {"category": "sunscreen", "skin_types": "oily", "allergen_free": ""}
        missing = profile_mod.profile_missing_fields(raw)
        assert "allergen_free" in missing
        # price_tier and size_pref should follow allergen_free
        assert "price_tier" in missing
        assert "size_pref" in missing
        # concerns only after all required fields including allergen_free
        assert "concerns" not in missing

    def test_concerns_optional_after_all_required_complete(self):
        """concerns only appears once all five required fields are set."""
        raw = {
            "category": "sunscreen", "skin_types": "oily",
            "price_tier": "under_600", "allergen_free": "none",
            "size_pref": "standard",
        }
        missing = profile_mod.profile_missing_fields(raw)
        assert "concerns" in missing
        # all required fields are set — must not appear again
        assert "category" not in missing
        assert "price_tier" not in missing
        assert "allergen_free" not in missing

    def test_fully_complete_profile_has_no_missing(self):
        raw = {
            "category": "sunscreen", "skin_types": "oily", "concerns": "acne",
            "price_tier": "under_600", "size_pref": "standard",
            "allergen_free": "fragrance",
        }
        missing = profile_mod.profile_missing_fields(raw)
        assert missing == []


class TestSaveAndLoadProfile:

    def test_save_then_load_roundtrip(self, fake_redis):
        profile_mod.save_profile("user-1", {"skin_types": ["oily"], "category": "sunscreen"})
        loaded = profile_mod.load_profile("user-1")
        assert loaded["category"] == "sunscreen"
        assert loaded["skin_types"] == "oily"   # stored as comma-joined string

    def test_save_merges_not_overwrites(self, fake_redis):
        profile_mod.save_profile("user-2", {"skin_types": ["oily"]})
        profile_mod.save_profile("user-2", {"category": "sunscreen"})
        loaded = profile_mod.load_profile("user-2")
        assert loaded["skin_types"] == "oily"
        assert loaded["category"] == "sunscreen"

    def test_load_nonexistent_profile_returns_empty_dict(self, fake_redis):
        loaded = profile_mod.load_profile("never-seen-user")
        assert loaded == {}

    def test_list_field_serialised_as_comma_string(self, fake_redis):
        profile_mod.save_profile("user-3", {"concerns": ["acne", "dark_spots"]})
        loaded = profile_mod.load_profile("user-3")
        assert loaded["concerns"] == "acne,dark_spots"

    def test_ttl_set_on_save(self, fake_redis):
        profile_mod.save_profile("user-4", {"category": "sunscreen"})
        ttl = fake_redis.ttl("profile:user-4")
        assert ttl > 0
        assert ttl <= profile_mod.PROFILE_TTL


class TestHistoryStore:

    def test_record_and_load_event(self, fake_redis):
        profile_mod.record_event("user-5", "DK_CCMS", "sunscreen", "R", 445)
        history = profile_mod.load_history("user-5")
        assert history["status"] == "browsing"   # only R events, no purchase
        assert "DK_CCMS" in history["block"]

    def test_purchase_event_sets_active_status(self, fake_redis):
        profile_mod.record_event("user-6", "DK_CCMS", "sunscreen", "P", 445)
        history = profile_mod.load_history("user-6")
        assert history["status"] == "active"
        assert history["has_purchases"] is True

    def test_no_history_returns_lapsed(self, fake_redis):
        history = profile_mod.load_history("never-purchased-user")
        assert history["status"] == "lapsed"

    def test_history_legend_included_in_block(self, fake_redis):
        profile_mod.record_event("user-7", "DK_X", "moisturizer", "R", 500)
        history = profile_mod.load_history("user-7")
        assert "cat:SC=sunscreen" in history["block"]   # legend present

    def test_category_abbreviated_correctly(self, fake_redis):
        profile_mod.record_event("user-8", "DK_X", "sunscreen", "P", 445)
        history = profile_mod.load_history("user-8")
        assert "DK_X|SC|P|445" in history["block"]


class TestCompactProfileForPrompt:

    def test_new_user_returns_placeholder(self, fake_redis):
        line = profile_mod.compact_profile_for_prompt("brand-new-user")
        assert line == "profile: new_user"

    def test_populated_profile_formats_compactly(self, fake_redis):
        profile_mod.save_profile("user-9", {
            "skin_types": ["oily"], "concerns": ["acne"], "category": "sunscreen",
        })
        line = profile_mod.compact_profile_for_prompt("user-9")
        assert "skin:oily" in line
        assert "concerns:acne" in line
        assert "cat:sunscreen" in line


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))