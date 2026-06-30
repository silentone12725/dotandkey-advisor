"""
backend/query_intent.py

Three-tier query-intent scoring for product ranking:

  Tier 1 — Exact    : literal ingredient/attribute tokens from the user query
  Tier 2 — Enriched : true synonyms, intent-concept expansion, fuzzy matching
  Tier 3 — LLM      : async, only for vague queries with no extracted tokens
                       (see backend/vague_intent.py)

Ranking formula (weights applied in compute_final_score):
  final_score = query_intent * 80 + skin * 30 + concern * 25 + allergen * 25

Boost multipliers by tier:
  Exact match   : ×1.0  (+100 pts per match location)
  Synonym match : ×0.9  (two names for the same ingredient)
  Intent match  : ×0.8  (user describes a concern → known ingredient)
  Fuzzy match   : ×0.7  (user typed a typo within edit-distance tolerance)
  Description   : ×0.4  (all tiers, match in description text)

Score ordering: exact > synonym > intent > fuzzy > generic
"""

import re
import logging
from dataclasses import dataclass

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal token type
# ---------------------------------------------------------------------------

@dataclass
class _Token:
    text: str
    factor: float   # ×1.0 / ×0.9 / ×0.8 / ×0.7
    source: str     # "exact" | "synonym" | "intent" | "fuzzy"


# ---------------------------------------------------------------------------
# Multi-word phrase list — greedy extraction, longest first
# Includes both literal ingredient phrases AND intent phrases that the user
# might type (e.g. "no white cast", "dark spots", "glass skin").
# ---------------------------------------------------------------------------

_MULTI_WORD_TOKENS: list[str] = sorted(
    [
        # Actives / ingredients
        "vitamin c", "vitamin b3",
        "salicylic acid", "hyaluronic acid", "glycolic acid",
        "alpha arbutin", "kojic acid", "tranexamic acid", "azelaic acid",
        "lactic acid", "ascorbic acid", "l-ascorbic acid", "mandelic acid",
        "ferulic acid", "benzoyl peroxide",
        # Claims
        "fragrance free", "fragrance-free",
        "without fragrance",
        "oil free", "oil-free",
        "paraben free", "paraben-free",
        "sulfate free", "sulfate-free",
        "alcohol free", "alcohol-free",
        "silicone free", "silicone-free",
        "water resistant",
        "non comedogenic", "non-comedogenic",
        # Intent phrases — user concern / outcome → ingredient
        "no white cast", "white cast", "with coverage", "with tint",
        "skin tint", "skin tone", "skin tone shades",
        "glass skin",
        "dark spots", "dark spot",
        "acne marks", "acne mark",
        "uneven skin tone",
        "skin barrier", "barrier repair", "damaged barrier",
        "oil control", "pore minimizing", "enlarged pores", "pore clearing",
        "acne prone",
        "chapped lips", "dry lips", "peeling lips",
        "sensitive skin",
        # Allergen / fragrance sensitivity
        "allergy safe", "allergy friendly",
        "reactive skin", "irritated skin",
        "react to fragrance", "easily irritated",
        "eczema friendly", "hypo allergenic",
        "no fragrance",
        # Sub-types
        "lip balm", "lip gloss", "lip care", "sheet mask", "face oil", "eye cream",
        # SPF
        "spf 50", "spf 30", "spf 40",
    ],
    key=len,
    reverse=True,
)


# ---------------------------------------------------------------------------
# Skip words — pure filler, intent verbs, category names, skin types.
# Note: concern words (acne, pores, dark, dull) are NOT skipped — they map
# to ingredients via _INTENT_CONCEPTS.
# ---------------------------------------------------------------------------

_SKIP_WORDS: frozenset[str] = frozenset(
    {
        # Articles / prepositions
        "a", "an", "the", "for", "with", "and", "or", "but",
        "in", "of", "to", "on", "at", "is", "it", "do", "no", "not",
        "me", "my", "i", "you", "can", "so",
        # Intent verbs / filler
        "suggest", "recommend", "find", "need", "want", "looking",
        "give", "show", "help", "please", "get", "use", "also",
        "just", "like", "under", "over", "that", "without",
        # Quality modifiers
        "good", "best", "better", "great", "nice", "some", "any",
        "something", "new", "really", "very",
        # Product categories (resolved at retrieval level)
        "serum", "moisturizer", "moisturiser", "sunscreen", "toner",
        "cleanser", "mask", "wash",
        # Skin types (handled via profile skin_score)
        "oily", "dry", "combination", "sensitive", "normal",
        # Generic words that don't signal a specific product attribute
        "skin", "face", "care", "product", "routine", "treatment",
        "free", "prone",
    }
)


