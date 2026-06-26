"""
tests/test_retrieval_regression.py

Retrieval regression suite.

Focuses on the class of bug where a product category other than the one
explicitly requested leaks into results.  The canonical failure was:

    query: "suggest me some lip balms under 300"
    expected: only lip_care products
    actual:   sunscreen products

The test surface is layered:

  Layer 1 — Routing / Extraction (no DB)
      keyword_extract() correctly maps message → category
      Router correctly routes category-bearing messages to intake_profile

  Layer 2 — Retrieval hard-filter (live DB required)
      retrieve() with category=X returns ONLY category-X products
      Budget tier is respected as a hard price ceiling
      Relaxation ladder (season → texture → allergen) never loosens category

  Layer 3 — Budget expansion (live DB)
      When requested tier has 0 results, expansion tries the next tier
      Expansion NEVER introduces products from a different category

  Layer 4 — Metamorphic invariant (live DB)
      Adding qualifiers (skin type, budget, allergen) to a category query
      must not change the category of returned products

  Layer 5 — Property / exhaustive (live DB)
      All 6 user-facing categories × all skin types × all budget tiers
      must preserve the category invariant

  Layer 6 — End-to-end integration (live DB)
      Simulates a full session: message → keyword_extract → profile → retrieve
      Catches routing regressions that bypass intake extraction

Each failure prints a structured diagnostic table so CI shows exactly what
went wrong without requiring a debugger.
"""

import sys
import itertools
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.retrieval import retrieve, PRICE_TIER_TO_MAX
from backend.router import _fast_classify
from backend.playbooks.intake_profile import keyword_extract

# ---------------------------------------------------------------------------
# Live-graph fixture — skip all graph tests if FalkorDB unavailable
# ---------------------------------------------------------------------------

try:
    from falkordb import FalkorDB as _FDB
    _db = _FDB(host="localhost", port=6379)
    _g  = _db.select_graph("dotandkey")
    _g.query("RETURN 1")          # connectivity check
    GRAPH_AVAILABLE = True
except Exception:
    GRAPH_AVAILABLE = False

skip_no_graph = pytest.mark.skipif(
    not GRAPH_AVAILABLE, reason="FalkorDB not reachable"
)

# ---------------------------------------------------------------------------
# Shared constants used by parametrize (must be module-level so the decorator
# can evaluate them at collection time before class bodies are executed)
# ---------------------------------------------------------------------------

_CATEGORIES      = ["lip_care", "sunscreen", "moisturizer", "face_wash", "serum", "eye_care"]
_SKIN_TYPES      = ["oily", "dry", "combination", "sensitive", "normal"]
_BUDGET_TIERS    = [("under_300", 300.0), ("under_600", 600.0), ("under_1000", 1000.0)]
_BASE_SKIN_PROFILES = [
    {"skin_types": ["oily"],        "concerns": []},
    {"skin_types": ["dry"],         "concerns": []},
    {"skin_types": ["combination"], "concerns": []},
    {"skin_types": ["sensitive"],   "concerns": []},
    {"skin_types": ["oily"],        "concerns": ["acne"]},
    {"skin_types": ["dry"],         "concerns": ["dryness"]},
]


@pytest.fixture(scope="module")
def graph():
    if not GRAPH_AVAILABLE:
        pytest.skip("FalkorDB not reachable")
    return _g


# ---------------------------------------------------------------------------
# Helper: verify every product in a list belongs to the expected category
# Returns list of (sku, actual_category) tuples for products that DON'T match.
# Queries the graph directly — not category_raw, which is a display field.
# ---------------------------------------------------------------------------

def _wrong_category_products(graph, products: list[dict], expected_cat: str) -> list[tuple]:
    if not products:
        return []
    skus = [p["sku"] for p in products]
    r = graph.query(
        "MATCH (p:Product)-[:IN_CATEGORY]->(c:Category) "
        "WHERE p.sku IN $skus "
        "RETURN p.sku, c.name, p.title, p.price",
        {"skus": skus},
    )
    wrong = []
    for row in r.result_set:
        sku, actual_cat, title, price = row[0], row[1], row[2], row[3]
        if actual_cat != expected_cat:
            wrong.append((sku, actual_cat, title, float(price or 0)))
    return wrong


def _assert_category(graph, result, expected_cat: str, context: str = ""):
    """Assert all products in result are in expected_cat.  Print diagnostic on fail."""
    all_products = result.top_picks + result.remaining
    wrong = _wrong_category_products(graph, all_products, expected_cat)
    if wrong:
        lines = [
            f"\nCATEGORY LEAKAGE DETECTED  {context}",
            f"  Expected category : {expected_cat}",
            f"  Dropped filters   : {result.dropped_filters}",
            f"  Expanded tier     : {result.expanded_budget_tier!r}",
            "",
            f"  {'SKU':<16} {'Returned cat':<16} {'Title':<45} {'₹':>6}",
            f"  {'─'*16} {'─'*16} {'─'*45} {'─'*6}",
        ]
        for sku, actual_cat, title, price in wrong:
            lines.append(f"  {sku:<16} {actual_cat:<16} {title[:45]:<45} {price:>6.0f}")
        pytest.fail("\n".join(lines))


def _assert_prices(result, max_price: float, context: str = ""):
    """Assert every returned product has price <= max_price."""
    over_budget = [
        p for p in result.top_picks + result.remaining
        if p.get("price") is not None and p["price"] > max_price
    ]
    if over_budget:
        lines = [
            f"\nBUDGET VIOLATION  {context}",
            f"  Max allowed : ₹{max_price:.0f}",
            "",
            f"  {'SKU':<16} {'Title':<45} {'₹':>6}",
            f"  {'─'*16} {'─'*45} {'─'*6}",
        ]
        for p in over_budget:
            lines.append(
                f"  {p['sku']:<16} {p['title'][:45]:<45} {p['price']:>6.0f}"
            )
        pytest.fail("\n".join(lines))


# ============================================================================
# LAYER 1 — Routing / Extraction  (no DB needed)
# ============================================================================

