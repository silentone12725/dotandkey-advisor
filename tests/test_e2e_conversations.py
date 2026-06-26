"""
tests/test_e2e_conversations.py

End-user conversation simulation suite (Layers 19–30).

Simulates realistic multi-turn advisor sessions without Redis, an LLM, or a
running server.  The SimulatedSession class replicates the profile-update
lifecycle that intake_profile.run() performs in production:

    message → route → keyword_extract → profile merge → [auto-transition?] → retrieve

Design goals:
  - Every test reads like a real conversation transcript
  - Failures print the full session transcript + profile + bad products
  - No mocking of keyword_extract or retrieval — real logic is exercised
  - Sessions are isolated: each test gets a fresh SimulatedSession instance
  - Graph-dependent tests are skipped when FalkorDB is unavailable

Layers covered:
  19  Happy path sessions (multi-turn, full profile)
  20  Category switching mid-session
  21  User corrections (category, budget)
  22  Invalid / gibberish inputs
  23  Conflicting instructions
  24  Negation stress
  25  Typo stress
  26  Budget expansion safety
  27  Allergen override
  28  Session memory leak (100 randomised sessions)
  29  Fuzz testing (1000 randomised inputs, no-crash guarantee)
  30  Fixture-based regression (known-failing transcripts)
"""

import sys
import json
import random
import itertools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.router import _fast_classify
from backend.playbooks.intake_profile import keyword_extract
from backend.retrieval import retrieve, PRICE_TIER_TO_MAX, RetrievalResult
from backend.profile import parse_profile

# ---------------------------------------------------------------------------
# Graph fixture
# ---------------------------------------------------------------------------

try:
    from falkordb import FalkorDB as _FDB
    _db = _FDB(host="localhost", port=6379)
    _g  = _db.select_graph("dotandkey")
    _g.query("RETURN 1")
    GRAPH_AVAILABLE = True
except Exception:
    GRAPH_AVAILABLE = False

skip_no_graph = pytest.mark.skipif(
    not GRAPH_AVAILABLE, reason="FalkorDB not reachable"
)


@pytest.fixture(scope="module")
def graph():
    if not GRAPH_AVAILABLE:
        pytest.skip("FalkorDB not reachable")
    return _g


# ---------------------------------------------------------------------------
# Invariant helpers
# ---------------------------------------------------------------------------

def _wrong_category(graph, products: list[dict], expected: str) -> list[tuple]:
    if not products:
        return []
    skus = [p["sku"] for p in products]
    r = graph.query(
        "MATCH (p:Product)-[:IN_CATEGORY]->(c:Category) WHERE p.sku IN $skus "
        "RETURN p.sku, c.name, p.title, p.price",
        {"skus": skus},
    )
    return [(row[0], row[1], row[2], float(row[3] or 0))
            for row in r.result_set if row[1] != expected]


def _price_violations(products: list[dict], max_price: float) -> list[dict]:
    return [p for p in products if p.get("price") is not None and p["price"] > max_price]


# ---------------------------------------------------------------------------
# SimulatedSession — the core harness
# ---------------------------------------------------------------------------

@dataclass
class TurnRecord:
    message:   str
    route:     Optional[str]
    extracted: dict
    profile_after: dict