# ---------------------------------------------------------------------------
# True synonyms: user writes A → canonical ingredient/attribute is B
# ---------------------------------------------------------------------------

_TRUE_SYNONYMS: dict[str, str] = {
    # Niacinamide
    "vitamin b3":           "niacinamide",
    "b3":                   "niacinamide",
    # Vitamin C
    "l ascorbic acid":      "vitamin c",
    "ascorbic acid":        "vitamin c",
    # Fragrance-free
    "unscented":            "fragrance free",
    "without fragrance":    "fragrance free",
    # Tinted
    "skin tint":            "tinted",
    "with tint":            "tinted",
    "skin tone":            "tinted",
    "tint":                 "tinted",
    # Ceramide
    "barrier repair":       "ceramide",
    "skin barrier":         "ceramide",
    # Hyaluronic acid
    "moisture boost":       "hyaluronic acid",
    # Fragrance-free synonyms
    "hypoallergenic":       "fragrance free",
    "hypo allergenic":      "fragrance free",
    "allergy safe":         "fragrance free",
    "allergy friendly":     "fragrance free",
    "no fragrance":         "fragrance free",
}


# ---------------------------------------------------------------------------
# Intent to Axis: user describes a concern/outcome → target capability axis.
# ---------------------------------------------------------------------------

_INTENT_TO_AXIS: dict[str, str] = {
    # Oil / Pores
    "oil control":          "oil_control",
    "pore minimizing":      "pore_care",
    "enlarged pores":       "pore_care",
    "pore clearing":        "pore_care",
    "pore":                 "pore_care",
    "pores":                "pore_care",
    # Acne
    "acne":                 "acne",
    "acne prone":           "acne",
    "blackhead":            "acne",
    "blackheads":           "acne",
    "breakout":             "acne",
    "blemish":              "acne",
    "blemishes":            "acne",
    # Brightening / Pigmentation
    "glass skin":           "brightening",
    "brightening":          "brightening",
    "glow":                 "brightening",
    "dull":                 "brightening",
    "dullness":             "brightening",
    "dark spots":           "pigmentation",
    "dark spot":            "pigmentation",
    "hyperpigmentation":    "pigmentation",
    "acne marks":           "pigmentation",
    "acne mark":            "pigmentation",
    "uneven skin tone":     "pigmentation",
    # Barrier / Sensitivity
    "barrier":              "barrier_repair",
    "skin barrier":         "barrier_repair",
    "barrier repair":       "barrier_repair",
    "damaged barrier":      "barrier_repair",
    "sensitive skin":       "sensitivity",
    # Hydration
    "hydration":            "hydration",
    "hydrating":            "hydration",
    "moisture":             "hydration",
    "dry lips":             "lip_repair",
    "chapped lips":         "lip_repair",
    "peeling lips":         "lip_repair",
    "chapped":              "lip_repair",
    "peeling":              "lip_repair",
}

# ---------------------------------------------------------------------------
# Intent concepts (Structural/Ingredient intents): user describes an outcome → target ingredient/attribute.
# ---------------------------------------------------------------------------

_INTENT_CONCEPTS: dict[str, str | list[str]] = {
    # Tinted / coverage
    "white cast":           "tinted",
    "no white cast":        "tinted",
    "with coverage":        "tinted",
    "skin tone shades":     "tinted",
    "coverage":             "tinted",
    # Antioxidant
    "antioxidant":          "vitamin c",
    # Allergen / fragrance sensitivity → fragrance free + ceramide
    "eczema":               ["fragrance free", "ceramide"],
    "eczema friendly":      ["fragrance free", "ceramide"],
    "reactive skin":        ["fragrance free", "ceramide"],
    "irritated skin":       ["fragrance free", "ceramide"],
    "easily irritated":     ["fragrance free", "ceramide"],
    "react to fragrance":   "fragrance free",
}