class TestCategoryExtraction:
    """keyword_extract() must map category-bearing messages to the correct
    canonical category regardless of surrounding recommend verbs or budget
    qualifiers."""

    CASES = [
        ("lip balm",                             "lip_care"),
        ("lip balms",                            "lip_care"),
        ("lip care",                             "lip_care"),
        ("lip gloss",                            "lip_care"),
        ("lip mask",                             "lip_care"),
        ("suggest me some lip balms under 300",  "lip_care"),
        ("show me lip care options",             "lip_care"),
        ("I need a lip balm for dry lips",       "lip_care"),
        ("best lip balm under 600",              "lip_care"),
        ("sunscreen",                            "sunscreen"),
        ("recommend a sunscreen",                "sunscreen"),
        ("sunscreen for oily skin",              "sunscreen"),
        ("SPF 50 sunscreen",                     "sunscreen"),
        ("moisturizer",                          "moisturizer"),
        ("moisturizer for dry skin",             "moisturizer"),
        ("face wash",                            "face_wash"),
        ("face wash for oily skin",              "face_wash"),
        ("cleanser",                             "face_wash"),
        ("serum",                                "serum"),
        ("serum for dark spots",                 "serum"),
        ("eye cream",                            "eye_care"),
        ("under eye cream",                      "eye_care"),
    ]

    @pytest.mark.parametrize("message,expected_cat", CASES)
    def test_extracts_correct_category(self, message, expected_cat):
        extracted = keyword_extract(message)
        got = extracted.get("category")
        assert got == expected_cat, (
            f"\nCATEGORY EXTRACTION FAILURE"
            f"\n  Message  : {message!r}"
            f"\n  Expected : {expected_cat}"
            f"\n  Got      : {got!r}"
            f"\n  Full extracted: {extracted}"
        )


class TestCategoryRoutingPriority:
    """When a message contains a product-category keyword, the router must
    route to intake_profile — even if a recommend trigger is also present
    and even if the profile is 'ready' with a different stale category.
    Category is the highest-priority constraint in the system."""

    # Stale sunscreen profile — simulates a user who previously searched sunscreens
    STALE = {
        "category":     "sunscreen",
        "skin_types":   ["dry"],
        "allergen_free": ["none"],
        "price_tier":   "any",
    }
    EMPTY = {}

    MUST_INTAKE = [
        # Category-switch messages — MUST route to intake regardless of STALE profile
        "suggest me some lip balms under 300",
        "show me lip care options",
        "recommend a lip balm for dry lips",
        "find me a moisturizer",
        "suggest moisturizers for dry skin",
        "I need a face wash",
        "recommend a serum for dark spots",
        "show me eye creams",
        "best sunscreen for oily skin",   # same category but must still update
    ]

    SHOULD_RECOMMEND = [
        # No category word + ready profile → recommend is correct
        "show me what works",
        "recommend something for me",
        "what are my options",
        "find me something",
    ]

    @pytest.mark.parametrize("message", MUST_INTAKE)
    def test_category_word_always_routes_to_intake(self, message):
        """Category keyword takes priority over any recommend trigger."""
        route = _fast_classify(message, self.STALE)
        assert route == "intake_profile", (
            f"\nROUTING BUG — category word ignored"
            f"\n  Message        : {message!r}"
            f"\n  Stale profile  : category={self.STALE['category']}"
            f"\n  Expected route : intake_profile"
            f"\n  Got route      : {route!r}"
            f"\n  ⚠  This allows stale category to persist into retrieval."
        )

    @pytest.mark.parametrize("message", SHOULD_RECOMMEND)
    def test_no_category_word_uses_ready_profile(self, message):
        """Pure recommend verbs with no new category → recommend is correct."""
        route = _fast_classify(message, self.STALE)
        assert route == "recommend", (
            f"Expected 'recommend' for {message!r} with ready profile, got {route!r}"
        )

    def test_original_regression_lip_balm_stale_sunscreen(self):
        """Regression: 'suggest me some lip balms under 300' with stale
        sunscreen profile must route to intake_profile so the category
        is updated before retrieval, not to recommend which uses stale category."""
        route = _fast_classify(
            "suggest me some lip balms under 300", self.STALE
        )
        assert route == "intake_profile", (
            "\nORIGINAL BUG REGRESSION"
            "\n  Query        : 'suggest me some lip balms under 300'"
            "\n  Stale profile: category=sunscreen"
            "\n  Expected     : intake_profile (to update category → lip_care)"
            f"\n  Got          : {route!r}"
            "\n  ⚠  Without this fix, retrieval uses category=sunscreen → sunscreens returned."
        )


# ============================================================================
# LAYER 2 — Retrieval hard-filter  (live DB)
# ============================================================================

@skip_no_graph
class TestCategoryHardFilter:
    """retrieve() must ONLY return products that belong to the explicitly
    requested category.  This invariant must hold across all relaxation
    rounds, all budget tiers, and all skin-type combinations."""

    BASE_SKIN_PROFILES = [
        {"skin_types": ["oily"],        "concerns": []},
        {"skin_types": ["dry"],         "concerns": []},
        {"skin_types": ["combination"], "concerns": []},
        {"skin_types": ["sensitive"],   "concerns": []},
        {"skin_types": ["oily"],        "concerns": ["acne"]},
        {"skin_types": ["dry"],         "concerns": ["dryness"]},
    ]

    CATEGORIES = ["lip_care", "sunscreen", "moisturizer", "face_wash", "serum", "eye_care"]

    @pytest.mark.parametrize("category,skin_profile", [
        (cat, sp) for cat in _CATEGORIES for sp in _BASE_SKIN_PROFILES
    ])
    def test_category_filter_is_never_violated(self, graph, category, skin_profile):
        profile = {
            "category":     category,
            "skin_types":   skin_profile["skin_types"],
            "concerns":     skin_profile["concerns"],
            "allergen_free": [],
            "max_price":    None,
        }
        result = retrieve(graph, profile)
        _assert_category(
            graph, result, category,
            f"category={category} skin={skin_profile['skin_types']}"
        )

    def test_lip_care_with_allergen_filter_stays_lip_care(self, graph):
        profile = {
            "category":     "lip_care",
            "skin_types":   ["dry"],
            "allergen_free": ["fragrance"],
            "max_price":    None,
        }
        result = retrieve(graph, profile)
        _assert_category(graph, result, "lip_care",
                         "lip_care + fragrance-free allergen filter")

    def test_sunscreen_with_allergen_filter_stays_sunscreen(self, graph):
        profile = {
            "category":     "sunscreen",
            "skin_types":   ["oily"],
            "allergen_free": ["fragrance", "alcohol"],
            "max_price":    None,
        }
        result = retrieve(graph, profile)
        _assert_category(graph, result, "sunscreen",
                         "sunscreen + fragrance+alcohol-free filter")

    def test_face_wash_never_returns_sunscreen_or_lip_care(self, graph):
        profile = {
            "category":     "face_wash",
            "skin_types":   ["oily"],
            "allergen_free": [],
            "max_price":    None,
        }
        result = retrieve(graph, profile)
        forbidden = {"sunscreen", "lip_care", "moisturizer", "serum"}
        all_skus = [p["sku"] for p in result.top_picks + result.remaining]
        if not all_skus:
            pytest.skip("no face_wash products in graph")
        r = graph.query(
            "MATCH (p:Product)-[:IN_CATEGORY]->(c:Category) WHERE p.sku IN $skus RETURN c.name",
            {"skus": all_skus}
        )
        leaked = [row[0] for row in r.result_set if row[0] in forbidden]
        assert not leaked, (
            f"face_wash query leaked into forbidden categories: {leaked}"
        )


