// =============================================================================
// Dot & Key — User Preference Graph schema
// Stored in a SEPARATE FalkorDB graph ("dotandkey_users") from the product
// graph ("dotandkey"), so product data and behavioural data stay decoupled.
//
// Run once to initialise: applied automatically by user_graph.py on first use.
// =============================================================================

// -----------------------------------------------------------------------------
// Node labels
// -----------------------------------------------------------------------------
// UserProfile  {id, skin_types, concerns, category, price_tier, size_pref,
//               season, created_at, last_seen}
//              skin_types / concerns stored as comma-separated strings (matches
//              the Redis profile representation so syncing is a direct copy).
//
// ProductRef   {sku}
//              Lightweight reference node — mirrors Product.sku in the product
//              graph. Kept minimal so the user graph stays fast and small; all
//              product metadata (title, price, etc.) is fetched from the product
//              graph at query time.

// -----------------------------------------------------------------------------
// Relationships
// -----------------------------------------------------------------------------
// (UserProfile)-[:CHOSE {event, price, date}]->(ProductRef)
//   event: "R" = recommended/shown  "P" = purchased
//   Recorded whenever a recommendation fires or a cart-add happens.

// -----------------------------------------------------------------------------
// Indexes
// -----------------------------------------------------------------------------
CREATE INDEX FOR (u:UserProfile) ON (u.id);
CREATE INDEX FOR (u:UserProfile) ON (u.skin_types);
CREATE INDEX FOR (p:ProductRef) ON (p.sku);