# ---------------------------------------------------------------------------
# Known ingredient/attribute strings for fuzzy matching.
# These are the canonical forms that fuzzy tokens are resolved to.
# ---------------------------------------------------------------------------

_KNOWN_INGREDIENTS: list[str] = [
    "niacinamide", "ceramide", "retinol", "peptide", "caffeine", "squalane",
    "vitamin c", "salicylic acid", "hyaluronic acid", "glycolic acid",
    "alpha arbutin", "kojic acid", "tranexamic acid", "azelaic acid",
    "lactic acid", "ascorbic acid", "benzoyl peroxide",
    "tinted", "matte", "dewy",
    "fragrance free",
    "brightening", "hydrating", "lightweight", "non comedogenic",
]


# ---------------------------------------------------------------------------
# Boost constants (for exact match at factor 1.0)
# ---------------------------------------------------------------------------

BOOST_TITLE            = 100
BOOST_VARIANT          = 100
BOOST_INGREDIENT       = 100
BOOST_FREE_FROM        = 80   # general free-from claims (paraben, sulfate, alcohol)
BOOST_FRAGRANCE_FREE   = 120  # fragrance/allergen-specific free-from claim
BOOST_DESCRIPTION      = 40

_FACTOR_EXACT   = 1.0
_FACTOR_SYNONYM = 0.9
_FACTOR_INTENT  = 0.8
_FACTOR_FUZZY   = 0.7

# Claim keys that use the higher BOOST_FRAGRANCE_FREE
_FRAGRANCE_CLAIM_KEYS: frozenset[str] = frozenset({"fragrance"})

# Penalty applied to confirmed-fragranced products when allergen intent is expressed
PENALTY_FRAGRANCED = 80

# Markers that indicate a product contains fragrance as an ingredient
_FRAGRANCED_MARKERS: frozenset[str] = frozenset({"fragrance", "parfum", "perfume"})


# ---------------------------------------------------------------------------
# Edit distance (for fuzzy matching)
# ---------------------------------------------------------------------------

def _edit_distance(a: str, b: str) -> int:
    """Standard edit distance (insert / delete / substitute)."""
    if abs(len(a) - len(b)) > 3:
        return 99
    m, n = len(a), len(b)
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        curr = [i] + [0] * n
        for j in range(1, n + 1):
            curr[j] = (
                prev[j - 1] if a[i - 1] == b[j - 1]
                else 1 + min(prev[j], curr[j - 1], prev[j - 1])
            )
        prev = curr
    return prev[n]


def _fuzzy_threshold(token: str) -> int:
    return 1 if len(token) <= 5 else 2


def _find_fuzzy_canonical(token: str) -> str | None:
    """Return the known ingredient/attribute that fuzzy-matches `token`, or None.

    Checks word-by-word for multi-word knowns (e.g. "hyalronic" matches
    "hyaluronic" inside "hyaluronic acid").
    """
    threshold = _fuzzy_threshold(token)
    best_dist = threshold + 1
    best_known: str | None = None

    for known in _KNOWN_INGREDIENTS:
        if known == token:
            return None   # exact match — handled separately
        for part in known.split():
            if abs(len(part) - len(token)) <= threshold:
                d = _edit_distance(token, part)
                if d <= threshold and d < best_dist:
                    best_dist = d
                    best_known = known

    return best_known


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _score_token(
    t: str,
    factor: float,
    product: dict,
    title: str,
    variant: str,
    description: str,
    ingredients: list[str],
    free_from: list[str],
) -> int:
    """Score one (possibly enriched) token against pre-normalised product fields and capability axes."""
    pts = 0
    
    # ── 1. Capability Axis Matching ──────────────────────────────────────────
    axis = _INTENT_TO_AXIS.get(t)
    if axis:
        cap_score = product.get(f"cap_{axis}") or 0.0
        cap_conf = product.get(f"cap_{axis}_conf") or 0.8
        
        # Capability base points (max ~100)
        pts += int(cap_score * cap_conf * 10 * factor)
        
        # Identity Boost
        if product.get("dna_primary") == axis:
            pts += int(50 * factor)
        elif product.get("dna_secondary") == axis:
            pts += int(25 * factor)
            
        return pts  # If it mapped to an axis, we use capability points instead of keyword points.

    # ── 2. Keyword String Matching ───────────────────────────────────────────
    if t in title:
        pts += int(BOOST_TITLE * factor)
    if t in variant:
        pts += int(BOOST_VARIANT * factor)
    for ing in ingredients:
        if t in ing or (len(t) >= 4 and ing in t):
            # Check ingredient roles if available (boost primary/supporting over incidental)
            roles = product.get("_ingredient_roles") or {}
            role = roles.get(ing, ("unknown", ""))[0]
            role_mult = 1.0
            if role == "primary": role_mult = 1.5
            elif role == "supporting": role_mult = 1.0
            elif role == "incidental": role_mult = 0.3
            
            pts += int(BOOST_INGREDIENT * factor * role_mult)
            break
            
    claim_key = t.replace(" free", "").strip()
    for ff in free_from:
        if claim_key in ff or ff in claim_key:
            base = BOOST_FRAGRANCE_FREE if claim_key in _FRAGRANCE_CLAIM_KEYS else BOOST_FREE_FROM
            pts += int(base * factor)
            break
            
    if t in description:
        pts += int(BOOST_DESCRIPTION * factor)
        
    return pts


