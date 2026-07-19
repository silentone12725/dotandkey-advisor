"""
tests/test_returning_user.py

Tests for backend/playbooks/returning_user.py — the structured "welcome
back" flow that replaces free-text handling of the returning-user greeting.

Delegations to recommend.run() / intake_profile.run() (which need a live
FalkorDB connection) are stubbed with async-generator fakes that record
their call arguments — these tests verify the STATE MACHINE and profile/
session writes, not the downstream playbooks themselves (those have their
own test files).

Uses fakeredis, following the session-scoped-fixture pattern established
in test_profile.py / test_sensitivity_memory.py / test_behavioral_learning.py
to avoid the ~3-4s fakeredis cold-init cost per test.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import fakeredis as _fakeredis_module

from backend.playbooks import returning_user as ru
from backend.profile import load_profile, parse_profile, save_profile

# =============================================================================
# Fakeredis fixture
# =============================================================================

_fake_redis = _fakeredis_module.FakeRedis(decode_responses=True)


@pytest.fixture(scope="module")
def fake_redis_instance():
    _fake_redis.flushall()
    return _fake_redis


@pytest.fixture(autouse=True)
def _patch_redis(monkeypatch, fake_redis_instance):
    import backend.profile as _pm
    monkeypatch.setattr(_pm, "_redis", fake_redis_instance)
    yield
    fake_redis_instance.flushall()


def _pid(name: str) -> str:
    return f"test_returning_{name}"


# =============================================================================
# Stub delegate playbooks (avoid requiring live FalkorDB)
# =============================================================================

class _Recorder:
    """Records calls to a stubbed playbook's run()."""
    def __init__(self):
        self.calls = []

    def make_stub(self, reply="stub reply"):
        async def _stub(profile_id, user_message, router_args):
            self.calls.append((profile_id, user_message, router_args))
            yield reply
        return _stub


