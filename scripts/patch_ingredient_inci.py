#!/usr/bin/env python3
"""
Patch Ingredient nodes with authoritative INCI names, CAS numbers, and
official CosIng functions.

Source: EU CosIng database (publicly documented), PubChem.
Conflict policy: same as enrich_ingredient_knowledge — ON CREATE / CASE WHEN IS NULL.
"""
import argparse
import os

# Authoritative INCI data per graph ingredient node name.
# cosing_function values are the official EU CosIng function strings.
_DATA: dict[str, dict] = {
    "niacinamide": {
        "inci_name":        "Niacinamide",
        "cas_number":       "98-92-0",
        "cosing_function":  "SKIN CONDITIONING, VISCOSITY CONTROLLING",
    },
    "salicylic": {
        "inci_name":        "Salicylic Acid",
        "cas_number":       "69-72-7",
        "cosing_function":  "ANTI-ACNE, ANTIMICROBIAL, ANTISEBORRHOEIC, EXFOLIANT, KERATOLYTIC, PRESERVATIVE, SKIN CONDITIONING",
    },
    "retinol": {
        "inci_name":        "Retinol",
        "cas_number":       "68-26-8",
        "cosing_function":  "SKIN CONDITIONING",
    },
    "vitamin_c": {
        "inci_name":        "Ascorbic Acid",
        "cas_number":       "50-81-7",
        "cosing_function":  "ANTIOXIDANT, SKIN CONDITIONING",
    },
    "glycolic": {
        "inci_name":        "Glycolic Acid",
        "cas_number":       "79-14-1",
        "cosing_function":  "BUFFERING, EXFOLIANT, SKIN CONDITIONING",
    },
    "kojic_acid": {
        "inci_name":        "Kojic Acid",
        "cas_number":       "501-30-4",
        "cosing_function":  "ANTIOXIDANT, SKIN CONDITIONING",
    },
    "zinc_oxide": {
        "inci_name":        "Zinc Oxide",
        "cas_number":       "1314-13-2",
        "cosing_function":  "OPACIFYING, SKIN CONDITIONING, SKIN PROTECTING, UV ABSORBER, UV FILTER",
    },
    "ceramides": {
        "inci_name":        "Ceramide NP",
        "cas_number":       "100403-19-8",
        "cosing_function":  "SKIN CONDITIONING",
    },
    "hyaluronic": {
        "inci_name":        "Sodium Hyaluronate",
        "cas_number":       "9067-32-7",
        "cosing_function":  "FILM FORMING, MOISTURISING, SKIN CONDITIONING",
    },
    "argan_oil": {
        "inci_name":        "Argania Spinosa Kernel Oil",
        "cas_number":       "223748-44-5",
        "cosing_function":  "EMOLLIENT, HAIR CONDITIONING, SKIN CONDITIONING",
    },
    "cica": {
        "inci_name":        "Centella Asiatica Extract",
        "cas_number":       "84696-21-9",
        "cosing_function":  "SKIN CONDITIONING, SOOTHING",
    },
}


def run(args) -> None:
    from falkordb import FalkorDB
    graph = FalkorDB(host=args.host, port=args.port).select_graph(args.graph)

    existing = {
        r[0]
        for r in graph.query("MATCH (i:Ingredient) RETURN i.name").result_set
        if r[0]
    }
    updated = 0
    for iname, data in _DATA.items():
        if iname not in existing:
            print(f"  [skip] {iname} — not in graph")
            continue
        sets = []
        params: dict = {"name": iname}
        for key, val in data.items():
            if val:
                sets.append(
                    f"i.{key} = CASE WHEN i.{key} IS NULL OR i.{key} = '' "
                    f"THEN ${key} ELSE i.{key} END"
                )
                params[key] = val
        if sets:
            graph.query(
                f"MATCH (i:Ingredient {{name: $name}}) SET {', '.join(sets)}",
                params,
            )
            print(f"  [ok] {iname} → {data.get('inci_name')} / {data.get('cas_number')}")
            updated += 1

    print(f"\n[patch_ingredient_inci] {updated} nodes patched")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--host",  default=os.getenv("FALKORDB_HOST", "localhost"))
    p.add_argument("--port",  type=int, default=int(os.getenv("FALKORDB_PORT", "6379")))
    p.add_argument("--graph", default=os.getenv("FALKORDB_GRAPH", "dotandkey"))
    run(p.parse_args())