def _enrich_tokens(query_tokens: list[str]) -> list[_Token]:
    """Expand raw query tokens into a ranked _Token list.

    For each token:
      1. Add itself at EXACT factor
      2. Expand true synonyms at SYNONYM factor
      3. Expand intent concepts at INTENT factor
      4. Fuzzy-match against known ingredients at FUZZY factor

    Deduplication: once a canonical text is added, it won't be re-added
    at a lower factor (exact wins over synonym, synonym over intent, etc.).
    """
    enriched: list[_Token] = []
    seen: set[str] = set()

    for token in query_tokens:
        if token not in seen:
            enriched.append(_Token(token, _FACTOR_EXACT, "exact"))
            seen.add(token)

        # True synonym expansion
        canon = _TRUE_SYNONYMS.get(token)
        if canon and canon not in seen:
            enriched.append(_Token(canon, _FACTOR_SYNONYM, "synonym"))
            seen.add(canon)

        # Intent concept expansion
        target = _INTENT_CONCEPTS.get(token)
        if target:
            targets = [target] if isinstance(target, str) else target
            for t in targets:
                if t not in seen:
                    enriched.append(_Token(t, _FACTOR_INTENT, "intent"))
                    seen.add(t)

        # Fuzzy match (only if no synonym/intent already resolved this token)
        if token not in _TRUE_SYNONYMS and token not in _INTENT_CONCEPTS:
            fuzzy = _find_fuzzy_canonical(token)
            if fuzzy and fuzzy not in seen:
                enriched.append(_Token(fuzzy, _FACTOR_FUZZY, "fuzzy"))
                seen.add(fuzzy)

    return enriched


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_query_tokens(user_message: str) -> list[str]:
    """Return a deduplicated list of ingredient/attribute token strings.

    Pass 1 — greedy multi-word extraction (longest phrase first).
    Pass 2 — remaining single words that aren't skip/category/skin-type words.

    Returns plain list[str] for backward compatibility. Synonym/intent/fuzzy
    enrichment happens inside compute_query_intent_score / rerank_by_query_intent.
    """
    if not user_message:
        return []

    text = user_message.lower()
    text = re.sub(r"-", " ", text)       # normalise hyphens
    text = re.sub(r"\s+", " ", text).strip()

    tokens: list[str] = []

    # Pass 1: greedy multi-word
    for phrase in _MULTI_WORD_TOKENS:
        norm = phrase.replace("-", " ")
        if norm in text:
            tokens.append(norm)
            text = text.replace(norm, " " * len(norm), 1)

    # Pass 2: remaining single words
    for raw in text.split():
        word = re.sub(r"[^\w]", "", raw)
        if not word or len(word) < 3 or word in _SKIP_WORDS:
            continue
        tokens.append(word)

    # Deduplicate, preserve order
    seen: set[str] = set()
    result: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result


