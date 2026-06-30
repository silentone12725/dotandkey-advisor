"""
backend/explainability.py

Generates structured ranking explanations from graph evidence — not LLM prose.

Every recommended product receives an `explanation` dict containing:
  - why_ranked:          list[str]  "Why this is ranked here"
  - tradeoffs:          list[str]  "What this product doesn't excel at"
  - ingredient_evidence: list[str]  "Specific ingredient→concern evidence"
  - vs_next:            dict       "What you gain/lose vs the next rank"
  - allergen_notes:     list[str]  "Allergen deductions/confirmations"
  - score_pct:          int        "Score as 0-100 percentage of max"

All strings come from structured graph data — zero LLM calls.
"""

from graph.capability_schema import CAPABILITY_AXES, CAPABILITY_LABELS, CONCERN_TO_CAPABILITY
from graph.capability_scorer import score_product, explain_capability
from graph.ingredient_knowledge import INGREDIENT_CONCERN_EDGES

# Pre-index ingredient concern evidence for fast lookup
_ING_CONCERN_INDEX: dict[tuple, tuple] = {}
for _ing, _rel, _concern, _strength, _conf, _expl in INGREDIENT_CONCERN_EDGES:
    _ING_CONCERN_INDEX[(_ing, _concern)] = (_rel, _strength, _expl)


def _concern_label(concern: str) -> str:
    return concern.replace("_", " ").title()


def _cap_label(axis: str, score: float) -> str:
    label = CAPABILITY_LABELS.get(axis, axis.title())
    return f"{label} ({score:.1f}/10)"