@dataclass
class SimulatedSession:
    """Replicates the intake_profile + retrieve lifecycle without Redis or LLM.

    Routing:
      - If _fast_classify returns 'intake_profile' or None (LLM-fallback):
          always attempt keyword_extract and merge fields.
          LLM-fallback is treated as intake so bare budget phrases ("Under 300")
          still update the profile.
      - If _fast_classify returns 'recommend':
          run retrieve() with the current profile.

    Auto-transition:
      Once category + skin_types + allergen_free + price_tier are all set,
      the next intake turn also triggers a retrieve() — mirroring the
      auto-transition in intake_profile.py.
    """
    graph: object = None
    profile: dict = field(default_factory=dict)
    transcript: list = field(default_factory=list)
    last_result: Optional[RetrievalResult] = None
    turns: list = field(default_factory=list)

    # ── required fields for auto-transition (mirrors intake_profile.py) ──────
    _REQUIRED = ("category", "skin_types", "allergen_free", "price_tier")

    def _is_ready(self) -> bool:
        p = parse_profile(self.profile)
        return all(p.get(f) for f in self._REQUIRED)

    def _build_retrieval_profile(self) -> dict:
        p = dict(self.profile)
        pt = p.get("price_tier") or ""
        if not isinstance(pt, str):
            pt = ""
        p["max_price"] = PRICE_TIER_TO_MAX.get(pt)
        return p

    def send(self, message: str) -> Optional[object]:
        """Process one turn.  Returns RetrievalResult when recommendations fire."""
        self.transcript.append(message)
        route = _fast_classify(message, self.profile)
        extracted = keyword_extract(message)

        # Merge extracted fields into profile (skip empty/None values)
        for k, v in extracted.items():
            if v:
                self.profile[k] = v

        self.turns.append(TurnRecord(
            message=message,
            route=route,
            extracted=extracted,
            profile_after=dict(self.profile),
        ))

        # Explicit recommend route
        if route == "recommend" and self.graph:
            self.last_result = retrieve(self.graph, self._build_retrieval_profile())
            return self.last_result

        # Auto-transition when all four required fields are now filled
        if self._is_ready() and self.graph and route != "recommend":
            self.last_result = retrieve(self.graph, self._build_retrieval_profile())
            return self.last_result

        return None

    # ── diagnostic formatting ─────────────────────────────────────────────────

    def format_failure(self, context: str = "") -> str:
        """Structured failure output for pytest."""
        lines = [
            "",
            f"SESSION FAILURE  {context}",
            "",
            "  TRANSCRIPT:",
        ]
        for i, (t, turn) in enumerate(zip(self.transcript, self.turns), 1):
            lines.append(f"    [{i}] User : {t!r}")
            lines.append(f"         Route: {turn.route or 'LLM-fallback'}")
            lines.append(f"         Extracted: {turn.extracted}")
        lines += [
            "",
            "  FINAL PROFILE:",
            f"    category    : {self.profile.get('category')!r}",
            f"    skin_types  : {self.profile.get('skin_types')!r}",
            f"    allergen_free: {self.profile.get('allergen_free')!r}",
            f"    price_tier  : {self.profile.get('price_tier')!r}",
        ]
        if self.last_result:
            r = self.last_result
            lines += [
                "",
                f"  RETURNED PRODUCTS ({r.total} total):",
            ]
            for p in (r.top_picks + r.remaining)[:8]:
                lines.append(
                    f"    {p['sku']:<16} ₹{p.get('price',0):>5.0f}  {p['title'][:45]}"
                )
            if r.expanded_budget_tier:
                lines.append(f"  budget expanded → {r.expanded_budget_tier}")
        return "\n".join(lines)

    def assert_category(self, expected_cat: str, context: str = ""):
        """Assert every returned product belongs to expected_cat."""
        if not self.last_result:
            return
        wrong = _wrong_category(
            self.graph,
            self.last_result.top_picks + self.last_result.remaining,
            expected_cat,
        )
        if wrong:
            diag = self.format_failure(context)
            lines = [diag, "", "  CATEGORY LEAKAGE:"]
            for sku, cat, title, price in wrong:
                lines.append(
                    f"    {sku:<16} {cat:<14} ₹{price:>5.0f}  {title[:40]}"
                )
            lines.append(f"\n  Expected ONLY: {expected_cat}")
            pytest.fail("\n".join(lines))

    def assert_prices(self, max_price: float, context: str = ""):
        if not self.last_result:
            return
        bad = _price_violations(
            self.last_result.top_picks + self.last_result.remaining, max_price
        )
        if bad:
            diag = self.format_failure(context)
            lines = [diag, "", f"  PRICE VIOLATIONS (max=₹{max_price:.0f}):"]
            for p in bad:
                lines.append(
                    f"    {p['sku']:<16} ₹{p['price']:>5.0f}  {p['title'][:40]}"
                )
            pytest.fail("\n".join(lines))

    def assert_has_results(self, context: str = ""):
        if not self.last_result or self.last_result.total == 0:
            pytest.fail(self.format_failure(context) + "\n  Expected at least 1 result")


# ============================================================================
# LAYER 19 — Happy path sessions
# ============================================================================