@pytest.fixture
def recommend_stub(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(
        "backend.playbooks.recommend.run", rec.make_stub("recommend reply")
    )
    return rec


@pytest.fixture
def intake_stub(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(
        "backend.playbooks.intake_profile.run", rec.make_stub("intake reply")
    )
    return rec


async def _collect(agen):
    out = []
    async for tok in agen:
        out.append(tok)
    return out


def _ui_data(tokens):
    """Extract the decoded {field, multi_select, options} chip payload from
    a token list, if any (unwraps the outer {"suggested_chips": ...})."""
    from backend.playbooks.base import try_extract_ui_data
    for tok in tokens:
        data = try_extract_ui_data(tok)
        if data is not None:
            return data.get("suggested_chips")
    return None


# =============================================================================
# 1. Session-state helpers
# =============================================================================

class TestSessionStateHelpers:

    def test_get_returning_step_empty_by_default(self):
        assert ru.get_returning_step(_pid("fresh")) == ""

    def test_set_and_get_round_trip(self):
        pid = _pid("set_get")
        ru.set_returning_step(pid, "awaiting_choice")
        assert ru.get_returning_step(pid) == "awaiting_choice"

    def test_clear_resets_step_and_pending_factors(self):
        pid = _pid("clear")
        ru.set_returning_step(pid, "awaiting_change_factors_selection")
        ru._set_pending_factors(pid, ["skin_types", "price_tier"])
        ru.clear_returning_step(pid)
        assert ru.get_returning_step(pid) == ""
        assert ru._get_pending_factors(pid) == []

    def test_pending_factors_round_trip(self):
        pid = _pid("pending")
        ru._set_pending_factors(pid, ["concerns", "texture"])
        assert ru._get_pending_factors(pid) == ["concerns", "texture"]

    def test_pending_factors_empty_by_default(self):
        assert ru._get_pending_factors(_pid("pending_fresh")) == []


# =============================================================================
# 2. Entry choice (awaiting_choice)
# =============================================================================

class TestEntryChoice:

    @pytest.mark.asyncio
    async def test_same_as_before_advances_step(self):
        pid = _pid("entry_same")
        tokens = await _collect(ru.run(pid, "Same as before", {"step": "awaiting_choice"}))
        assert ru.get_returning_step(pid) == "awaiting_same_choice"
        chips = _ui_data(tokens)
        assert chips is not None
        assert {o["value"] for o in chips["options"]} == {"continue", "new"}

    @pytest.mark.asyncio
    async def test_something_has_changed_advances_step(self):
        pid = _pid("entry_changed")
        tokens = await _collect(ru.run(pid, "Something has changed", {"step": "awaiting_choice"}))
        assert ru.get_returning_step(pid) == "awaiting_change_factors_selection"
        chips = _ui_data(tokens)
        assert chips["multi_select"] is True
        values = {o["value"] for o in chips["options"]}
        assert values == {"category", "skin_types", "concerns", "texture", "allergen_free", "price_tier"}

    @pytest.mark.asyncio
    async def test_concerns_advances_step(self):
        pid = _pid("entry_concerns")
        tokens = await _collect(
            ru.run(pid, "Have concerns with a previous purchase", {"step": "awaiting_choice"})
        )
        assert ru.get_returning_step(pid) == "awaiting_concern_details"
        assert any("what happened" in t.lower() for t in tokens)

    @pytest.mark.asyncio
    async def test_unrecognized_reply_reshows_entry_chips(self):
        pid = _pid("entry_unrecognized")
        ru.set_returning_step(pid, "awaiting_choice")
        tokens = await _collect(ru.run(pid, "blah blah nonsense", {"step": "awaiting_choice"}))
        # step unchanged — still waiting for a valid choice
        assert ru.get_returning_step(pid) == "awaiting_choice"
        chips = _ui_data(tokens)
        assert {o["value"] for o in chips["options"]} == {"same", "changed", "concerns"}


# =============================================================================
# 3. Same-as-before sub-choice
# =============================================================================

class TestSameChoice:

    @pytest.mark.asyncio
    async def test_continue_delegates_to_recommend_and_clears_step(self, recommend_stub):
        pid = _pid("same_continue")
        ru.set_returning_step(pid, "awaiting_same_choice")
        tokens = await _collect(
            ru.run(pid, "Continue where I left off", {"step": "awaiting_same_choice"})
        )
        assert ru.get_returning_step(pid) == ""
        assert tokens == ["recommend reply"]
        assert len(recommend_stub.calls) == 1
        assert recommend_stub.calls[0][0] == pid

    @pytest.mark.asyncio
    async def test_browse_new_clears_category_and_delegates_to_intake(self, intake_stub):
        pid = _pid("same_browse_new")
        save_profile(pid, {"category": "sunscreen", "skin_types": ["oily"]})
        ru.set_returning_step(pid, "awaiting_same_choice")

        tokens = await _collect(
            ru.run(pid, "Browse something new", {"step": "awaiting_same_choice"})
        )
        assert ru.get_returning_step(pid) == ""
        assert tokens == ["intake reply"]
        assert len(intake_stub.calls) == 1

        profile = parse_profile(load_profile(pid))
        assert profile.get("category") == ""
        assert profile.get("skin_types") == ["oily"]   # untouched

    @pytest.mark.asyncio
    async def test_unrecognized_reshows_subchips(self):
        pid = _pid("same_unrecognized")
        ru.set_returning_step(pid, "awaiting_same_choice")
        tokens = await _collect(ru.run(pid, "huh?", {"step": "awaiting_same_choice"}))
        assert ru.get_returning_step(pid) == "awaiting_same_choice"
        chips = _ui_data(tokens)
        assert {o["value"] for o in chips["options"]} == {"continue", "new"}


# =============================================================================
# 4. "Something has changed" — factor selection + sequential collection
# =============================================================================

class TestFactorSelectionFlow:

    @pytest.mark.asyncio
    async def test_single_factor_selected_asks_for_it(self):
        pid = _pid("factor_single")
        save_profile(pid, {"skin_types": ["oily"]})
        ru.set_returning_step(pid, "awaiting_change_factors_selection")

        tokens = await _collect(
            ru.run(pid, "Skin type", {"step": "awaiting_change_factors_selection"})
        )
        assert ru.get_returning_step(pid) == "awaiting_factor_value:skin_types"
        assert ru._get_pending_factors(pid) == []
        # field cleared up front
        profile = parse_profile(load_profile(pid))
        assert profile.get("skin_types") == []
        chips = _ui_data(tokens)
        assert chips["field"] == "skin_types"

    @pytest.mark.asyncio
    async def test_multi_select_queues_remaining_factors(self):
        pid = _pid("factor_multi")
        tokens = await _collect(
            ru.run(pid, "Skin type, Budget", {"step": "awaiting_change_factors_selection"})
        )
        # first selected factor asked now, second queued
        assert ru.get_returning_step(pid) == "awaiting_factor_value:skin_types"
        assert ru._get_pending_factors(pid) == ["price_tier"]
        chips = _ui_data(tokens)
        assert chips["field"] == "skin_types"

    @pytest.mark.asyncio
    async def test_no_recognized_factor_reshows_chips(self):
        pid = _pid("factor_none")
        ru.set_returning_step(pid, "awaiting_change_factors_selection")
        tokens = await _collect(
            ru.run(pid, "something unrelated", {"step": "awaiting_change_factors_selection"})
        )
        assert ru.get_returning_step(pid) == "awaiting_change_factors_selection"
        chips = _ui_data(tokens)
        assert chips["multi_select"] is True

    @pytest.mark.asyncio
    async def test_factor_value_chip_click_saves_and_advances_to_next(self):
        pid = _pid("factor_value_advance")
        ru.set_returning_step(pid, "awaiting_factor_value:skin_types")
        ru._set_pending_factors(pid, ["price_tier"])

        tokens = await _collect(
            ru.run(pid, "Dry", {"step": "awaiting_factor_value:skin_types"})
        )
        profile = parse_profile(load_profile(pid))
        assert profile.get("skin_types") == ["dry"]

        assert ru.get_returning_step(pid) == "awaiting_factor_value:price_tier"
        assert ru._get_pending_factors(pid) == []
        chips = _ui_data(tokens)
        assert chips["field"] == "price_tier"

    @pytest.mark.asyncio
    async def test_last_factor_value_falls_through_to_recommend(self, recommend_stub):
        pid = _pid("factor_value_last")
        ru.set_returning_step(pid, "awaiting_factor_value:price_tier")
        ru._set_pending_factors(pid, [])

        tokens = await _collect(
            ru.run(pid, "Under ₹600", {"step": "awaiting_factor_value:price_tier"})
        )
        assert ru.get_returning_step(pid) == ""
        assert tokens == ["recommend reply"]
        assert len(recommend_stub.calls) == 1

        profile = parse_profile(load_profile(pid))
        assert profile.get("price_tier") == "under_600"

    @pytest.mark.asyncio
    async def test_full_multi_factor_sequence_end_to_end(self, recommend_stub):
        """Simulates the complete chip-by-chip conversation a real user
        would have after selecting 'Skin type, Budget'."""
        pid = _pid("factor_full_sequence")

        # Step A: user selects two factors to change
        await _collect(ru.run(pid, "Skin type, Budget", {"step": "awaiting_change_factors_selection"}))
        assert ru.get_returning_step(pid) == "awaiting_factor_value:skin_types"

        # Step B: user answers skin type
        await _collect(ru.run(pid, "Combination", {"step": "awaiting_factor_value:skin_types"}))
        assert ru.get_returning_step(pid) == "awaiting_factor_value:price_tier"

        # Step C: user answers budget — last factor, falls through to recommend
        tokens = await _collect(ru.run(pid, "Under ₹300", {"step": "awaiting_factor_value:price_tier"}))
        assert ru.get_returning_step(pid) == ""
        assert tokens == ["recommend reply"]

        profile = parse_profile(load_profile(pid))
        assert profile.get("skin_types") == ["combination"]
        assert profile.get("price_tier") == "under_300"


# =============================================================================
# 5. "Concerns with a previous purchase"
# =============================================================================

class TestConcernDetails:

    @pytest.mark.asyncio
    async def test_clears_step_and_responds(self):
        pid = _pid("concern_basic")
        ru.set_returning_step(pid, "awaiting_concern_details")
        tokens = await _collect(
            ru.run(pid, "the moisturizer felt heavy", {"step": "awaiting_concern_details"})
        )
        assert ru.get_returning_step(pid) == ""
        assert any("thanks for letting me know" in t.lower() for t in tokens)

    @pytest.mark.asyncio
    async def test_reaction_message_persists_sensitivity_flags(self):
        pid = _pid("concern_sensitivity")
        ru.set_returning_step(pid, "awaiting_concern_details")
        await _collect(
            ru.run(pid, "I react to fragrance in your serums", {"step": "awaiting_concern_details"})
        )
        profile = parse_profile(load_profile(pid))
        assert profile.get("avoid_fragrance") is True
        assert profile.get("fragrance_sensitive") is True

    @pytest.mark.asyncio
    async def test_ingredient_mention_records_behavioral_reject(self):
        from backend.behavioral_learning import get_behavioral_prefs

        pid = _pid("concern_behavioral")
        ru.set_returning_step(pid, "awaiting_concern_details")
        await _collect(
            ru.run(pid, "the niacinamide serum broke me out", {"step": "awaiting_concern_details"})
        )
        prefs = get_behavioral_prefs(pid)
        # "reject" weight is negative — niacinamide should now be avoided
        assert prefs.get("ingredients", {}).get("niacinamide", 0) < 0

    @pytest.mark.asyncio
    async def test_no_recognizable_tokens_does_not_crash(self):
        pid = _pid("concern_no_tokens")
        ru.set_returning_step(pid, "awaiting_concern_details")
        tokens = await _collect(
            ru.run(pid, "it just didn't work for me", {"step": "awaiting_concern_details"})
        )
        assert ru.get_returning_step(pid) == ""
        assert tokens   # still responds


# =============================================================================
# 6. Dispatcher fallback
# =============================================================================

class TestDispatcherFallback:

    @pytest.mark.asyncio
    async def test_unknown_step_falls_back_to_intake(self, intake_stub):
        pid = _pid("dispatch_fallback")
        ru.set_returning_step(pid, "some_stale_step_that_no_longer_exists")
        tokens = await _collect(
            ru.run(pid, "hello", {"step": "some_stale_step_that_no_longer_exists"})
        )
        assert ru.get_returning_step(pid) == ""
        assert tokens == ["intake reply"]
        assert len(intake_stub.calls) == 1

    @pytest.mark.asyncio
    async def test_step_read_from_session_when_not_in_router_args(self):
        """run() should fall back to get_returning_step() when router_args
        doesn't carry a 'step' key (e.g. called directly without app.py's
        explicit dispatch)."""
        pid = _pid("dispatch_session_fallback")
        ru.set_returning_step(pid, "awaiting_choice")
        tokens = await _collect(ru.run(pid, "Same as before", {}))
        assert ru.get_returning_step(pid) == "awaiting_same_choice"
        assert _ui_data(tokens) is not None


# =============================================================================
# 7. Regression tests
# =============================================================================

class TestRegressions:

    def test_is_product_intent_clears_returning_step_in_app(self):
        """Sending a product-intent message when returning_step is set must
        clear the step so subsequent non-intent replies (e.g. intake answers
        like "oily") don't fall back into the returning-user flow mid-intake.
        This mirrors the fix in app.py:
          if active_returning_step and not pb_returning.is_product_intent(message):
              ...
          else:
              if active_returning_step:
                  pb_returning.clear_returning_step(profile_id)
        """
        pid = _pid("escape_clears_step")
        ru.set_returning_step(pid, "awaiting_change_factors_selection")
        assert ru.get_returning_step(pid) == "awaiting_change_factors_selection"
        # Simulate the app.py escape path
        if ru.is_product_intent("recommend a sunscreen for oily skin"):
            ru.clear_returning_step(pid)
        assert ru.get_returning_step(pid) == "", (
            "returning_step must be cleared when is_product_intent() escapes the flow"
        )

    def test_no_allergies_phrase_sets_allergen_free(self):
        """'no allergies' (common natural-language answer) must match the
        'none' allergen keyword group so allergen_free is saved as ['none'],
        which is truthy — allowing intake to hand off to recommend.run().
        Without this fix allergen_free remained [] (falsy) and the intake
        looped asking the same question forever."""
        from backend.playbooks.intake_profile import keyword_extract
        result = keyword_extract("no allergies")
        assert "allergen_free" in result, (
            "keyword_extract must recognise 'no allergies' as setting allergen_free"
        )
        assert "none" in result["allergen_free"], (
            "allergen_free should contain 'none' for 'no allergies'"
        )

    def test_no_allergy_singular_sets_allergen_free(self):
        from backend.playbooks.intake_profile import keyword_extract
        result = keyword_extract("I have no allergy")
        assert "allergen_free" in result
        assert "none" in result["allergen_free"]

    def test_not_allergic_sets_allergen_free(self):
        from backend.playbooks.intake_profile import keyword_extract
        result = keyword_extract("I'm not allergic to anything")
        assert "allergen_free" in result
        assert "none" in result["allergen_free"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
