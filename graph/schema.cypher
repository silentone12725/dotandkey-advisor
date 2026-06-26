// =============================================================================
// Dot & Key skin advisor — graph schema
// Run against an empty graph before ingest. FalkorDB (Cypher subset).
// =============================================================================

// -----------------------------------------------------------------------------
// Node labels
// -----------------------------------------------------------------------------
// Product        {sku, title, variant, category_raw, price, compare_at_price,
//                 description, active, size_g, url, image_url}
//                 size_g: normalised size in grams/ml (for size-tier filtering)
// Combo          {sku, title, price, compare_at_price, url, image_url, active}
//                 Represents a real multi-product bundle from the store.
//                 Skin-type/concern edges mirror the component products' tags.
// SkinType       {name}            -- oily | dry | combination | normal | sensitive | all
// Concern        {name}            -- acne | dark_spots | dullness | ...
// Season         {name}            -- summer | monsoon | post_monsoon | winter
// Ingredient     {name}            -- vitamin_c | niacinamide | ...
// AllergenClass  {name}            -- fragrance | alcohol | sulfate | ...
// Category       {name}            -- sunscreen | moisturizer | face_wash | ...
// Texture        {name}            -- dewy | matte | gel | lightweight | rich

// -----------------------------------------------------------------------------
// Relationships
// -----------------------------------------------------------------------------
// (Product)-[:SUITS_SKIN_TYPE]->(SkinType)
// (Product)-[:TARGETS_CONCERN]->(Concern)
// (Product)-[:BEST_IN_SEASON]->(Season)
// (Product)-[:CONTAINS_INGREDIENT]->(Ingredient)
// (Product)-[:FREE_FROM]->(AllergenClass)        -- explicit "free of X" claim
// (Product)-[:IN_CATEGORY]->(Category)
// (Product)-[:HAS_TEXTURE]->(Texture)
//
// (Combo)-[:INCLUDES]->(Product)                 -- combo components
// (Combo)-[:SUITS_SKIN_TYPE]->(SkinType)         -- inherited from component tags
// (Combo)-[:TARGETS_CONCERN]->(Concern)
// (Combo)-[:IN_CATEGORY]->(Category {name:"combo"})
//
// Not populated in Phase 0 (require manual curation, added in a later phase):
// (Product)-[:COMPATIBLE_WITH {routine_order, time: "AM"|"PM"}]->(Product)
// (Product)-[:CONFLICTS_WITH {reason}]->(Product)
// (Ingredient)-[:SYNERGIZES_WITH]->(Ingredient)

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
