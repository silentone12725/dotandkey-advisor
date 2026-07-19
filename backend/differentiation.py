"""
backend/differentiation.py

Query helpers and sentence builders for Product Differentiation (Pillar 1).
Reads the rank_*_in_cat properties and formats them into UI-ready sentences.
"""

from graph.capability_schema import CAPABILITY_LABELS


def get_category_rank_str(product: dict, axis: str) -> str | None:
    """Return a human-readable rank string for a capability axis.
    e.g., "Best oil control in sunscreens (#1 of 23)"
    e.g., "Better pore care than 91% of sunscreens"
    """
    rank = product.get(f"rank_{axis}_in_cat")
    total = product.get(f"rank_{axis}_total")
    pct = product.get(f"rank_{axis}_percentile")
    cat = (product.get("category_raw") or "products").lower()
    if cat.endswith("care"):
        cat = cat.replace("_", " ") + " products"
    else:
        if not cat.endswith("s"):
            cat += "s"

    if not rank or not total or pct is None:
        return None

    label = CAPABILITY_LABELS.get(axis, axis.title()).lower()

    if rank == 1:
        return f"Best {label} in {cat} (#1 of {total})"
    elif rank <= 3:
        return f"Top 3 {label} in {cat} (#{rank} of {total})"
    elif pct >= 0.80:
        return f"Better {label} than {int(pct * 100)}% of {cat}"
    elif pct >= 0.50:
        return f"Above average {label} for {cat}"
    
    return None