def build_ranking_explanation(
    product: dict,
    rank: int,
    all_products: list[dict],
    user_profile: dict,
    query_tokens: list[str],
) -> dict:
    """Build a full explanation dict for one ranked product.

    Args:
        product:      Product dict from retrieval (includes cap_* props)
        rank:         1-based rank position
        all_products: All ranked products (for vs_next comparison)
        user_profile: Parsed user profile dict
        query_tokens: Extracted query intent tokens
    """
    # Capability scores — prefer pre-computed cap_* props, fall back to scorer
    caps = _get_cap_scores(product)

    # User's stated concerns from profile
    user_concerns = user_profile.get("concerns") or []
    user_skin_types = user_profile.get("skin_types") or []

    why_ranked: list[str] = []
    tradeoffs: list[str] = []
    ingredient_evidence: list[str] = []
    allergen_notes: list[str] = []

    # 1. Capability highlights — top axes above threshold
    HIGHLIGHT_THRESHOLD = 6.0
    strong_axes = [(ax, caps[ax]) for ax in CAPABILITY_AXES if caps.get(ax, 0) >= HIGHLIGHT_THRESHOLD]
    strong_axes.sort(key=lambda x: -x[1])
    for ax, score in strong_axes[:3]:
        why_ranked.append(f"Strong {CAPABILITY_LABELS.get(ax, ax)}: {score:.1f}/10")

    # 2. Concern alignment — match user concerns to cap axes
    for concern in user_concerns:
        axes = CONCERN_TO_CAPABILITY.get(concern, [])
        for ax in axes:
            score = caps.get(ax, 0)
            if score >= 5.0:
                why_ranked.append(
                    f"Addresses your {_concern_label(concern)} concern "
                    f"({CAPABILITY_LABELS.get(ax, ax)}: {score:.1f}/10)"
                )
                break  # one line per concern

    # 3. Ingredient evidence for matched concerns
    ingredients = [i.lower() for i in (product.get("ingredients") or [])]
    for concern in user_concerns:
        for ing in ingredients:
            key = (ing, concern)
            if key in _ING_CONCERN_INDEX:
                rel, strength, expl = _ING_CONCERN_INDEX[key]
                display_ing = ing.replace("_", " ").title()
                ingredient_evidence.append(
                    f"{display_ing} {rel.lower().replace('_', ' ')} {_concern_label(concern)} "
                    f"(strength: {strength:.2f}) — {expl}"
                )

    # 4. Fragrance-free confirmation
    free_from = [f.lower() for f in (product.get("free_from") or [])]
    requested_allergens = [a for a in (user_profile.get("allergen_free") or []) if a != "none"]
    if requested_allergens:
        confirmed = [a for a in requested_allergens if any(a in ff for ff in free_from)]
        unconfirmed = [a for a in requested_allergens if a not in confirmed]
        for a in confirmed:
            why_ranked.append(f"Confirmed {a.title()}-free — matches your preference")
        for a in unconfirmed:
            allergen_notes.append(f"Could not confirm {a.title()}-free — check ingredient list")

    # 5. Skin type match
    matched_skin = product.get("matched_skin_types") or []
    if matched_skin:
        why_ranked.append(
            f"Suits {' & '.join(s.title() for s in matched_skin[:2])} skin"
        )

    # 6. Budget fit
    price = product.get("price") or 0
    max_price = user_profile.get("max_price")
    if max_price and price and price <= max_price:
        why_ranked.append(f"Within budget (₹{price:.0f})")

    # 7. vs_next — compare against the next rank
    vs_next: dict = {}
    if rank <= len(all_products) - 1:
        next_p = all_products[rank]  # rank is 1-based, list is 0-based
        next_caps = _get_cap_scores(next_p)
        gained = []
        lost = []
        for ax in CAPABILITY_AXES:
            this_score = caps.get(ax, 0)
            next_score = next_caps.get(ax, 0)
            delta = this_score - next_score
            if delta >= 1.5:
                gained.append(f"Better {CAPABILITY_LABELS.get(ax, ax)} (+{delta:.1f})")
            elif delta <= -1.5:
                lost.append(f"Lower {CAPABILITY_LABELS.get(ax, ax)} ({delta:.1f})")
        vs_next = {
            "vs_product": next_p.get("title", ""),
            "gained": gained[:3],
            "lost": lost[:3],
        }

    # 8. Tradeoffs — axes where this product scores low but user might care
    LOW_THRESHOLD = 3.0
    for concern in user_concerns:
        axes = CONCERN_TO_CAPABILITY.get(concern, [])
        for ax in axes:
            score = caps.get(ax, 0)
            if score < LOW_THRESHOLD:
                tradeoffs.append(
                    f"Limited {CAPABILITY_LABELS.get(ax, ax)} ({score:.1f}/10)"
                )

    # 9. Score percentage
    max_possible = sum(caps.values())
    score_pct = min(99, int(
        (product.get("final_score", 0) / max(1, max_possible * 10)) * 100
    )) if max_possible else 0
    # Simpler: normalise rank to rough score
    score_pct = max(60, 98 - (rank - 1) * 8)

    return {
        "rank": rank,
        "score_pct": score_pct,
        "why_ranked": why_ranked[:5],
        "tradeoffs": tradeoffs[:3],
        "ingredient_evidence": ingredient_evidence[:4],
        "vs_next": vs_next,
        "allergen_notes": allergen_notes,
        "capability_scores": {
            ax: round(caps.get(ax, 0.0), 1) for ax in CAPABILITY_AXES
        },
    }


def build_comparison_explanation(
    product_a: dict,
    product_b: dict,
    axis: str,
) -> str:
    """One sentence explaining why A beats B on a specific capability axis."""
    caps_a = _get_cap_scores(product_a)
    caps_b = _get_cap_scores(product_b)
    score_a = caps_a.get(axis, 0)
    score_b = caps_b.get(axis, 0)
    axis_label = CAPABILITY_LABELS.get(axis, axis.title())
    title_a = product_a.get("title", "Product A")
    title_b = product_b.get("title", "Product B")

    ev_a = explain_capability(product_a, axis)
    evidence_str = f" ({ev_a[0]})" if ev_a else ""

    return (
        f"{title_a} scores higher on {axis_label} ({score_a:.1f} vs {score_b:.1f})"
        f"{evidence_str}."
    )


def _get_cap_scores(product: dict) -> dict[str, float]:
    """Read cap_* props from product dict, fall back to computing them."""
    scores = {}
    for ax in CAPABILITY_AXES:
        prop = f"cap_{ax}"
        val = product.get(prop)
        if val is not None:
            scores[ax] = float(val)

    if not scores:
        # Pre-computed scores not present — compute inline (slower but correct)
        scores = score_product(product)

    return scores