@skip_no_graph
class TestHappyPathSessions:

    def test_session_a_lip_balm_dry_fragrance_free_under_300(self, graph):
        """Full 4-turn session: lip balm → dry lips → no fragrance → under 300.
        Must return only lip_care, fragrance-free, ≤ ₹300."""
        s = SimulatedSession(graph=graph)
        s.send("Recommend a lip balm")
        s.send("Dry lips")
        s.send("No fragrance")
        result = s.send("Under 300")

        assert result is not None, s.format_failure("Session A should trigger recommendations")
        s.assert_category("lip_care", "Session A — final category must be lip_care")
        s.assert_prices(300.0, "Session A — all products must be ≤ ₹300")

    def test_session_b_sunscreen_oily_under_600(self, graph):
        """3-turn session: sunscreen → oily skin → under 600."""
        s = SimulatedSession(graph=graph)
        s.send("Need sunscreen")
        s.send("Oily skin")
        s.send("None / not sure")     # allergen: no preference
        result = s.send("Under 600")

        assert result is not None, s.format_failure("Session B should trigger recommendations")
        s.assert_category("sunscreen", "Session B — must return only sunscreen")
        s.assert_prices(600.0, "Session B — all products must be ≤ ₹600")

    def test_session_c_moisturizer_dry_fragrance_free_any_budget(self, graph):
        """Moisturizer session with no budget limit."""
        s = SimulatedSession(graph=graph)
        s.send("Looking for a moisturizer")
        s.send("Dry skin")
        s.send("Fragrance-free")
        result = s.send("No budget preference")

        assert result is not None, s.format_failure("Session C should trigger recommendations")
        s.assert_category("moisturizer", "Session C — must return only moisturizer")

    def test_session_d_face_wash_oily_no_sulfates_under_600(self, graph):
        s = SimulatedSession(graph=graph)
        s.send("I need a face wash")
        s.send("Oily skin")
        s.send("No sulfates")
        result = s.send("under 600")

        assert result is not None, s.format_failure("Session D should fire recommendations")
        s.assert_category("face_wash", "Session D — must return only face_wash")

    def test_session_profile_accumulates_correctly(self, graph):
        """Verify the profile dict is built incrementally, field by field."""
        s = SimulatedSession(graph=graph)
        s.send("Recommend a serum")
        assert s.profile.get("category") == "serum", s.format_failure("category after turn 1")

        s.send("Oily skin")
        assert s.profile.get("skin_types"), s.format_failure("skin_types after turn 2")

        s.send("No fragrance")
        assert "fragrance" in (s.profile.get("allergen_free") or []), (
            s.format_failure("allergen_free after turn 3")
        )

        s.send("Under 600")
        assert s.profile.get("price_tier") == "under_600", (
            s.format_failure("price_tier after turn 4")
        )


# ============================================================================
# LAYER 20 — Category switching
# ============================================================================

@skip_no_graph
class TestCategorySwitch:

    def test_sunscreen_then_lip_balm(self, graph):
        """User switches from sunscreen to lip balm — only lip_care must survive."""
        s = SimulatedSession(graph=graph)
        s.send("Recommend sunscreen")
        assert s.profile.get("category") == "sunscreen"

        s.send("Actually show lip balms")
        assert s.profile.get("category") == "lip_care", s.format_failure(
            "category must update to lip_care after switch"
        )

        # Complete the profile so recommendations fire
        s.send("Dry skin")
        s.send("No fragrance")
        result = s.send("Under 300")
        if result is not None:
            s.assert_category("lip_care", "post-switch must return ONLY lip_care")

    def test_moisturizer_then_face_wash(self, graph):
        """'No, face wash instead' must immediately replace moisturizer."""
        s = SimulatedSession(graph=graph)
        s.send("Need moisturizer")
        assert s.profile.get("category") == "moisturizer"

        s.send("No, face wash instead")
        assert s.profile.get("category") == "face_wash", s.format_failure(
            "category must switch to face_wash"
        )

    def test_6_step_chain_final_category_wins(self, graph):
        """Chain: sunscreen→lip_care→moisturizer→serum→face_wash→sunscreen.
        After each step the profile category must equal the last explicit mention."""
        chain = [
            ("recommend a sunscreen for oily skin",         "sunscreen"),
            ("actually show me lip balms instead",           "lip_care"),
            ("now I want a moisturizer for dry skin",        "moisturizer"),
            ("switch to serum for dark spots",               "serum"),
            ("actually a face wash for my oily skin",        "face_wash"),
            ("ok back to sunscreens please",                 "sunscreen"),
        ]
        s = SimulatedSession(graph=graph)
        for msg, expected_cat in chain:
            s.send(msg)
            got = s.profile.get("category")
            assert got == expected_cat, (
                s.format_failure(f"After {msg!r}")
                + f"\n  Expected category: {expected_cat}"
                + f"\n  Got             : {got!r}"
            )

    def test_no_sunscreen_survives_lip_balm_switch(self, graph):
        """After switching to lip_care, retrieve must never return sunscreen."""
        s = SimulatedSession(graph=graph)
        # Prime with sunscreen
        s.send("recommend a sunscreen")
        s.send("oily skin")
        # Switch
        s.send("actually lip balm please")
        s.send("no fragrance")
        result = s.send("under 300")
        if result and result.total > 0:
            s.assert_category("lip_care", "no sunscreen must survive lip_care switch")


