// =============================================================================
// Dot & Key skin advisor — graph schema
// Run against an empty graph before ingest. FalkorDB (Cypher subset).
// =============================================================================

// -----------------------------------------------------------------------------
// Node labels
// -----------------------------------------------------------------------------
// Product        {sku, title, variant, category_raw, price, compare_at_price,
//                 description, active, size_g, url, image_url,
//                 cap_oil_control, cap_hydration, cap_barrier_repair,
//                 cap_brightening, cap_pigmentation, cap_acne,
//                 cap_pore_care, cap_sensitivity, cap_sun_protection, cap_lip_repair}
//                 cap_* float 0.0-10.0 — computed by generate_capability_scores.py
// Combo          {sku, title, price, compare_at_price, url, image_url, active}
// SkinType       {name}
// Concern        {name}
// Season         {name}
// Ingredient     {name}
// AllergenClass  {name}
// Category       {name}
// Texture        {name}
// Capability     {name}  — oil_control|hydration|barrier_repair|brightening|
//                          pigmentation|acne|pore_care|sensitivity|
//                          sun_protection|lip_repair
// ProductType    {name}  — gel_sunscreen|lip_mask|vitamin_c_serum|...

// -----------------------------------------------------------------------------
// Relationships
// -----------------------------------------------------------------------------
// (Product)-[:SUITS_SKIN_TYPE]->(SkinType)
// (Product)-[:TARGETS_CONCERN]->(Concern)
// (Product)-[:BEST_IN_SEASON]->(Season)
// (Product)-[:CONTAINS_INGREDIENT]->(Ingredient)
// (Product)-[:FREE_FROM]->(AllergenClass)
// (Product)-[:IN_CATEGORY]->(Category)
// (Product)-[:HAS_TEXTURE]->(Texture)
// (Product)-[:HAS_TYPE]->(ProductType)        — assigned by build_product_types.py
//
// (Combo)-[:INCLUDES]->(Product)
// (Combo)-[:SUITS_SKIN_TYPE]->(SkinType)
// (Combo)-[:TARGETS_CONCERN]->(Concern)
// (Combo)-[:IN_CATEGORY]->(Category {name:"combo"})
//
// Ingredient Knowledge (build_ingredient_knowledge.py):
// (Ingredient)-[:TREATS    {strength, confidence, explanation}]->(Concern)
// (Ingredient)-[:HELPS     {strength, confidence, explanation}]->(Concern)
// (Ingredient)-[:BEST_FOR  {strength, confidence, explanation}]->(Concern)
// (Ingredient)-[:PROVIDES  {strength}]->(Capability)
// (Ingredient)-[:SUPPORTS  {strength}]->(Capability)
//
// Ingredient Synergy (build_synergy_graph.py):
// (Ingredient)-[:SYNERGIZES_WITH {
//     evidence_strength, confidence, supported_concerns, explanation, source
// }]->(Ingredient)
//
// Product-Product Relations (generate_product_relations.py):
// (Product)-[:MORE_HYDRATING_THAN         {reason, confidence, source}]->(Product)
// (Product)-[:BETTER_FOR_OILY_SKIN_THAN   {reason, confidence, source}]->(Product)
// (Product)-[:BETTER_BARRIER_REPAIR_THAN  {reason, confidence, source}]->(Product)
// (Product)-[:MORE_BRIGHTENING_THAN       {reason, confidence, source}]->(Product)
// (Product)-[:BETTER_FOR_PIGMENTATION_THAN{reason, confidence, source}]->(Product)
// (Product)-[:BETTER_FOR_ACNE_THAN        {reason, confidence, source}]->(Product)
// (Product)-[:BETTER_PORE_CARE_THAN       {reason, confidence, source}]->(Product)
// (Product)-[:GENTLER_THAN                {reason, confidence, source}]->(Product)
// (Product)-[:SIMILAR_TO                  {reason, confidence}]->(Product)
// (Product)-[:BUDGET_ALTERNATIVE_TO       {reason, confidence}]->(Product)
// (Product)-[:PREMIUM_ALTERNATIVE_TO      {reason, confidence}]->(Product)
// (Product)-[:FRAGRANCE_FREE_ALTERNATIVE_TO {reason, confidence}]->(Product)
//
// ProductType Ontology (build_product_types.py):
// (ProductType)-[:HAS_SUBTYPE]->(ProductType)
// (ProductType)-[:PREPARES_FOR]->(ProductType)
// (ProductType)-[:FOLLOWED_BY]->(ProductType)
// (ProductType)-[:PAIRS_WELL_WITH]->(ProductType)
// (ProductType)-[:NOT_RECOMMENDED_WITH]->(ProductType)
// (ProductType)-[:MORE_INTENSIVE_THAN]->(ProductType)
// (ProductType)-[:CAN_REPLACE]->(ProductType)
// (ProductType)-[:COMPLEMENTS]->(ProductType)

// -----------------------------------------------------------------------------
// Indexes — sku is the natural key for Product and Combo.
// -----------------------------------------------------------------------------
CREATE INDEX FOR (p:Product) ON (p.sku);
CREATE INDEX FOR (c:Combo) ON (c.sku);
CREATE INDEX FOR (n:SkinType) ON (n.name);
CREATE INDEX FOR (n:Concern) ON (n.name);
CREATE INDEX FOR (n:Season) ON (n.name);
CREATE INDEX FOR (n:Ingredient) ON (n.name);
CREATE INDEX FOR (n:AllergenClass) ON (n.name);
CREATE INDEX FOR (n:Category) ON (n.name);
CREATE INDEX FOR (n:Texture) ON (n.name);
CREATE INDEX FOR (n:Capability) ON (n.name);
CREATE INDEX FOR (n:ProductType) ON (n.name);

// -----------------------------------------------------------------------------
// Pre-create taxonomy nodes so they exist even before any product references
// them (keeps the graph browsable/queryable for the full vocabulary, and lets
// the admin UI list all options for dropdowns).
// -----------------------------------------------------------------------------
UNWIND ['oily','dry','combination','normal','sensitive','all'] AS n
MERGE (:SkinType {name: n});

UNWIND ['acne','dark_spots','dullness','dryness','excess_oil','pigmentation',
        'ageing','damaged_skin_barrier','dehydration','clogged_pores',
        'dry_lips','fine_lines','open_pores','tanning','redness_irritation'] AS n
MERGE (:Concern {name: n});

UNWIND ['summer','monsoon','post_monsoon','winter'] AS n
MERGE (:Season {name: n});

UNWIND ['vitamin_c','niacinamide','hyaluronic','ceramides','salicylic','retinol',
        'glycolic','cica','watermelon','strawberry','blood_orange','blueberry',
        'pomegranate','mango','dragon_fruit','lime','ricewater','argan_oil',
        'liquid_ice','zinc_oxide','kojic_acid'] AS n
MERGE (:Ingredient {name: n});

UNWIND ['fragrance','alcohol','sulfate','paraben','silicone','essential_oil'] AS n
MERGE (:AllergenClass {name: n});

UNWIND ['sunscreen','moisturizer','face_wash','serum','toner','mask',
        'lip_care','eye_care','body_care','hair_care','combo'] AS n
MERGE (:Category {name: n});

UNWIND ['dewy','matte','gel','lightweight','rich'] AS n
MERGE (:Texture {name: n});
