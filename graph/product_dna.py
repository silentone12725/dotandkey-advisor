"""
graph/product_dna.py

Computes normalized identity profiles (Product DNA) for every product.
Identifies primary/secondary capabilities, strengths/weaknesses, and generates
a human-readable identity label (e.g., "Oil Control Sunscreen").
"""

import json

from graph.capability_schema import CAPABILITY_AXES, CAPABILITY_LABELS, cap_prop


def compute_product_dna(product: dict) -> dict[str, str]:
    """Return a dictionary of DNA properties for the given product.
    
    product dict must contain capability scores (e.g., cap_oil_control) and category_raw.
    """
    scores = []
    for axis in CAPABILITY_AXES:
        score = product.get(cap_prop(axis)) or 0.0
        scores.append((axis, score))
        
    scores.sort(key=lambda x: -x[1])
    
    primary = ""
    secondary = ""
    strengths = []
    weaknesses = []
    
    if scores and scores[0][1] >= 6.0:
        primary = scores[0][0]
        
    if len(scores) > 1 and scores[1][1] >= 4.5:
        secondary = scores[1][0]
        
    for axis, score in scores:
        if 3.0 <= score < 4.5 and axis not in (primary, secondary):
            strengths.append(axis)
        elif score < 2.0:
            weaknesses.append(axis)
            
    # Category name for label
    cat_raw = (product.get("category_raw") or "Product").replace("_", " ").title()
    if cat_raw.lower() == "combo":
        cat_raw = "Bundle"
        
    # Generate label
    label = cat_raw
    if primary:
        primary_label = CAPABILITY_LABELS.get(primary, primary.replace("_", " ").title())
        label = f"{primary_label} {cat_raw}"
        
    # Finishes & Textures - inferred from ingredients for now, or fall back to generic.
    # In a real system, these would come from the HAS_TEXTURE edges. 
    # For now, we'll extract from product dict if provided.
    finish = product.get("texture", "Natural")
    texture = product.get("texture", "Cream/Gel")
    
    return {
        "dna_primary": primary,
        "dna_secondary": secondary,
        "dna_strengths": json.dumps(strengths),
        "dna_weaknesses": json.dumps(weaknesses),
        "dna_label": label,
        "dna_finish": finish,
        "dna_texture": texture,
    }