# ============================================================================
# LAYER 3 — Budget filtering  (live DB)
# ============================================================================

@skip_no_graph
class TestBudgetHardFilter:
    """Every returned product must have price ≤ the selected budget tier."""

    # lip_care: all products are ₹249–₹259 (all fit in under_300)
    # sunscreen: cheapest is ₹445 (none fit under_300, many fit under_600)
    # face_wash: some products are ₹249 (fit under_300)

    def test_lip_care_under_300_all_products_within_budget(self, graph):
        profile = {
            "category": "lip_care", "skin_types": ["dry"],
            "allergen_free": [], "price_tier": "under_300", "max_price": 300.0,
        }
        result = retrieve(graph, profile)
        assert result.total > 0, "Expected lip_care products under ₹300 (all lip_care ≤ ₹259)"
        _assert_prices(result, 300.0, "lip_care under_300")
        _assert_category(graph, result, "lip_care", "lip_care under_300")

    def test_face_wash_under_300_within_budget(self, graph):
        profile = {
            "category": "face_wash", "skin_types": ["oily"],
            "allergen_free": [], "price_tier": "under_300", "max_price": 300.0,
        }
        result = retrieve(graph, profile)
        if result.total == 0:
            pytest.skip("No face_wash products under ₹300 available")
        _assert_prices(result, 300.0, "face_wash under_300")
        _assert_category(graph, result, "face_wash", "face_wash under_300")

    def test_sunscreen_under_600_within_budget(self, graph):
        profile = {
            "category": "sunscreen", "skin_types": ["oily"],
            "allergen_free": [], "price_tier": "under_600", "max_price": 600.0,
        }
        result = retrieve(graph, profile)
        assert result.total > 0, "Expected sunscreen products under ₹600"
        _assert_prices(result, 600.0, "sunscreen under_600")
        _assert_category(graph, result, "sunscreen", "sunscreen under_600")

    def test_moisturizer_under_1000_within_budget(self, graph):
        profile = {
            "category": "moisturizer", "skin_types": ["dry"],
            "allergen_free": [], "price_tier": "under_1000", "max_price": 1000.0,
        }
        result = retrieve(graph, profile)
        assert result.total > 0, "Expected moisturizer products under ₹1000"
        _assert_prices(result, 1000.0, "moisturizer under_1000")
        _assert_category(graph, result, "moisturizer", "moisturizer under_1000")

    @pytest.mark.parametrize("tier,max_price", [
        ("under_300", 300.0),
        ("under_600", 600.0),
        ("under_1000", 1000.0),
    ])
    def test_budget_respected_across_all_tiers(self, graph, tier, max_price):
        """When no expansion fires, ALL returned prices must be ≤ requested max.
        When expansion fires, prices must be ≤ the EXPANDED tier's limit —
        products above the requested tier are the correct expansion behaviour."""
        for cat in ["lip_care", "sunscreen", "moisturizer", "face_wash", "serum"]:
            profile = {
                "category": cat, "skin_types": ["oily"],
                "allergen_free": [], "price_tier": tier, "max_price": max_price,
            }
            result = retrieve(graph, profile)
            if result.total == 0:
                continue
            if result.expanded_budget_tier:
                # Expansion fired — assert against the expanded tier's ceiling
                expanded_max = PRICE_TIER_TO_MAX.get(result.expanded_budget_tier)
                if expanded_max is not None:
                    _assert_prices(result, expanded_max,
                                   f"{cat} {tier} (expanded → {result.expanded_budget_tier})")
            else:
                # No expansion — requested tier must be honoured exactly
                _assert_prices(result, max_price, f"{cat} {tier}")


# ============================================================================
# LAYER 4 — Budget expansion  (live DB)
# ============================================================================

@skip_no_graph
class TestBudgetExpansionCategoryPreservation:
    """When retrieval expands the budget tier, it MUST NOT introduce products
    from a different category.  This is the most dangerous form of leakage
    because expansion is automatic and silent."""

    def test_sunscreen_under_300_expands_to_600_stays_sunscreen(self, graph):
        """Sunscreens start at ₹445 — no sunscreen fits under ₹300.
        Retrieval must expand to under_600 and return ONLY sunscreens."""
        profile = {
            "category": "sunscreen", "skin_types": ["oily"],
            "allergen_free": [], "price_tier": "under_300", "max_price": 300.0,
        }
        result = retrieve(graph, profile)
        assert result.total > 0, "Expected sunscreen expansion to under_600"
        assert result.expanded_budget_tier != "", (
            "Expected expanded_budget_tier to be set when under_300 → expanded"
        )
        _assert_category(graph, result, "sunscreen",
                         "sunscreen: budget expanded from under_300")
        # After expansion, prices should be within the expanded tier
        expanded_max = PRICE_TIER_TO_MAX.get(result.expanded_budget_tier)
        if expanded_max:
            _assert_prices(result, expanded_max,
                           f"sunscreen: expanded to {result.expanded_budget_tier}")

    def test_moisturizer_under_300_expands_but_stays_moisturizer(self, graph):
        """Moisturisers start at ₹345 — none under ₹300.
        Expansion must keep category=moisturizer."""
        profile = {
            "category": "moisturizer", "skin_types": ["dry"],
            "allergen_free": [], "price_tier": "under_300", "max_price": 300.0,
        }
        result = retrieve(graph, profile)
        if result.total > 0:
            _assert_category(graph, result, "moisturizer",
                             "moisturizer: budget expanded from under_300")

    def test_serum_under_300_expansion_never_returns_sunscreen(self, graph):
        """Serums start at ₹399 — none under ₹300.  After expansion,
        absolutely no sunscreen or lip_care products must appear."""
        profile = {
            "category": "serum", "skin_types": ["oily"],
            "allergen_free": [], "price_tier": "under_300", "max_price": 300.0,
        }
        result = retrieve(graph, profile)
        if result.total == 0:
            pytest.skip("no serum products available even after expansion")
        _assert_category(graph, result, "serum",
                         "serum: budget expanded — must never return sunscreen or lip_care")

    def test_expansion_never_crosses_category_boundary(self, graph):
        """Comprehensive: for every category, expand from under_300.
        Whatever tier expansion lands on, category must be preserved."""
        for cat in ["lip_care", "sunscreen", "moisturizer", "face_wash", "serum"]:
            profile = {
                "category": cat, "skin_types": ["oily"],
                "allergen_free": [], "price_tier": "under_300", "max_price": 300.0,
            }
            result = retrieve(graph, profile)
            if result.total == 0:
                continue
            _assert_category(
                graph, result, cat,
                f"{cat}: after budget expansion from under_300 to {result.expanded_budget_tier!r}"
            )


