"""
tests/test_sensitivity_memory.py

Tests for backend/sensitivity_memory.py:

  1. Detection — detect_sensitivity_flags() extracts correct flags from text
  2. Ranking  — apply_sensitivity_ranking() boosts FF / ceramide products
                and penalises confirmed-fragranced products
  3. Profile   — parse_profile() deserialises booleans; save_profile serialises them
  4. Multi-session — sensitivity set in one session persists to the next
                     (uses fakeredis, no live Redis required)
  5. Override  — "forget my fragrance preference" clears flags

All tests are self-contained (no FalkorDB, no live LLM).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import fakeredis

from backend.sensitivity_memory import (
    detect_sensitivity_flags,
    apply_sensitivity_ranking,
    sensitivity_from_profile,
    SENSITIVITY_FLAG_FIELDS,
    MEMORY_BOOST_FRAGRANCE_FREE,
    MEMORY_PENALTY_FRAGRANCED,
    MEMORY_BOOST_CERAMIDE,
)


# =============================================================================
# Helpers
# =============================================================================

def _p(title, *, free_from=None, ingredients=None,
       skin_score=1, concern_score=0, price=500, variant=""):
    return {
        "sku": title[:8].upper().replace(" ", "_"),
        "title": title,
        "variant": variant,
        "free_from": free_from or [],
        "ingredients": ingredients or [],
        "skin_score": skin_score,
        "concern_score": concern_score,
        "price": price,
        "match_score": skin_score + concern_score,
        "final_score": skin_score * 30 + concern_score * 25,  # pre-computed baseline
        "query_intent_score": 0,
        "intent_sources": [],
        "url": "",
    }


def _top(products, sensitivity):
    ranked = apply_sensitivity_ranking(list(products), sensitivity)
    return ranked[0]["title"]


# =============================================================================
# 1. Detection — detect_sensitivity_flags()
# =============================================================================

class TestDetectSensitivityFlags:

    def test_react_to_fragrance(self):
        flags = detect_sensitivity_flags("I react to fragrance")
        assert flags.get("fragrance_sensitive") is True
        assert flags.get("avoid_fragrance") is True

    def test_react_badly_to_fragrance(self):
        flags = detect_sensitivity_flags("I react badly to fragrance")
        assert flags.get("fragrance_sensitive") is True
        assert flags.get("avoid_fragrance") is True

    def test_fragrance_irritates(self):
        flags = detect_sensitivity_flags("Fragrance irritates my skin")
        assert flags.get("fragrance_sensitive") is True

    def test_sensitive_to_fragrance(self):
        flags = detect_sensitivity_flags("I am sensitive to fragrance")
        assert flags.get("fragrance_sensitive") is True

    def test_allergic_to_fragrance(self):
        flags = detect_sensitivity_flags("I am allergic to fragrance")
        assert flags.get("fragrance_sensitive") is True
        assert flags.get("avoid_fragrance") is True

    def test_cannot_use_fragrance(self):
        flags = detect_sensitivity_flags("I cannot use fragrance products")
        assert flags.get("fragrance_sensitive") is True

    def test_eczema(self):
        flags = detect_sensitivity_flags("I have eczema")
        assert flags.get("eczema_prone") is True
        assert flags.get("reactive_skin") is True
        assert flags.get("avoid_fragrance") is True

    def test_reactive_skin(self):
        flags = detect_sensitivity_flags("I have reactive skin")
        assert flags.get("reactive_skin") is True
        assert flags.get("avoid_fragrance") is True

    def test_skin_reacts_easily(self):
        flags = detect_sensitivity_flags("My skin reacts easily")
        assert flags.get("reactive_skin") is True

    def test_have_allergies(self):
        flags = detect_sensitivity_flags("I have allergies")
        assert flags.get("allergy_prone") is True
        assert flags.get("avoid_known_allergens") is True

    def test_get_rashes(self):
        flags = detect_sensitivity_flags("I get rashes from skincare")
        assert flags.get("allergy_prone") is True

    def test_generic_message_no_flags(self):
        flags = detect_sensitivity_flags("recommend a moisturizer for dry skin")
        assert flags == {}

    def test_suggest_sunscreen_no_flags(self):
        flags = detect_sensitivity_flags("suggest me a good sunscreen under 600")
        assert flags == {}

    def test_niacinamide_no_flags(self):
        flags = detect_sensitivity_flags("I want a niacinamide serum")
        assert flags == {}

    # ── Forget / override ────────────────────────────────────────────────────

    def test_forget_fragrance_preference(self):
        flags = detect_sensitivity_flags("forget my fragrance preference")
        assert flags.get("avoid_fragrance") is False
        assert flags.get("fragrance_sensitive") is False

    def test_fragrance_doesnt_bother(self):
        flags = detect_sensitivity_flags("fragrance doesn't bother me anymore")
        assert flags.get("avoid_fragrance") is False

    def test_fragrance_does_not_bother(self):
        flags = detect_sensitivity_flags("fragrance does not bother me")
        assert flags.get("avoid_fragrance") is False

    def test_forget_sensitivity(self):
        flags = detect_sensitivity_flags("forget my sensitivity")
        # All flags should be cleared
        assert all(flags.get(f) is False for f in SENSITIVITY_FLAG_FIELDS if f in flags)


# =============================================================================
# 2. Ranking — apply_sensitivity_ranking()
# =============================================================================

class TestApplySensitivityRanking:

    _moist = [
        _p("Peptide Daily Cream", skin_score=3,
           ingredients=["Peptide", "Fragrance"]),
        _p("Ceramide Daily Moisturizer", skin_score=3,
           ingredients=["Ceramide", "Fragrance"]),
        _p("Fragrance-Free Barrier Moisturizer",
           free_from=["Fragrance"], skin_score=1,
           ingredients=["Ceramide", "Niacinamide"]),
    ]

    _sun = [
        _p("Watermelon Sunscreen SPF 50", skin_score=2,
           ingredients=["Watermelon Extract", "Fragrance"]),
        _p("Cica Sunscreen SPF 50 FF",
           free_from=["Fragrance"], skin_score=1,
           ingredients=["Centella", "Zinc Oxide"]),
    ]

    # ── avoid_fragrance ──────────────────────────────────────────────────────

    def test_avoid_fragrance_boosts_ff_moisturizer(self):
        top = _top(self._moist, {"avoid_fragrance": True})
        assert "fragrance" in top.lower()

    def test_avoid_fragrance_boosts_ff_sunscreen(self):
        top = _top(self._sun, {"avoid_fragrance": True})
        assert "ff" in top.lower() or "fragrance" in top.lower()

    def test_fragrance_sensitive_flag_also_boosts_ff(self):
        top = _top(self._moist, {"fragrance_sensitive": True})
        assert "fragrance" in top.lower()

    def test_avoid_fragrance_penalises_confirmed_fragranced(self):
        """Fragranced product (confirmed in ingredients) must score LOWER than FF."""
        ff = _p("FF Moisturizer", free_from=["Fragrance"],
                skin_score=1, ingredients=["Ceramide"])
        fragranced = _p("Scented Cream", skin_score=10,
                        ingredients=["Ceramide", "Fragrance"])
        ranked = apply_sensitivity_ranking(
            [fragranced, ff], {"avoid_fragrance": True}
        )
        assert ranked[0]["title"] == ff["title"], (
            "FF moisturizer must outrank fragranced even with 10× higher skin_score"
        )

    def test_no_penalty_without_ingredient_data(self):
        """Products with no ingredient list must NOT be penalised.
        With equal skin_score, the FF boost alone decides ranking."""
        ff = _p("FF Cream", free_from=["Fragrance"], skin_score=2)
        unknown = _p("Unknown Cream", skin_score=2)  # equal skin_score, no ingredients
        ranked = apply_sensitivity_ranking([unknown, ff], {"avoid_fragrance": True})
        # FF gets boost (wins); unknown does NOT get a fragrance penalty
        assert ranked[0]["title"] == ff["title"]
        assert unknown.get("fragrance_penalty", 0) == 0

    def test_empty_sensitivity_dict_is_noop(self):
        """No sensitivity → products returned in original order."""
        products = [
            _p("A", skin_score=3),
            _p("B", skin_score=2),
            _p("C", skin_score=1),
        ]
        original = [p["title"] for p in products]
        apply_sensitivity_ranking(products, {})
        assert [p["title"] for p in products] == original

    def test_all_false_sensitivity_is_noop(self):
        products = [_p("A", skin_score=3), _p("B", skin_score=1)]
        original = [p["title"] for p in products]
        apply_sensitivity_ranking(products, {k: False for k in SENSITIVITY_FLAG_FIELDS})
        assert [p["title"] for p in products] == original

    # ── reactive_skin / eczema_prone ─────────────────────────────────────────

    def test_reactive_skin_boosts_ceramide_product(self):
        products = [
            _p("Peptide Moisturizer", skin_score=3, ingredients=["Peptide"]),
            _p("Ceramide Barrier Moisturizer", skin_score=1,
               ingredients=["Ceramide"], free_from=["Fragrance"]),
        ]
        top = _top(products, {"reactive_skin": True})
        assert "ceramide" in top.lower()

    def test_eczema_prone_boosts_ceramide_and_ff(self):
        products = [
            _p("Plain Moisturizer", skin_score=3, ingredients=["Glycerin"]),
            _p("Ceramide Fragrance-Free Cream", skin_score=1,
               ingredients=["Ceramide"], free_from=["Fragrance"]),
        ]
        top = _top(products, {"eczema_prone": True})
        assert "ceramide" in top.lower()

    def test_eczema_avoids_fragranced_product(self):
        """eczema_prone = True implies avoid_fragrance = True in apply_sensitivity_ranking."""
        ff = _p("FF Barrier Cream", free_from=["Fragrance"],
                skin_score=1, ingredients=["Ceramide"])
        fragranced = _p("Scented Cream", skin_score=5,
                        ingredients=["Fragrance"])
        top = _top([fragranced, ff], {"eczema_prone": True})
        assert top == ff["title"]

    # ── allergy_prone ────────────────────────────────────────────────────────

    def test_allergy_prone_boosts_ff_product(self):
        products = [
            _p("Standard Moisturizer", skin_score=3),
            _p("Allergen-Free Moisturizer", free_from=["Fragrance", "Paraben"],
               skin_score=1, ingredients=["Niacinamide"]),
        ]
        top = _top(products, {"allergy_prone": True})
        assert "allergen" in top.lower() or "free" in top.lower()

    # ── Debug fields ─────────────────────────────────────────────────────────

    def test_memory_boost_field_set_on_ff_product(self):
        products = [_p("FF Cream", free_from=["Fragrance"], ingredients=["Ceramide"])]
        apply_sensitivity_ranking(products, {"avoid_fragrance": True})
        assert products[0]["memory_boost"] == MEMORY_BOOST_FRAGRANCE_FREE

    def test_fragrance_penalty_field_set_on_fragranced_product(self):
        products = [_p("Scented Cream", ingredients=["Ceramide", "Fragrance"])]
        apply_sensitivity_ranking(products, {"avoid_fragrance": True})
        assert products[0]["fragrance_penalty"] == MEMORY_PENALTY_FRAGRANCED

    def test_memory_boost_zero_without_sensitivity(self):
        products = [_p("FF Cream", free_from=["Fragrance"])]
        apply_sensitivity_ranking(products, {})
        assert products[0].get("memory_boost", 0) == 0

    def test_memory_boost_formula(self):
        """final_score = original_final + memory_boost - penalty."""
        p = _p("FF Cream", free_from=["Fragrance"], skin_score=2,
               ingredients=["Ceramide"])
        original_final = p["final_score"]
        apply_sensitivity_ranking([p], {"avoid_fragrance": True})
        assert p["final_score"] == original_final + MEMORY_BOOST_FRAGRANCE_FREE

    def test_ceramide_boost_added_for_reactive_skin(self):
        products = [_p("Ceramide Cream", ingredients=["Ceramide"])]
        apply_sensitivity_ranking(products, {"reactive_skin": True})
        assert products[0]["memory_boost"] >= MEMORY_BOOST_CERAMIDE


# =============================================================================
# 3. Profile parsing — boolean round-trip through parse_profile / save_profile
# =============================================================================

class TestProfileBooleanRoundTrip:
    """Verify parse_profile converts "true"/"false" strings to booleans."""

    def test_parse_true_string(self):
        from backend.profile import parse_profile
        raw = {"avoid_fragrance": "true", "skin_types": "oily"}
        parsed = parse_profile(raw)
        assert parsed["avoid_fragrance"] is True

    def test_parse_false_string(self):
        from backend.profile import parse_profile
        raw = {"avoid_fragrance": "false"}
        parsed = parse_profile(raw)
        assert parsed["avoid_fragrance"] is False

    def test_parse_missing_flag_defaults_false(self):
        from backend.profile import parse_profile
        raw = {"skin_types": "dry"}  # no sensitivity flags
        parsed = parse_profile(raw)
        assert parsed["avoid_fragrance"] is False

    def test_parse_already_bool_passthrough(self):
        from backend.profile import parse_profile
        raw = {"avoid_fragrance": True}   # already bool
        parsed = parse_profile(raw)
        assert parsed["avoid_fragrance"] is True

    def test_sensitivity_from_profile_extracts_flags(self):
        parsed = {
            "avoid_fragrance": True,
            "fragrance_sensitive": False,
            "eczema_prone": True,
            "skin_types": ["dry"],
        }
        sensitivity = sensitivity_from_profile(parsed)
        assert sensitivity["avoid_fragrance"] is True
        assert sensitivity["fragrance_sensitive"] is False
        assert sensitivity["eczema_prone"] is True
        # Non-flag field not included
        assert "skin_types" not in sensitivity


# =============================================================================
# 4. Multi-session persistence (fakeredis)
# =============================================================================

# Singleton fakeredis instance — avoids 3-4s init cost per test
import fakeredis as _fakeredis_module
_fake_redis_singleton = _fakeredis_module.FakeRedis(decode_responses=True)


@pytest.fixture(scope="module")
def fake_redis():
    _fake_redis_singleton.flushall()
    return _fake_redis_singleton


@pytest.fixture(autouse=True)
def _patch_redis(monkeypatch, fake_redis):
    """Redirect profile.py's get_redis() to the fakeredis instance."""
    import backend.profile as _profile_module
    monkeypatch.setattr(_profile_module, "_redis", fake_redis)
    yield
    fake_redis.flushall()