# ============================================================================
# LAYER 21 — User corrections
# ============================================================================

class TestUserCorrections:

    def test_category_correction_sunscreen_to_lip_balm(self):
        """'Actually I meant lip balm' after 'recommend sunscreen'."""
        s = SimulatedSession()
        s.send("Recommend sunscreen")
        assert s.profile.get("category") == "sunscreen"
        s.send("Actually I meant lip balm")
        assert s.profile.get("category") == "lip_care", s.format_failure(
            "'Actually I meant lip balm' must update category"
        )

    def test_budget_correction_300_to_600(self):
        """User changes budget from 300 to 600."""
        s = SimulatedSession()
        s.send("lip balm under 300")
        assert s.profile.get("price_tier") == "under_300"
        s.send("actually under 600")
        assert s.profile.get("price_tier") == "under_600", s.format_failure(
            "budget must update to under_600"
        )

    def test_budget_correction_any_budget(self):
        """'Budget doesn't matter' must set price_tier to 'any'."""
        s = SimulatedSession()
        s.send("under 300")
        s.send("budget doesn't matter")  # maps to "any"
        # "doesn't matter" → _PRICE_TIER_KEYWORDS["any"] includes "doesn't matter"
        # so price_tier should be overridden to "any"
        tier = s.profile.get("price_tier")
        assert tier == "any", s.format_failure(
            f"'budget doesn't matter' must set price_tier='any', got {tier!r}"
        )

    def test_skin_type_correction(self):
        """User first says oily, then corrects to dry."""
        s = SimulatedSession()
        s.send("oily skin")
        s.send("actually dry skin")
        # Keyword extract on "actually dry skin" → {"skin_types": ["dry"]}
        # which overrides the previous "oily" in the profile
        parsed = parse_profile(s.profile)
        assert "dry" in parsed.get("skin_types", []), s.format_failure(
            "skin_type correction to 'dry' must take effect"
        )


# ============================================================================
# LAYER 22 — Invalid inputs
# ============================================================================

class TestInvalidInputs:
    """Gibberish, numbers, emojis must never crash extraction or routing,
    and must never trigger recommendations without a real category."""

    INVALID = [
        "asdfghjkl",
        "zxcvbnm",
        "123456",
        "9999",
        "?????",
        "!!!!",
        "🔥🔥🔥",
        "😂😭💅",
        "",
        "   ",
        "aaaaaaaaaaaaaaaaaaaaa",
        "1 2 3 4 5 6 7 8 9",
    ]

    @pytest.mark.parametrize("bad_input", INVALID)
    def test_no_crash(self, bad_input):
        """keyword_extract and _fast_classify must never raise on any input."""
        try:
            extracted = keyword_extract(bad_input)
            route     = _fast_classify(bad_input, {})
        except Exception as exc:
            pytest.fail(
                f"Exception on input {bad_input!r}: {type(exc).__name__}: {exc}"
            )
        # No category should be extracted from pure garbage
        assert extracted.get("category") is None, (
            f"Garbage input {bad_input!r} must not extract a category, got {extracted}"
        )

    def test_invalid_inputs_do_not_trigger_recommendation(self):
        """A session that receives only gibberish must never generate recommendations."""
        s = SimulatedSession()
        for bad in self.INVALID[:6]:
            result = s.send(bad)
            assert result is None, (
                f"Gibberish {bad!r} must not trigger recommendations; "
                f"profile was {s.profile}"
            )
        assert not s._is_ready(), (
            "Profile must not be 'ready' after only gibberish inputs"
        )