# ============================================================================
# LAYER 5 — Relaxation ladder  (live DB)
# ============================================================================

@skip_no_graph
class TestRelaxationLadderCategoryPreservation:
    """The fallback relaxation ladder (season → texture → allergen) must never
    change the category.  Only price, season, texture, and allergen can be
    dropped.  Category is immutable."""

    def test_allergen_relaxation_preserves_lip_care_category(self, graph):
        """If no fragrance-free lip balms exist for sensitive skin in monsoon,
        relaxing allergen must still return lip_care products only."""
        profile = {
            "category":     "lip_care",
            "skin_types":   ["sensitive"],
            "allergen_free": ["fragrance"],
            "season":       "monsoon",
            "texture":      "lightweight",
            "max_price":    None,
        }
        result = retrieve(graph, profile)
        if result.total == 0:
            pytest.skip("no lip_care products found at all")
        _assert_category(graph, result, "lip_care",
                         "lip_care with allergen+season+texture filter")

    def test_allergen_relaxation_preserves_sunscreen_category(self, graph):
        profile = {
            "category":     "sunscreen",
            "skin_types":   ["oily"],
            "allergen_free": ["fragrance", "alcohol"],
            "season":       "summer",
            "max_price":    None,
        }
        result = retrieve(graph, profile)
        if result.total == 0:
            pytest.skip("no sunscreen products found at all")
        _assert_category(graph, result, "sunscreen",
                         "sunscreen with allergen relaxation")

    @pytest.mark.parametrize("cat", [
        "lip_care", "sunscreen", "moisturizer", "face_wash", "serum"
    ])
    def test_no_round_ever_introduces_wrong_category(self, graph, cat):
        """Simulate worst-case relaxation by using tight filters that will
        force round 2-4.  Category must survive every round."""
        profile = {
            "category":     cat,
            "skin_types":   ["oily"],
            "allergen_free": ["fragrance", "sulfate"],
            "season":       "summer",
            "texture":      "lightweight",
            "max_price":    None,
        }
        result = retrieve(graph, profile)
        if result.total == 0:
            pytest.skip(f"no {cat} products found even after full relaxation")
        _assert_category(graph, result, cat,
                         f"{cat} with tight filter to force relaxation")


# ============================================================================
# LAYER 6 — Metamorphic invariant  (live DB)
# ============================================================================

@skip_no_graph
class TestMetamorphicCategoryInvariant:
    """Adding qualifiers to a category query must never change the category
    of returned products.

    Formal invariant:
        category(retrieve(base_profile))
        == category(retrieve(base_profile + skin_type))
        == category(retrieve(base_profile + skin_type + budget))
        == category(retrieve(base_profile + skin_type + budget + allergen))
    """

    QUALIFIER_LADDERS = [
        {
            "desc": "lip_care qualifiers",
            "base":    {"category": "lip_care"},
            "plus1":   {"category": "lip_care", "skin_types": ["dry"]},
            "plus2":   {"category": "lip_care", "skin_types": ["dry"], "price_tier": "under_600", "max_price": 600.0},
            "plus3":   {"category": "lip_care", "skin_types": ["dry"], "price_tier": "under_600", "max_price": 600.0, "allergen_free": ["fragrance"]},
        },
        {
            "desc": "sunscreen qualifiers",
            "base":    {"category": "sunscreen"},
            "plus1":   {"category": "sunscreen", "skin_types": ["oily"]},
            "plus2":   {"category": "sunscreen", "skin_types": ["oily"], "price_tier": "under_600", "max_price": 600.0},
            "plus3":   {"category": "sunscreen", "skin_types": ["oily"], "price_tier": "under_600", "max_price": 600.0, "allergen_free": ["fragrance"]},
        },
        {
            "desc": "face_wash qualifiers",
            "base":    {"category": "face_wash"},
            "plus1":   {"category": "face_wash", "skin_types": ["oily"]},
            "plus2":   {"category": "face_wash", "skin_types": ["oily"], "price_tier": "under_600", "max_price": 600.0},
            "plus3":   {"category": "face_wash", "skin_types": ["oily"], "price_tier": "under_600", "max_price": 600.0, "allergen_free": ["sulfate"]},
        },
        {
            "desc": "moisturizer qualifiers",
            "base":    {"category": "moisturizer"},
            "plus1":   {"category": "moisturizer", "skin_types": ["dry"]},
            "plus2":   {"category": "moisturizer", "skin_types": ["dry"], "price_tier": "under_1000", "max_price": 1000.0},
            "plus3":   {"category": "moisturizer", "skin_types": ["dry"], "price_tier": "under_1000", "max_price": 1000.0, "allergen_free": ["fragrance"]},
        },
    ]

    @pytest.mark.parametrize("ladder", QUALIFIER_LADDERS, ids=[str(l["desc"]) for l in QUALIFIER_LADDERS])
    def test_qualifiers_never_change_category(self, graph, ladder):
        expected_cat = ladder["base"]["category"]
        for step_name in ("base", "plus1", "plus2", "plus3"):
            profile = ladder[step_name]
            result = retrieve(graph, profile)
            if result.total == 0:
                continue  # legitimate empty result — not a category violation
            _assert_category(
                graph, result, expected_cat,
                f"{ladder['desc']} [{step_name}] budget_expanded={result.expanded_budget_tier!r}"
            )


# ============================================================================
# LAYER 7 — Property / exhaustive  (live DB)
# ============================================================================