class TestMultiSessionPersistence:
    """Simulate two separate sessions: set sensitivity in session 1,
    verify it influences ranking in session 2."""

    def test_fragrance_sensitivity_persists_across_sessions(self):
        """Session 1: user says 'I react to fragrance'.
        Session 2: generic 'recommend a moisturizer'.
        FF product must still rank first in session 2."""
        from backend.profile import save_profile, load_profile, parse_profile
        from backend.sensitivity_memory import detect_sensitivity_flags

        profile_id = "test_multi_session_fragrance"

        # Session 1: detect + persist
        flags = detect_sensitivity_flags("I react to fragrance")
        assert flags.get("avoid_fragrance") is True
        save_profile(profile_id, flags)

        # Session 2: load profile, extract sensitivity, apply ranking
        raw = load_profile(profile_id)
        parsed = parse_profile(raw)
        sensitivity = sensitivity_from_profile(parsed)
        assert sensitivity["avoid_fragrance"] is True   # persisted ✓

        products = [
            _p("Peptide Daily Cream", skin_score=3, ingredients=["Peptide", "Fragrance"]),
            _p("Fragrance-Free Ceramide Cream", free_from=["Fragrance"],
               skin_score=1, ingredients=["Ceramide"]),
        ]
        ranked = apply_sensitivity_ranking(list(products), sensitivity)
        assert "fragrance" in ranked[0]["title"].lower() or \
               "ff" in ranked[0]["title"].lower()
        assert ranked[0]["free_from"] == ["Fragrance"], (
            "FF product must rank first in session 2 without user repeating preference"
        )

    def test_eczema_flag_persists(self):
        from backend.profile import save_profile, load_profile, parse_profile

        profile_id = "test_multi_session_eczema"
        flags = detect_sensitivity_flags("I have eczema")
        save_profile(profile_id, flags)

        raw = load_profile(profile_id)
        parsed = parse_profile(raw)
        sensitivity = sensitivity_from_profile(parsed)

        assert sensitivity["eczema_prone"] is True
        assert sensitivity["avoid_fragrance"] is True

    def test_forget_clears_fragrance_flag(self):
        """Session 1: set fragrance sensitivity.
        Session 2: user says 'forget my fragrance preference'.
        Session 3: ranking returns to normal (no FF boost)."""
        from backend.profile import save_profile, load_profile, parse_profile

        profile_id = "test_forget_fragrance"

        # Session 1: set sensitivity
        save_profile(profile_id, detect_sensitivity_flags("I react to fragrance"))
        raw = load_profile(profile_id)
        assert parse_profile(raw)["avoid_fragrance"] is True

        # Session 2: forget
        forget_flags = detect_sensitivity_flags("forget my fragrance preference")
        assert forget_flags.get("avoid_fragrance") is False
        save_profile(profile_id, forget_flags)

        # Session 3: sensitivity should be cleared
        raw = load_profile(profile_id)
        parsed = parse_profile(raw)
        assert parsed["avoid_fragrance"] is False, "Flag must be cleared after forget"

        # Ranking returns to normal
        products = [
            _p("Peptide Cream", skin_score=5),
            _p("FF Ceramide Cream", free_from=["Fragrance"], skin_score=1),
        ]
        sensitivity = sensitivity_from_profile(parsed)
        ranked = apply_sensitivity_ranking(list(products), sensitivity)
        # No memory boost → Peptide Cream (higher skin_score) should rank first
        assert ranked[0]["title"] == "Peptide Cream"

    def test_allergy_prone_persists(self):
        from backend.profile import save_profile, load_profile, parse_profile

        profile_id = "test_allergy_prone"
        flags = detect_sensitivity_flags("I have allergies to skincare")
        save_profile(profile_id, flags)

        parsed = parse_profile(load_profile(profile_id))
        sensitivity = sensitivity_from_profile(parsed)
        assert sensitivity["allergy_prone"] is True