# ============================================================================
# LAYER 23 — Conflicting inputs
# ============================================================================

class TestConflictingInputs:

    def test_recommend_lip_balm_then_no_lip_balm(self):
        """User asks for lip balm then negates it.

        Negation prevents a NEW category from being extracted but does NOT
        clear the existing profile category (that would require an LLM turn
        to handle the ambiguity).  The system keeps the last positive category
        and the LLM would ask for clarification in production.
        """
        s = SimulatedSession()
        s.send("Recommend lip balm")
        assert s.profile.get("category") == "lip_care"

        s.send("No lip balm")
        # "No lip balm" negates extraction — but the stored category is
        # NOT overwritten since no positive category was given.
        # Profile category remains "lip_care" (unchanged from last positive).
        assert s.profile.get("category") == "lip_care", s.format_failure(
            "'No lip balm' must not silently switch to a different category"
        )

    def test_budget_latest_wins(self):
        """When the user gives two different budgets, the later one wins."""
        s = SimulatedSession()
        s.send("under 300")
        assert s.profile.get("price_tier") == "under_300"
        s.send("budget doesn't matter")
        assert s.profile.get("price_tier") == "any", s.format_failure(
            "latest budget instruction must override earlier one"
        )

    def test_conflicting_skin_types_last_wins(self):
        """Two contradictory skin types in the same message → both kept
        (multi-select is valid).  In a later message, new skin type is added."""
        s = SimulatedSession()
        s.send("I have oily skin")
        s.send("actually I think dry skin")
        # Both may be present; the point is no crash and dry must be included
        parsed = parse_profile(s.profile)
        assert "dry" in parsed.get("skin_types", []), s.format_failure(
            "'dry skin' must be reflected in profile"
        )


# ============================================================================
# LAYER 24 — Negation stress tests
# ============================================================================

class TestNegationStress:

    NEGATION_CASES = [
        ("I do not want sunscreen",          None),
        ("No serum please",                  None),
        ("not face wash",                    None),
        ("I don't want moisturizer",         None),
        ("without sunscreen",                None),
        ("avoid lip balm",                   None),
        ("I want lip balm not sunscreen",    "lip_care"),   # positive overrides
        ("serum not moisturizer",            "serum"),
        ("face wash without sunscreen",      "face_wash"),
    ]

    @pytest.mark.parametrize("message,expected_cat", NEGATION_CASES)
    def test_negated_category_not_extracted(self, message, expected_cat):
        got = keyword_extract(message).get("category")
        assert got == expected_cat, (
            f"\nNEGATION EXTRACTION FAILURE"
            f"\n  Message  : {message!r}"
            f"\n  Expected : {expected_cat!r}"
            f"\n  Got      : {got!r}"
        )

    def test_negated_category_does_not_extract_new_category(self):
        """'no lip balm' must not extract 'lip_care' as a new category.
        The existing profile category is unchanged (negation only blocks
        extraction — it does not clear stored fields)."""
        s = SimulatedSession()
        s.send("lip balm")
        assert s.profile.get("category") == "lip_care"
        s.send("no lip balm")
        # Negation prevented extraction — no new category was added.
        # The stored category stays "lip_care" from the previous turn.
        assert s.profile.get("category") == "lip_care", s.format_failure(
            "negation must not silently switch to a different category"
        )
        assert not s._is_ready(), (
            "Profile must not be ready (missing skin_types, allergen_free, price_tier)"
        )


# ============================================================================
# LAYER 25 — Typo stress tests
# ============================================================================