@skip_no_graph
class TestPropertyCategoryInvariant:
    """Exhaustive category × skin_type × budget_tier combinations.
    Category must be preserved in every combination.

    This generates 6 categories × 5 skin_types × 3 budget_tiers = 90 profiles.
    Each profile is cheap (single retrieve() call) and fails fast with a
    clear diagnostic when a category boundary is crossed."""

    CATEGORIES  = ["lip_care", "sunscreen", "moisturizer", "face_wash", "serum", "eye_care"]
    SKIN_TYPES  = ["oily", "dry", "combination", "sensitive", "normal"]
    BUDGET_TIERS = [
        ("under_300",  300.0),
        ("under_600",  600.0),
        ("under_1000", 1000.0),
    ]

    @pytest.mark.parametrize(
        "category,skin_type,tier_name,max_price",
        [
            (cat, skin, tier, mp)
            for cat, skin, (tier, mp) in itertools.product(CATEGORIES, SKIN_TYPES, BUDGET_TIERS)
        ]
    )
    def test_category_invariant(self, graph, category, skin_type, tier_name, max_price):
        profile = {
            "category":     category,
            "skin_types":   [skin_type],
            "allergen_free": [],
            "price_tier":   tier_name,
            "max_price":    max_price,
        }
        result = retrieve(graph, profile)
        if result.total == 0:
            # Empty is allowed — it just means no products at this intersection.
            # The test is about category purity, not about empty results.
            return
        _assert_category(
            graph, result, category,
            f"cat={category} skin={skin_type} tier={tier_name}"
        )

    @pytest.mark.parametrize("category,allergen", [
        ("lip_care",    "fragrance"),
        ("sunscreen",   "fragrance"),
        ("moisturizer", "fragrance"),
        ("face_wash",   "sulfate"),
        ("serum",       "fragrance"),
    ])
    def test_allergen_filter_preserves_category(self, graph, category, allergen):
        profile = {
            "category":     category,
            "skin_types":   ["oily"],
            "allergen_free": [allergen],
            "max_price":    None,
        }
        result = retrieve(graph, profile)
        if result.total == 0:
            return
        _assert_category(
            graph, result, category,
            f"cat={category} allergen_free=[{allergen!r}]"
        )


# ============================================================================
# LAYER 8 — End-to-end routing → extraction → retrieval  (live DB)
# ============================================================================

@skip_no_graph
class TestEndToEndCategoryRouting:
    """Simulate the full session flow for common query patterns.

    Flow:
      message
        → _fast_classify() → playbook name
        → keyword_extract() → profile fields
        → save to profile dict (simulated)
        → retrieve(graph, profile)
        → assert category invariant

    This layer catches bugs where routing bypasses extraction so that a
    stale or incorrect category reaches retrieval.
    """

    STALE_SUNSCREEN = {
        "category":     "sunscreen",
        "skin_types":   ["dry"],
        "allergen_free": ["none"],
        "price_tier":   "any",
    }

    @pytest.mark.parametrize("message,expected_cat", [
        ("suggest me some lip balms under 300",  "lip_care"),
        ("show me lip care options",             "lip_care"),
        ("I need a lip balm for dry lips",       "lip_care"),
        ("best lip balm under 600",              "lip_care"),
        ("suggest a moisturizer for dry skin",   "moisturizer"),
        ("recommend a face wash for oily skin",  "face_wash"),
        ("show me a serum for dark spots",       "serum"),
    ])
    def test_message_routes_and_extracts_correct_category(
        self, graph, message, expected_cat
    ):
        """Route the message, extract fields, simulate profile update, retrieve."""
        route = _fast_classify(message, self.STALE_SUNSCREEN)
        assert route == "intake_profile", (
            f"\nROUTING BYPASSED EXTRACTION"
            f"\n  Message  : {message!r}"
            f"\n  Expected : intake_profile (to update category → {expected_cat})"
            f"\n  Got      : {route!r}"
        )
        extracted = keyword_extract(message)
        assert extracted.get("category") == expected_cat, (
            f"\nEXTRACTION MISSED CATEGORY"
            f"\n  Message          : {message!r}"
            f"\n  Expected cat     : {expected_cat}"
            f"\n  Extracted fields : {extracted}"
        )
        # Simulate profile update: stale profile + extracted fields
        profile = {**self.STALE_SUNSCREEN, **extracted}
        # Resolve max_price from price_tier
        pt = profile.get("price_tier") or ""
        profile["max_price"] = PRICE_TIER_TO_MAX.get(pt if isinstance(pt, str) else "")

        result = retrieve(graph, profile)
        if result.total == 0:
            return  # Empty is OK — price may be too tight
        _assert_category(
            graph, result, expected_cat,
            f"end-to-end: {message!r}"
        )

    def test_original_regression_full_pipeline(self, graph):
        """The exact bug that triggered this test suite:
           'suggest me some lip balms under 300' with stale sunscreen profile
           must return ONLY lip_care products."""
        message = "suggest me some lip balms under 300"
        _fast_classify(message, self.STALE_SUNSCREEN)   # routing verified elsewhere
        extracted = keyword_extract(message)
        profile = {**self.STALE_SUNSCREEN, **extracted}
        pt = profile.get("price_tier") or ""
        profile["max_price"] = PRICE_TIER_TO_MAX.get(pt if isinstance(pt, str) else "")

        result = retrieve(graph, profile)
        # Even if budget expansion fires, category must be lip_care
        if result.total > 0:
            _assert_category(graph, result, "lip_care",
                             "ORIGINAL REGRESSION: suggest lip balms under 300")


# ============================================================================
# LAYER 9 — Synonym / alias handling  (no DB)
# ============================================================================

class TestCategoryAliases:
    """Every common synonym for each category must extract to the canonical
    category value.  New aliases added to intake_profile._CATEGORY_KEYWORDS
    must also be reflected here."""

    ALIAS_MAP = {
        "lip_care": [
            "lip balm", "lip balms", "lip care", "lip mask", "lip gloss",
            "lip color", "lipbalm",
        ],
        "sunscreen": [
            "sunscreen", "SPF", "sunblock",
            # "sun protection" deliberately excluded — it is a feature descriptor,
            # not a product category name, and caused false-positives on
            # "lip balm with sun protection".
        ],
        "face_wash": [
            "face wash", "cleanser", "foaming wash", "gel wash",
        ],
        "moisturizer": [
            "moisturizer", "moisturiser", "hydrating cream", "face cream",
        ],
        "serum": [
            "serum", "essence",
        ],
        "eye_care": [
            "eye cream", "eye care", "under eye",
        ],
    }

    @pytest.mark.parametrize("expected_cat,alias", [
        (cat, alias)
        for cat, aliases in ALIAS_MAP.items()
        for alias in aliases
    ])
    def test_alias_extracts_correct_category(self, expected_cat, alias):
        extracted = keyword_extract(f"I need a {alias}")
        got = extracted.get("category")
        assert got == expected_cat, (
            f"\nALIAS EXTRACTION FAILURE"
            f"\n  Alias    : {alias!r}"
            f"\n  Expected : {expected_cat}"
            f"\n  Got      : {got!r}"
        )