def compute_query_intent_score(product: dict, query_tokens: list[str]) -> int:
    """Score a product against extracted query tokens (all three enrichment tiers).

    Returns raw query-intent points before the ×80 weight in final_score.
    """
    if not query_tokens:
        return 0

    title       = (product.get("title") or "").lower().replace("-", " ")
    variant     = (product.get("variant") or "").lower().replace("-", " ")
    description = (product.get("description") or "").lower().replace("-", " ")
    ingredients = [i.lower() for i in (product.get("ingredients") or [])]
    free_from   = [f.lower() for f in (product.get("free_from") or [])]

    enriched = _enrich_tokens(query_tokens)
    return sum(
        _score_token(tok.text, tok.factor, product, title, variant, description, ingredients, free_from)
        for tok in enriched
    )


def compute_final_score(product: dict, query_tokens: list[str]) -> float:
    """Weighted ranking score.

    final_score = query_intent×80 + skin×30 + concern×25 + allergen×25
    """
    qi      = compute_query_intent_score(product, query_tokens)
    skin    = int(product.get("skin_score", 0) or 0)
    concern = int(product.get("concern_score", 0) or 0)
    allergen = len(product.get("free_from") or [])
    return qi * 80 + skin * 30 + concern * 25 + allergen * 25


def rerank_by_query_intent(
    products: list[dict],
    query_tokens: list[str],
) -> list[dict]:
    """Re-sort products by the full ranking formula.

    No-ops when query_tokens is empty — preserves original Cypher ordering.

    Sets per-product debug fields:
      query_intent_score — raw qi (ingredient + allergen intent matches)
      fragrance_score    — points from fragrance-free signals specifically
      allergen_score     — free_from count × 25 (weighted allergen component)
      final_score        — qi×80 + skin×30 + concern×25 + allergen×25 − penalty
      intent_sources     — which tiers contributed (exact/synonym/intent/fuzzy)
    """
    if not query_tokens:
        return products

    enriched = _enrich_tokens(query_tokens)

    # Detect whether any enriched token targets fragrance-free products.
    # True for queries like "fragrance free", "unscented", "hypoallergenic",
    # "eczema", "sensitive skin", "I react to fragrance", etc.
    allergen_intent = any(tok.text == "fragrance free" for tok in enriched)

    for p in products:
        title       = (p.get("title") or "").lower().replace("-", " ")
        variant     = (p.get("variant") or "").lower().replace("-", " ")
        description = (p.get("description") or "").lower().replace("-", " ")
        ingredients = [i.lower() for i in (p.get("ingredients") or [])]
        free_from   = [f.lower() for f in (p.get("free_from") or [])]

        qi = 0
        fragrance_qi = 0
        sources: list[str] = []
        for tok in enriched:
            pts = _score_token(
                tok.text, tok.factor, p,
                title, variant, description, ingredients, free_from,
            )
            if pts > 0:
                qi += pts
                if tok.text == "fragrance free":
                    fragrance_qi += pts
                if tok.source not in sources:
                    sources.append(tok.source)

        skin     = int(p.get("skin_score", 0) or 0)
        concern  = int(p.get("concern_score", 0) or 0)
        allergen = len(p.get("free_from") or [])

        # Fragrance penalty: applied when the user expressed allergy/sensitivity intent
        # AND the product is confirmed to contain fragrance as an ingredient.
        # Only fires when ingredient data is present (avoids false penalties for
        # products that simply have no ingredient list yet).
        penalty = 0
        if allergen_intent and ingredients:
            product_is_fragrance_free = any("fragrance" in ff for ff in free_from)
            product_has_fragrance = any(
                m in " ".join(ingredients) for m in _FRAGRANCED_MARKERS
            )
            if product_has_fragrance and not product_is_fragrance_free:
                penalty = PENALTY_FRAGRANCED

        p["query_intent_score"] = qi
        p["fragrance_score"]    = fragrance_qi
        p["allergen_score"]     = allergen * 25
        p["final_score"]        = qi * 80 + skin * 30 + concern * 25 + allergen * 25 - penalty
        p["intent_sources"]     = sources

        _log.debug(
            "RANK_DEBUG | %-50s | qi=%3d | frag=%d | skin=%d | concern=%d"
            " | allergen=%d | penalty=%d | final=%.0f | src=%s",
            p.get("title", "?")[:50],
            qi, fragrance_qi, skin, concern, allergen, penalty, p["final_score"], sources,
        )

    products.sort(key=lambda p: (-p["final_score"], p.get("price") or 0))
    return products