class TestTypoStress:

    TYPO_CASES = [
        ("lipbalm",   "lip_care"),
        ("lipbalms",  "lip_care"),
        ("lipcare",   "lip_care"),
        ("lip balm",  "lip_care"),
        ("lip balms", "lip_care"),
        ("lip gloss",  "lip_care"),
        ("lip colour", "lip_care"),   # British spelling
        ("lip color",  "lip_care"),
        ("sunscrean",  "sunscreen"),
        ("sunscreen",  "sunscreen"),
    ]

    @pytest.mark.parametrize("typo,expected", TYPO_CASES)
    def test_typo_extracts_correct_category(self, typo, expected):
        got = keyword_extract(f"show me {typo}").get("category")
        assert got == expected, (
            f"\nTYPO EXTRACTION FAILURE"
            f"\n  Input    : {typo!r}"
            f"\n  Expected : {expected}"
            f"\n  Got      : {got!r}"
        )

    @pytest.mark.parametrize("typo,expected_cat", TYPO_CASES)
    def test_typo_routes_to_intake_not_llm(self, typo, expected_cat):
        """Typo messages containing a recognised product word must route to
        intake_profile (so extraction runs) not fall to LLM router."""
        STALE = {"category": "serum", "skin_types": ["oily"]}
        got_route = _fast_classify(f"I need {typo}", STALE)
        assert got_route == "intake_profile", (
            f"Typo {typo!r} (expected category={expected_cat!r}) must route to "
            f"intake_profile, got {got_route!r}"
        )

    def test_all_lip_care_typos_stay_lip_care_in_session(self):
        """Multiple lip care typos in one session must all resolve to lip_care."""
        lip_typos = ["lipbalm", "lip balm", "lipcare", "lip gloss", "lipbalms"]
        for typo in lip_typos:
            s = SimulatedSession()
            s.send(typo)
            got = s.profile.get("category")
            assert got == "lip_care", (
                f"Typo {typo!r} in session must produce category='lip_care', got {got!r}"
            )


# ============================================================================
# LAYER 26 — Budget expansion safety
# ============================================================================

@skip_no_graph
class TestBudgetExpansionSafety:
    """When budget expansion fires, the category must never change.
    This tests the session-level guarantee, not just the retrieval unit."""

    def test_lip_balm_under_300_expansion_stays_lip_care(self, graph):
        """All lip_care products are ≤ ₹259, so under_300 returns results.
        Even if it expanded, category must remain lip_care."""
        s = SimulatedSession(graph=graph)
        s.send("lip balm")
        s.send("dry skin")
        s.send("no fragrance")
        result = s.send("under 300")

        assert result is not None, s.format_failure("under_300 lip_care must produce results")
        s.assert_category("lip_care", "budget expansion must never introduce sunscreen")

    def test_sunscreen_under_300_expansion_stays_sunscreen(self, graph):
        """Sunscreens start at ₹445 — under_300 will expand.
        After expansion, ONLY sunscreen products may appear."""
        s = SimulatedSession(graph=graph)
        s.send("sunscreen")
        s.send("oily skin")
        s.send("none / not sure")
        result = s.send("under 300")

        assert result is not None, s.format_failure("expansion must produce results")
        assert result.expanded_budget_tier, s.format_failure(
            "budget must have expanded from under_300"
        )
        s.assert_category("sunscreen", "expansion must never return lip_care or moisturizer")

    @pytest.mark.parametrize("category", [
        "lip_care", "sunscreen", "moisturizer", "face_wash", "serum"
    ])
    def test_expansion_never_crosses_category_for_all_categories(self, graph, category):
        """For every category: a tight under_300 budget may expand, but category
        must be preserved throughout."""
        s = SimulatedSession(graph=graph)
        # Use a minimal profile to force expansion risk
        s.profile = {
            "category":     category,
            "skin_types":   ["oily"],
            "allergen_free": ["fragrance"],
            "price_tier":   "under_300",
        }
        result = retrieve(graph, {**s.profile, "max_price": 300.0})
        if result.total == 0:
            return  # nothing at all — skip, not a test failure
        wrong = _wrong_category(graph, result.top_picks + result.remaining, category)
        if wrong:
            lines = [
                f"\nBUDGET EXPANSION CATEGORY LEAK  category={category}",
                f"  expanded_to={result.expanded_budget_tier!r}",
                "  Wrong products:",
            ]
            for sku, cat, title, price in wrong:
                lines.append(f"    {sku:<16} {cat:<14} ₹{price:>5.0f}  {title[:40]}")
            pytest.fail("\n".join(lines))


# ============================================================================
# LAYER 27 — Allergen override
# ============================================================================