# ============================================================================
# LAYER 10 — Anti-confusion tests (no DB)
# ============================================================================

class TestCategoryConfusion:
    """Ensure there is no cross-category confusion between products that have
    similar names (lip balm SPF 50 might confuse 'sunscreen' detection, etc.)"""

    def test_lip_balm_spf_is_still_lip_care(self):
        """DK's lip balms all have 'SPF 50+' in the title.  The SPF keyword
        must not reclassify them as sunscreens."""
        msgs = [
            "lip balm SPF 50",
            "SPF lip balm",
            "lip care SPF",
            "lip balm with sun protection",
        ]
        for msg in msgs:
            extracted = keyword_extract(msg)
            got = extracted.get("category")
            # "lip balm" appears before "spf" in the keyword scan so lip_care wins
            assert got == "lip_care", (
                f"\nLIP BALM SPF CONFUSION"
                f"\n  Message  : {msg!r}"
                f"\n  Expected : lip_care (not sunscreen)"
                f"\n  Got      : {got!r}"
            )

    def test_serum_does_not_match_sunscreen(self):
        extracted = keyword_extract("brightening serum for dark spots")
        assert extracted.get("category") == "serum", (
            "serum query must not route to sunscreen"
        )

    def test_face_wash_does_not_match_moisturizer(self):
        extracted = keyword_extract("gentle face wash for dry skin")
        assert extracted.get("category") == "face_wash", (
            "face wash query must not route to moisturizer"
        )

    def test_moisturizer_does_not_match_sunscreen(self):
        extracted = keyword_extract("moisturizer for oily skin")
        assert extracted.get("category") == "moisturizer"


# ============================================================================
# LAYER 12 — Session memory leak / multi-turn category switch  (live DB)
# ============================================================================

class TestSessionMemoryLeak:
    """Simulates multi-turn conversations where the user switches product
    category between messages.

    The profile is updated turn-by-turn using keyword_extract() + merge,
    exactly as intake_profile.run() does.  The invariant: the LATEST
    explicit category in the conversation must always govern retrieval.
    Previous categories must not leak into results.
    """

    STALE_SUNSCREEN = {
        "category":     "sunscreen",
        "skin_types":   ["oily"],
        "allergen_free": ["none"],
        "price_tier":   "any",
    }
    STALE_SERUM = {
        "category":     "serum",
        "skin_types":   ["dry"],
        "allergen_free": ["fragrance"],
        "price_tier":   "any",
    }

    def _update(self, profile: dict, message: str) -> dict:
        """Simulate one intake_profile turn: extract → merge into profile."""
        extracted = keyword_extract(message)
        return {**profile, **{k: v for k, v in extracted.items() if v}}

    def test_sunscreen_then_lip_balm_returns_lip_care(self, graph):
        profile = self._update(self.STALE_SUNSCREEN, "show me lip balms")
        assert profile.get("category") == "lip_care", (
            f"Category was not updated: still {profile.get('category')!r}"
        )
        result = retrieve(graph, profile)
        _assert_category(graph, result, "lip_care",
                         "turn1=sunscreen turn2=lip_balm → must return lip_care")

    def test_serum_then_face_wash_returns_face_wash(self, graph):
        profile = self._update(self.STALE_SERUM, "show me face wash")
        assert profile.get("category") == "face_wash"
        result = retrieve(graph, profile)
        _assert_category(graph, result, "face_wash",
                         "turn1=serum turn2=face_wash → must return face_wash")

    def test_sunscreen_then_lip_balm_original_regression(self, graph):
        """Regression: the exact bug report scenario."""
        profile = self._update(self.STALE_SUNSCREEN,
                               "suggest me some lip balms under 300")
        result = retrieve(graph, profile)
        if result.total > 0:
            _assert_category(graph, result, "lip_care",
                             "REGRESSION: suggest lip balms after sunscreen session")


@skip_no_graph
class TestMultiTurnCategorySwitchStress:
    """6-step chain: sunscreen→lip_care→moisturizer→serum→face_wash→sunscreen.
    After each switch, retrieve() must return ONLY the current category.
    No previous category may influence the result."""

    CHAIN = [
        ("recommend a sunscreen for oily skin",         "sunscreen"),
        ("actually show me lip balms instead",           "lip_care"),
        ("now I want a moisturizer for dry skin",        "moisturizer"),
        ("switch to serum for dark spots",               "serum"),
        ("actually a face wash for my oily skin",        "face_wash"),
        ("ok back to sunscreens please",                 "sunscreen"),
    ]

    def test_category_chain_each_step_correct(self, graph):
        profile: dict = {}
        for msg, expected_cat in self.CHAIN:
            extracted = keyword_extract(msg)
            profile = {**profile, **{k: v for k, v in extracted.items() if v}}

            assert profile.get("category") == expected_cat, (
                f"\nMULTI-TURN CHAIN EXTRACTION FAILURE"
                f"\n  Message         : {msg!r}"
                f"\n  Expected cat    : {expected_cat}"
                f"\n  Profile cat     : {profile.get('category')!r}"
                f"\n  Full profile    : {profile}"
            )

            result = retrieve(graph, profile)
            if result.total == 0:
                continue  # legitimate empty result — category may have no products

            _assert_category(
                graph, result, expected_cat,
                f"multi-turn chain step: {msg!r}"
            )


# ============================================================================
# LAYER 13 — Typo robustness  (no DB)
# ============================================================================

class TestTypoRobustness:
    """Common user misspellings must map to the correct category.
    Each entry is (misspelling, expected_canonical_category)."""

    TYPO_CASES = [
        # lip care typos
        ("lipbalm",     "lip_care"),
        ("lip balms",   "lip_care"),
        ("lipbalms",    "lip_care"),
        ("lip gloss",   "lip_care"),
        ("lipcare",     "lip_care"),
        ("lip care",    "lip_care"),
        # sunscreen typos
        ("sunscrean",   "sunscreen"),
        ("sunscreen",   "sunscreen"),
        # moisturizer — British vs American spelling
        ("moisturiser", "moisturizer"),
        ("moisturizer", "moisturizer"),
    ]

    @pytest.mark.parametrize("typo,expected", TYPO_CASES)
    def test_typo_extracts_correct_category(self, typo, expected):
        got = keyword_extract(f"I need a {typo}").get("category")
        assert got == expected, (
            f"\nTYPO EXTRACTION FAILURE"
            f"\n  Input    : {typo!r}"
            f"\n  Expected : {expected}"
            f"\n  Got      : {got!r}"
        )

    def test_lipbalm_nospace_routes_to_intake_not_llm(self):
        """'lipbalm' (no space) must trigger intake_profile routing, not
        fall through to the LLM router, since it maps to a known category."""
        stale = {"category": "sunscreen", "skin_types": ["oily"]}
        route = _fast_classify("show me lipbalm options", stale)
        assert route == "intake_profile", (
            f"'lipbalm' should trigger intake_profile, got {route!r}"
        )


