"""
backend/user_graph.py

Separate FalkorDB graph ("dotandkey_users") that stores behavioural data:
which products each user was shown / chose, and a lightweight copy of their
profile for similarity queries.

Kept entirely separate from the product graph ("dotandkey") so product data
and user data evolve independently and the product graph stays read-only in
production.

Public API
----------
sync_user_profile(profile_id, profile_dict)
    Write/update a UserProfile node from the Redis profile.  Call this once
    per recommend turn so the user graph stays fresh.

record_user_choice(profile_id, sku, event, price)
    Append a CHOSE edge from the user to a ProductRef node.
    event: "R" = shown / recommended, "P" = purchased (cart-add).

get_collaborative_picks(profile_id, category, limit=5) -> list[str]
    Return SKUs that users with the same skin type and concerns chose most
    often, excluding products already shown to this user.

get_trending_for_skin_type(skin_types, category, limit=5) -> list[str]
    Return most-chosen SKUs for a given skin type globally (no user context
    needed — useful for brand-new users before any choices are recorded).
"""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_graph_instance = None


def _get_user_graph():
    global _graph_instance
    if _graph_instance is None:
        from falkordb import FalkorDB
        db = FalkorDB(
            host=os.getenv("FALKORDB_HOST", "localhost"),
            port=int(os.getenv("FALKORDB_PORT", 6379)),
        )
        graph_name = os.getenv("FALKORDB_USER_GRAPH", "dotandkey_users")
        _graph_instance = db.select_graph(graph_name)
        _ensure_schema(_graph_instance)
    return _graph_instance


def _ensure_schema(graph) -> None:
    """Apply the user graph schema once on first connection.
    Idempotent — uses CREATE INDEX which is a no-op if the index exists.
    """
    schema_path = (
        Path(__file__).resolve().parent.parent / "graph" / "user_graph_schema.cypher"
    )
    src = schema_path.read_text()
    no_comments = "\n".join(
        line for line in src.splitlines() if not line.strip().startswith("//")
    )
    for stmt in [s.strip() for s in no_comments.split(";") if s.strip()]:
        try:
            graph.query(stmt)
        except Exception:
            pass   # index already exists — safe to ignore


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------

def sync_user_profile(profile_id: str, profile: dict) -> None:
    """Upsert a UserProfile node with the current profile values.
    Lists are stored comma-separated to match the Redis encoding.
    """
    def _cs(val):
        if isinstance(val, list):
            return ",".join(sorted(val))   # sort for order-independent matching
        return val or ""

    graph = _get_user_graph()
    graph.query(
        "MERGE (u:UserProfile {id: $id}) "
        "SET u.skin_types = $skin_types, u.concerns = $concerns, "
        "    u.category = $category, u.price_tier = $price_tier, "
        "    u.size_pref = $size_pref, u.season = $season, "
        "    u.last_seen = $last_seen",
        {
            "id": profile_id,
            "skin_types": _cs(profile.get("skin_types")),
            "concerns":   _cs(profile.get("concerns")),
            "category":   profile.get("category", ""),
            "price_tier": profile.get("price_tier", ""),
            "size_pref":  profile.get("size_pref", ""),
            "season":     profile.get("season", ""),
            "last_seen":  datetime.now(timezone.utc).isoformat(),
        },
    )


def record_user_choice(
    profile_id: str, sku: str, event: str, price: float, category: str = ""
) -> None:
    """Append a CHOSE edge. Creates ProductRef if it doesn't exist yet."""
    graph = _get_user_graph()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    graph.query(
        "MERGE (p:ProductRef {sku: $sku}) "
        "WITH p "
        "MATCH (u:UserProfile {id: $id}) "
        "CREATE (u)-[:CHOSE {event: $event, price: $price, date: $date, category: $cat}]->(p)",
        {
            "sku":   sku,
            "id":    profile_id,
            "event": event,
            "price": price,
            "date":  today,
            "cat":   category,
        },
    )


# ---------------------------------------------------------------------------
# Read operations — collaborative filtering
# ---------------------------------------------------------------------------

def get_collaborative_picks(
    profile_id: str,
    category: str,
    limit: int = 5,
) -> list[str]:
    """Return SKUs chosen by users with the same skin_types as this user,
    weighted by how many similar users chose each one, excluding SKUs this
    user has already been shown.

    Returns an empty list when there are no similar users yet or the user
    graph is unreachable (fail-open — caller must tolerate an empty list).
    """
    try:
        graph = _get_user_graph()

        # Get this user's skin_types
        res = graph.query(
            "MATCH (u:UserProfile {id: $id}) RETURN u.skin_types AS st",
            {"id": profile_id},
        )
        if not res.result_set:
            return []
        my_skin = res.result_set[0][0] or ""

        # SKUs already shown to this user (avoid re-showing)
        shown_res = graph.query(
            "MATCH (u:UserProfile {id: $id})-[:CHOSE]->(p:ProductRef) "
            "RETURN p.sku AS sku",
            {"id": profile_id},
        )
        shown_skus = [r[0] for r in shown_res.result_set]

        # Popular picks among similar-skin users for the same category.
        # Match category on the CHOSE edge (not UserProfile.category) so
        # users who've explored multiple categories still match correctly.
        rows = graph.query(
            "MATCH (other:UserProfile)-[c:CHOSE]->(p:ProductRef) "
            "WHERE other.skin_types = $skin AND c.category = $cat "
            "  AND other.id <> $id "
            "RETURN p.sku AS sku, count(*) AS freq "
            "ORDER BY freq DESC "
            "LIMIT $limit",
            {
                "skin":  my_skin,
                "cat":   category,
                "id":    profile_id,
                "limit": limit + len(shown_skus),
            },
        ).result_set

        picks = [r[0] for r in rows if r[0] not in shown_skus]
        return picks[:limit]

    except Exception:
        return []   # user graph down / empty — degrade gracefully


def get_trending_for_skin_type(
    skin_types: list[str],
    category: str,
    limit: int = 5,
) -> list[str]:
    """Return globally popular SKUs for a skin type, across all users.
    Used as a cold-start fallback when a brand-new user has no similar peers.
    """
    try:
        graph = _get_user_graph()
        skin_csv = ",".join(sorted(skin_types))
        rows = graph.query(
            "MATCH (u:UserProfile)-[:CHOSE]->(p:ProductRef) "
            "WHERE u.skin_types = $skin AND u.category = $cat "
            "RETURN p.sku AS sku, count(*) AS freq "
            "ORDER BY freq DESC LIMIT $limit",
            {"skin": skin_csv, "cat": category, "limit": limit},
        ).result_set
        return [r[0] for r in rows]
    except Exception:
        return []