# =============================================================================
# 5. Ranking priority: current query intent must override memory
# =============================================================================

class TestQueryOverridesMemory:
    """When the user has avoid_fragrance=True but makes a specific ingredient
    query, the ingredient match must still dominate (qi×80 >> memory boost)."""

    def test_niacinamide_query_beats_fragrance_memory(self):
        """Fragrance-sensitive user asks 'niacinamide sunscreen'.
        A niacinamide sunscreen (with fragrance) must outrank a generic FF sunscreen."""
        from backend.query_intent import rerank_by_query_intent, extract_query_tokens

        products = [
            _p("Niacinamide Sunscreen SPF 50", skin_score=1,
               ingredients=["Niacinamide", "Fragrance"]),      # has niacinamide + fragrance
            _p("Fragrance-Free Generic Sunscreen", free_from=["Fragrance"],
               skin_score=1, ingredients=["Zinc Oxide"]),       # FF but no niacinamide
        ]

        # Simulate the ranking pipeline
        tokens = extract_query_tokens("niacinamide sunscreen")
        ranked_qi = rerank_by_query_intent(list(products), tokens)

        sensitivity = {"avoid_fragrance": True}
        ranked_final = apply_sensitivity_ranking(ranked_qi, sensitivity)

        # Niacinamide sunscreen: qi = 200 (title+ingredient) × 80 = 16000
        #                        fragrance penalty = 100
        #                        net = 15900
        # FF generic:            qi = 0; memory boost = 200
        #                        net = 200
        # → Niacinamide sunscreen still wins
        assert "niacinamide" in ranked_final[0]["title"].lower(), (
            "Explicit ingredient query must dominate memory-based preference"
        )

    def test_memory_dominates_generic_query(self):
        """For a generic 'recommend moisturizer' query (no ingredient tokens),
        memory boost decides ranking — FF must win over higher skin_score product."""
        products = [
            _p("Peptide Cream", skin_score=5, ingredients=["Peptide", "Fragrance"]),
            _p("FF Ceramide Cream", free_from=["Fragrance"], skin_score=1,
               ingredients=["Ceramide"]),
        ]
        # No query tokens → rerank_by_query_intent is a no-op (qi=0)
        from backend.query_intent import rerank_by_query_intent
        rerank_by_query_intent(products, [])

        ranked = apply_sensitivity_ranking(list(products), {"avoid_fragrance": True})
        assert "ff" in ranked[0]["title"].lower() or \
               "fragrance" in ranked[0]["title"].lower()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