# ============================================================================
# LAYER 14 — Negation  (no DB + live DB)
# ============================================================================

class TestNegation:
    """Negative category expressions must not extract the negated category.

    Implementation: keyword_extract's category scan skips any keyword that
    is preceded within 20 chars by a negation word (not, no, don't, without).
    """

    def test_not_sunscreen_extracts_no_category(self):
        """'I do not want sunscreen' — the only product word is negated,
        so no category should be extracted (None)."""
        got = keyword_extract("I do not want sunscreen").get("category")
        assert got is None, (
            f"Negated category 'sunscreen' must not be extracted, got {got!r}"
        )

    def test_lip_balm_not_sunscreen_extracts_lip_care(self):
        """'I want lip balm, not sunscreen' — lip_care is positive,
        sunscreen is negated → lip_care must win."""
        got = keyword_extract("I want lip balm not sunscreen").get("category")
        assert got == "lip_care", (
            f"Expected 'lip_care' (sunscreen negated), got {got!r}"
        )

    def test_no_face_wash_extracts_no_category(self):
        got = keyword_extract("I don't want face wash").get("category")
        assert got is None, f"Negated 'face wash' must not be extracted, got {got!r}"

    def test_positive_plus_negated_second_category(self):
        """'serum not moisturizer' — serum is positive, moisturizer is negated."""
        got = keyword_extract("I want a serum not moisturizer").get("category")
        assert got == "serum", f"Expected 'serum', got {got!r}"

    def test_negated_category_does_not_reach_retrieval(self):
        """When negation strips the category, profile has no category → retrieval
        falls back to the existing profile category (sunscreen in this stale case)
        rather than returning nothing or the wrong category."""
        # Simulate: stale profile has sunscreen, user says "not sunscreen" →
        # keyword_extract returns no category → profile category unchanged
        stale_profile = {
            "category": "sunscreen", "skin_types": ["oily"],
            "allergen_free": [], "price_tier": "any",
        }
        extracted = keyword_extract("I do not want sunscreen")
        assert extracted.get("category") is None, (
            "Negated category must not be extracted"
        )
        # Profile unchanged — category stays 'sunscreen' since no override
        merged = {**stale_profile, **{k: v for k, v in extracted.items() if v}}
        assert merged.get("category") == "sunscreen", (
            "Without a positive category in the message, profile category is unchanged"
        )


# ============================================================================
# LAYER 15 — OOS variant integrity  (live DB + Shopify)
# ============================================================================

@skip_no_graph
class TestOOSVariantIntegrity:
    """Out-of-stock (available=False) variants must never be recommended
    as the primary/selected variant.

    The Shopify .js endpoint is the source of truth for availability.
    This test works at the graph level: confirms that for each tinted product
    family, the graph's variant identity matches what is known to be available.

    The widget-level swatch filtering (available=False → no swatch) is tested
    in test_widget.js::TestBrandRedesign.

    Known OOS variants as of the last catalog audit:
      - DK_LBWMR (Watermelon Rush High Tinted) — available=False
    """

    def test_gloss_boss_watermelon_rush_not_solo_recommended(self, graph):
        """DK_LBWMR (Watermelon Rush) is OOS.  It may be in the graph but when
        any of the Gloss Boss variants is recommended, dedupe collapses all 5
        to one card keyed by URL.  The specific SKU shown may be DK_LBWMR, but
        the card's swatch selection will be driven by the .js live availability
        check in the widget.  Here we verify the backend at least returns
        SOME lip_care product for this profile."""
        profile = {
            "category":     "lip_care",
            "skin_types":   ["dry"],
            "allergen_free": [],
            "price_tier":   "any",
            "max_price":    None,
        }
        result = retrieve(graph, profile)
        assert result.total > 0, "Expected at least one lip_care product"
        _assert_category(graph, result, "lip_care",
                         "lip_care retrieval — OOS variant must not leak category")

    def test_oos_sku_does_not_appear_as_separate_top_pick(self, graph):
        """After deduplication, no two top_picks or remaining items should share
        the same product URL (each URL = one card).  This ensures DK_LBWMR
        and the other Gloss Boss variants are not shown as five separate cards."""
        profile = {
            "category":     "lip_care",
            "skin_types":   ["dry"],
            "allergen_free": [],
            "price_tier":   "any",
            "max_price":    None,
        }
        result = retrieve(graph, profile)
        urls = [p["url"] for p in result.top_picks + result.remaining if p.get("url")]
        unique_urls = set(urls)
        assert len(urls) == len(unique_urls), (
            f"Duplicate URL in results — dedup failed.\n"
            f"  All URLs: {urls}\n"
            f"  Duplicates: {[u for u in urls if urls.count(u) > 1]}"
        )


# ============================================================================
# LAYER 16 — Price sorting after budget expansion  (live DB)
# ============================================================================