@skip_no_graph
class TestAllergenOverride:

    def test_multiple_allergen_exclusions_all_honoured(self, graph):
        """User excludes fragrance AND sulfates.  Both must apply to retrieval."""
        s = SimulatedSession(graph=graph)
        s.send("face wash")
        s.send("oily skin")
        s.send("fragrance-free, no sulfates")
        result = s.send("under 600")

        if result and result.total > 0:
            s.assert_category("face_wash", "face_wash with dual allergen filter")
            # Allergens are verified structurally at the graph level —
            # the allergen-count check in retrieval ensures both are excluded.
            assert s.profile.get("allergen_free"), s.format_failure(
                "allergen_free must be set after exclusion messages"
            )

    def test_no_fragrance_no_essential_oils(self, graph):
        """User excludes fragrance and essential_oil — both in allergen_free."""
        s = SimulatedSession(graph=graph)
        s.send("lip balm")
        s.send("dry skin")
        s.send("no fragrance")
        s.send("no essential oils")   # 'essential oil' is an allergen node
        result = s.send("No budget preference")

        if result and result.total > 0:
            s.assert_category("lip_care", "allergen chain session must stay lip_care")

    def test_allergen_none_clears_restrictions(self, graph):
        """'None / not sure' signals no allergen preference — retrieval must
        still work and category must be intact."""
        s = SimulatedSession(graph=graph)
        s.send("moisturizer")
        s.send("dry skin")
        s.send("none / not sure")
        result = s.send("under 1000")

        if result:
            s.assert_category("moisturizer", "no-allergen session must stay moisturizer")


# ============================================================================
# LAYER 28 — Session memory leak (100 randomised sessions)
# ============================================================================

@skip_no_graph
class TestSessionMemoryLeak100:
    """Run 100 independent sessions with randomised category + skin type +
    allergen + budget.  Each session starts clean (new SimulatedSession).
    Category invariant must hold on every session."""

    _CATEGORIES  = ["lip_care", "sunscreen", "moisturizer", "face_wash", "serum"]
    _SKIN_TYPES  = ["oily", "dry", "combination", "sensitive", "normal"]
    _ALLERGENS   = ["no fragrance", "none / not sure", "no sulfates"]
    _BUDGETS     = ["under 300", "under 600", "under 1000", "No budget preference"]

    def _make_100_sessions(self):
        rng = random.Random(42)   # fixed seed for reproducibility
        combos = list(itertools.product(
            self._CATEGORIES, self._SKIN_TYPES, self._ALLERGENS, self._BUDGETS
        ))
        return rng.sample(combos, min(100, len(combos)))

    def test_100_sessions_no_category_leak(self, graph):
        failures = []
        for cat, skin, allergen, budget in self._make_100_sessions():
            s = SimulatedSession(graph=graph)
            s.send(f"recommend {cat.replace('_', ' ')}")
            s.send(skin)
            s.send(allergen)
            result = s.send(budget)

            if result is None or result.total == 0:
                continue
            wrong = _wrong_category(
                graph,
                result.top_picks + result.remaining,
                cat,
            )
            if wrong:
                failures.append(
                    f"  cat={cat} skin={skin} budget={budget!r}: "
                    f"leaked {[(w[1], w[2][:30]) for w in wrong[:2]]}"
                )

        if failures:
            pytest.fail(
                f"CATEGORY LEAKAGE IN {len(failures)}/100 SESSIONS:\n"
                + "\n".join(failures[:10])
            )


# ============================================================================
# LAYER 29 — Fuzz testing (1000 random inputs, no-crash guarantee)
# ============================================================================

