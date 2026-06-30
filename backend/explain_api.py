"""
backend/explain_api.py

Builds the complete, canonical explainability payload for a given product and user profile.
Answers "Why this product?" and "What do I give up?" in one structured response.
"""

import json
from graph.capability_schema import CAPABILITY_AXES, CAPABILITY_LABELS

def build_explain_payload(sku: str, profile_id: str, graph) -> dict:
    from backend.profile import load_profile, parse_profile
    from backend.differentiation import get_category_rank_str
    
    raw = load_profile(profile_id)
    profile = parse_profile(raw) if raw else {}
    
    # Fetch product with all necessary props
    q = """
    MATCH (p:Product {sku: $sku})
    OPTIONAL MATCH (p)-[ri:CONTAINS_INGREDIENT]->(i:Ingredient)
    RETURN p, collect(DISTINCT [i.name, ri.role, ri.role_reason]) as ings
    """
    rows = graph.query(q, {"sku": sku}).result_set
    if not rows:
        return {"error": "Product not found"}
        
    p_node = rows[0][0]
    p_props = p_node.properties
    ings = rows[0][1]
    
    # Fetch better_than and worse_than examples (simplified, using rank for now)
    cat = p_props.get("category_raw")
    
    better_than = []
    worse_than = []
    if cat:
        # Just grab a couple SIMILAR_TO or same-category products
        q2 = """
        MATCH (p:Product {sku: $sku})-[:SIMILAR_TO]-(other:Product)
        RETURN other.sku, other.title, other.cap_oil_control, other.cap_hydration, other.cap_brightening
        LIMIT 3
        """
        similars = graph.query(q2, {"sku": sku}).result_set
        for sim in similars:
            sim_sku, sim_title, o_oil, o_hyd, o_bri = sim
            o_oil = o_oil or 0
            o_hyd = o_hyd or 0
            o_bri = o_bri or 0
            
            p_oil = p_props.get("cap_oil_control", 0)
            p_hyd = p_props.get("cap_hydration", 0)
            p_bri = p_props.get("cap_brightening", 0)
            
            axes_better = []
            axes_worse = []
            if p_oil > o_oil + 1.0: axes_better.append("oil_control")
            if p_hyd > o_hyd + 1.0: axes_better.append("hydration")
            if p_oil < o_oil - 1.0: axes_worse.append("oil_control")
            if p_hyd < o_hyd - 1.0: axes_worse.append("hydration")
            
            if axes_better:
                better_than.append({"sku": sim_sku, "title": sim_title, "axes": axes_better})
            if axes_worse:
                worse_than.append({"sku": sim_sku, "title": sim_title, "axes": axes_worse})
                
    # Build capabilities
    capabilities = {}
    overall_score = 0.0
    for ax in CAPABILITY_AXES:
        score = p_props.get(f"cap_{ax}") or 0.0
        conf = p_props.get(f"cap_{ax}_conf") or 0.0
        rank = p_props.get(f"rank_{ax}_in_cat")
        total = p_props.get(f"rank_{ax}_total")
        pct = p_props.get(f"rank_{ax}_percentile")
        
        overall_score += score
        if score > 0:
            capabilities[ax] = {
                "score": score,
                "confidence": conf,
                "rank": rank,
                "total": total,
                "percentile": pct
            }
            
    overall_conf = 0.0
    if capabilities:
        overall_conf = sum(c["confidence"] for c in capabilities.values()) / len(capabilities)
        
    # Build ingredient evidence
    ing_evidence = []
    for ing_name, role, reason in (ings or []):
        if role in ("primary", "supporting"):
            ing_evidence.append({
                "ingredient": ing_name,
                "role": role,
                "reason": reason
            })
            
    # Strengths / Tradeoffs
    def _safe_json(val):
        if isinstance(val, str):
            try:
                return json.loads(val)
            except Exception:
                pass
        return []
        
    strengths = _safe_json(p_props.get("unique_strengths", "[]"))
    tradeoffs = _safe_json(p_props.get("unique_weaknesses", "[]"))
    
    why_ranked = []
    for s in strengths:
        r_str = get_category_rank_str(p_props, s)
        if r_str: why_ranked.append(r_str)
        
    if p_props.get("identity_statement"):
        why_ranked.insert(0, p_props.get("identity_statement"))
        
    dna = {
        "primary": p_props.get("dna_primary", ""),
        "secondary": p_props.get("dna_secondary", ""),
        "label": p_props.get("dna_label", ""),
        "strengths": _safe_json(p_props.get("dna_strengths", "[]")),
        "weaknesses": _safe_json(p_props.get("dna_weaknesses", "[]")),
        "finish": p_props.get("dna_finish", ""),
        "texture": p_props.get("dna_texture", "")
    }

    return {
        "sku": p_props.get("sku"),
        "title": p_props.get("title"),
        "overall_score": round(overall_score, 1),
        "confidence": round(overall_conf, 2),
        "capabilities": capabilities,
        "ingredient_evidence": ing_evidence,
        "strengths": strengths,
        "tradeoffs": tradeoffs,
        "why_ranked": why_ranked,
        "better_than": better_than,
        "worse_than": worse_than,
        "product_dna": dna,
        "identity_statement": p_props.get("identity_statement", "")
    }