@skip_no_graph
class TestPriceSortingAfterExpansion:
    """When budget expansion occurs, the result set must still be sorted by
    relevance (match_score DESC) — not simply cheapest-first.

    The sort order `match_score DESC, price ASC` must be preserved regardless
    of which budget tier was ultimately used."""

    def test_sunscreen_under_300_expansion_sorted_by_relevance(self, graph):
        """Sunscreens start at ₹445 — expansion from under_300 fires.
        Top pick must have the highest match_score, not the lowest price."""
        profile = {
            "category":     "sunscreen",
            "skin_types":   ["oily"],
            "concerns":     ["acne", "excess_oil"],
            "allergen_free": [],
            "price_tier":   "under_300",
            "max_price":    300.0,
        }
        result = retrieve(graph, profile)
        if result.total < 2:
            pytest.skip("Not enough sunscreen results to compare sort order")
        assert result.expanded_budget_tier, "Expected budget expansion to fire"

        all_p = result.top_picks + result.remaining
        # match_score should be non-increasing across the list
        # (descending sort was applied before dedup)
        for i in range(len(all_p) - 1):
            s_curr = all_p[i]["match_score"]
            s_next = all_p[i + 1]["match_score"]
            assert s_curr >= s_next, (
                f"\nSORT ORDER VIOLATED AFTER BUDGET EXPANSION"
                f"\n  [{i}] {all_p[i]['title'][:40]} score={s_curr}"
                f"\n  [{i+1}] {all_p[i+1]['title'][:40]} score={s_next}"
                f"\n  Expected match_score to be non-increasing"
            )

    def test_no_expansion_sort_order_preserved(self, graph):
        """When expansion does NOT fire, results are still match_score ordered."""
        profile = {
            "category":     "lip_care",
            "skin_types":   ["dry"],
            "concerns":     ["dryness"],
            "allergen_free": [],
            "price_tier":   "under_600",
            "max_price":    600.0,
        }
        result = retrieve(graph, profile)
        if result.total < 2:
            pytest.skip("Not enough lip_care results to verify sort order")
        all_p = result.top_picks + result.remaining
        for i in range(len(all_p) - 1):
            s_curr = all_p[i]["match_score"]
            s_next = all_p[i + 1]["match_score"]
            assert s_curr >= s_next, (
                f"Sort order violated: [{i}] score={s_curr} > [{i+1}] score={s_next}"
            )


# ============================================================================
# LAYER 17 — Catalog evolution  (live DB, auto-generated)
# ============================================================================

@skip_no_graph
class TestCatalogEvolution:
    """Data integrity tests generated dynamically from the live graph.
    These tests automatically adapt as products are added or removed.

    Each test is a property that should hold for every product in the graph:
    - Belongs to exactly one category
    - Has a positive price
    - Has a non-empty URL (post media-enrichment)
    - URL follows the expected Shopify format
    - Category is one of the known user-facing categories
    """

    # Categories that can be surface to users in recommendations
    VALID_CATEGORIES = {
        "sunscreen", "moisturizer", "face_wash", "serum",
        "lip_care", "eye_care", "toner", "mask",
        "body_care", "hair_care", "combo",
    }

    def _all_products(self, graph) -> list[dict]:
        r = graph.query(
            "MATCH (p:Product)-[:IN_CATEGORY]->(c:Category) "
            "WHERE p.url IS NOT NULL AND p.url <> '' "
            "RETURN p.sku, p.title, p.price, p.url, c.name "
            "ORDER BY c.name, p.sku"
        )
        return [
            {"sku": row[0], "title": row[1], "price": row[2],
             "url": row[3], "category": row[4]}
            for row in r.result_set
        ]

    def test_all_products_have_positive_price(self, graph):
        products = self._all_products(graph)
        bad = [p for p in products if not p["price"] or p["price"] <= 0]
        if bad:
            lines = [f"\nPRODUCTS WITH INVALID PRICE:"]
            for p in bad:
                lines.append(f"  {p['sku']:<16} {p['category']:<14} ₹{p['price']} — {p['title'][:40]}")
            pytest.fail("\n".join(lines))

    def test_all_products_have_shopify_url(self, graph):
        products = self._all_products(graph)
        bad = [p for p in products
               if not p["url"] or "/products/" not in p["url"]]
        if bad:
            lines = [f"\nPRODUCTS WITHOUT A VALID SHOPIFY URL ({len(bad)}):"]
            for p in bad[:20]:
                lines.append(f"  {p['sku']:<16} {p['category']:<14} {p['url']!r}")
            pytest.fail("\n".join(lines))

    def test_all_products_in_valid_categories(self, graph):
        r = graph.query(
            "MATCH (p:Product)-[:IN_CATEGORY]->(c:Category) "
            "RETURN DISTINCT c.name"
        )
        actual = {row[0] for row in r.result_set}
        unknown = actual - self.VALID_CATEGORIES
        assert not unknown, (
            f"Unknown categories found in graph: {unknown}\n"
            f"Update VALID_CATEGORIES if these are intentional."
        )

    def test_retrieve_returns_products_for_all_user_categories(self, graph):
        """Every user-facing category must return at least one product when
        queried with a minimal profile.  If a category returns zero products,
        it either has no data or the retrieval query has a regression."""
        user_cats = ["sunscreen", "moisturizer", "face_wash", "serum",
                     "lip_care", "eye_care"]
        empty_cats = []
        for cat in user_cats:
            result = retrieve(graph, {"category": cat, "skin_types": ["oily"],
                                      "allergen_free": [], "max_price": None})
            if result.total == 0:
                empty_cats.append(cat)
        assert not empty_cats, (
            f"These categories returned zero products with a minimal profile: "
            f"{empty_cats}"
        )

    def test_no_product_appears_in_two_different_main_categories(self, graph):
        """A product should belong to exactly one category. If a SKU appears
        under both 'sunscreen' and 'lip_care', that is a graph ingest error."""
        r = graph.query(
            "MATCH (p:Product)-[:IN_CATEGORY]->(c:Category) "
            "WITH p.sku AS sku, collect(DISTINCT c.name) AS cats "
            "WHERE size(cats) > 1 "
            "RETURN sku, cats LIMIT 20"
        )
        if r.result_set:
            lines = ["\nPRODUCTS IN MULTIPLE CATEGORIES (ingest error):"]
            for row in r.result_set:
                lines.append(f"  {row[0]}: {row[1]}")
            pytest.fail("\n".join(lines))

    def test_category_counts_match_expected_minimums(self, graph):
        """Sanity check: each category must have at least a minimum number of
        products.  If a count drops below the threshold, a bulk-delete or
        ingest failure probably occurred."""
        # Thresholds derived from the current catalog — update if products are
        # intentionally removed.
        MIN_COUNTS = {
            "sunscreen":   15,
            "lip_care":    10,
            "moisturizer":  8,
            "face_wash":    8,
            "serum":        8,
        }
        r = graph.query(
            "MATCH (p:Product)-[:IN_CATEGORY]->(c:Category) "
            "RETURN c.name, count(p) AS n ORDER BY c.name"
        )
        actual = {row[0]: row[1] for row in r.result_set}
        violations = []
        for cat, min_n in MIN_COUNTS.items():
            n = actual.get(cat, 0)
            if n < min_n:
                violations.append(f"  {cat}: expected ≥{min_n}, got {n}")
        assert not violations, (
            "CATALOG BELOW MINIMUM THRESHOLDS\n" + "\n".join(violations)
        )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