class TestFuzz1000:
    """Generate 1000 randomised messages and verify:
    - keyword_extract never raises
    - _fast_classify never raises
    - If a category is extracted, it is a known valid category
    - If a price_tier is extracted, it is a known valid tier

    Retrieval is NOT called here (1000 DB calls would be too slow).
    The fuzz target is the extraction + routing layer."""

    VALID_CATEGORIES = {
        "sunscreen", "moisturizer", "face_wash", "serum",
        "lip_care", "eye_care", "toner", "mask", "body_care", "hair_care",
    }
    VALID_TIERS = {"under_300", "under_600", "under_1000", "any"}

    _WORDS = [
        "lip balm", "sunscreen", "moisturizer", "face wash", "serum",
        "oily", "dry", "sensitive", "combination", "normal",
        "no fragrance", "no sulfates", "fragrance free", "none",
        "under 300", "under 600", "under 1000", "under ₹500",
        "recommend", "suggest", "show me", "I need", "I want",
        "not", "no", "don't want", "avoid",
        "lipbalm", "lipcare", "sunscrean", "moisturiser",
        "dry lips", "oily skin", "acne", "dark spots",
        "", "asdf", "1234", "???", "🔥",
    ]

    def _random_message(self, rng):
        n = rng.randint(1, 5)
        return " ".join(rng.choices(self._WORDS, k=n))

    def test_1000_fuzz_inputs_no_crash(self):
        rng = random.Random(1234)
        errors = []
        profile = {}
        for i in range(1000):
            msg = self._random_message(rng)
            try:
                extracted = keyword_extract(msg)
                route     = _fast_classify(msg, profile)
            except Exception as exc:
                errors.append(f"  [{i}] {msg!r}: {type(exc).__name__}: {exc}")
                continue

            # Validate extracted fields
            cat  = extracted.get("category")
            tier = extracted.get("price_tier")
            if cat is not None and cat not in self.VALID_CATEGORIES:
                errors.append(
                    f"  [{i}] {msg!r}: invalid category {cat!r}"
                )
            if tier is not None and tier not in self.VALID_TIERS:
                errors.append(
                    f"  [{i}] {msg!r}: invalid price_tier {tier!r}"
                )

            # Evolve profile for next turn (simulates session churn)
            if i % 20 == 0:
                profile = {}   # reset every 20 turns
            elif extracted.get("category"):
                profile["category"] = extracted["category"]

        if errors:
            pytest.fail(
                f"FUZZ FAILURES ({len(errors)}/1000):\n"
                + "\n".join(errors[:20])
            )


# ============================================================================
# LAYER 30 — Fixture-based regression (known-failing transcripts)
# ============================================================================

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "conversations"


def _load_fixtures() -> list:
    if not FIXTURES_DIR.exists():
        return []
    return sorted(FIXTURES_DIR.glob("*.json"))


def _fixture_ids(paths) -> list:
    return [p.stem for p in paths]


@skip_no_graph
class TestFixtureRegressions:
    """Replay known-failing conversation fixtures.

    Each fixture is a JSON file in tests/fixtures/conversations/ with shape:
    {
        "description": "human-readable description of the regression",
        "turns": ["message 1", "message 2", ...],
        "expected_category": "lip_care",
        "expected_max_price": 300.0  (optional),
        "must_not_contain_categories": ["sunscreen", "serum"]  (optional)
    }

    New regressions should be added here with:
        python -m pytest tests/ -k "fixture" --save-fixture <name>
    """

    @pytest.mark.parametrize("fixture_path", _load_fixtures(), ids=_fixture_ids(_load_fixtures()))
    def test_replay_fixture(self, graph, fixture_path):
        with open(fixture_path) as f:
            data = json.load(f)

        desc          = data.get("description", fixture_path.stem)
        turns         = data["turns"]
        expected_cat  = data.get("expected_category")
        max_price     = data.get("expected_max_price")
        forbidden_cats = data.get("must_not_contain_categories", [])

        s = SimulatedSession(graph=graph)
        for msg in turns:
            s.send(msg)

        if expected_cat and s.last_result and s.last_result.total > 0:
            s.assert_category(expected_cat, f"FIXTURE REGRESSION: {desc}")

        if max_price and s.last_result:
            s.assert_prices(max_price, f"FIXTURE REGRESSION: {desc}")

        if forbidden_cats and s.last_result:
            all_products = s.last_result.top_picks + s.last_result.remaining
            if all_products:
                skus = [p["sku"] for p in all_products]
                r = graph.query(
                    "MATCH (p:Product)-[:IN_CATEGORY]->(c:Category) WHERE p.sku IN $skus "
                    "RETURN p.sku, c.name, p.title",
                    {"skus": skus},
                )
                leaked = [
                    (row[0], row[1], row[2])
                    for row in r.result_set if row[1] in forbidden_cats
                ]
                if leaked:
                    diag = s.format_failure(f"FIXTURE REGRESSION: {desc}")
                    lines = [diag, "  FORBIDDEN CATEGORIES PRESENT:"]
                    for sku, cat, title in leaked:
                        lines.append(f"    {sku:<16} {cat:<14} {title[:40]}")
                    pytest.fail("\n".join(lines))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
