"""
backend/profile.py

Two Redis stores:

1. Profile store  — skin profile, persists 60 days (PROFILE_TTL_SECONDS).
   Key:   profile:{profile_id}    Redis Hash
   Fields: skin_types, concerns, category, texture, allergen_free,
           budget, season, city, created_at, last_seen

2. Session store  — active conversation turns, expires 30 min.
   Key:   session:{profile_id}    Redis Hash
   Fields: turn_count, history (JSON list of {role, content} dicts)

3. History store  — product recommendation/purchase events, 365 days.
   Key:   history:{profile_id}    Redis Sorted Set
   Score: Unix timestamp
   Member: compact pipe string  sku|cat|ev|price|YYYY-MM-DD
           ev: P=purchased  R=recommended
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import redis

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

_redis: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            db=int(os.getenv("REDIS_DB", 0)),
            decode_responses=True,
        )
    return _redis


# ---------------------------------------------------------------------------
# TTLs
# ---------------------------------------------------------------------------

PROFILE_TTL  = int(os.getenv("PROFILE_TTL_SECONDS",  5_184_000))   # 60 days
SESSION_TTL  = int(os.getenv("SESSION_TTL_SECONDS",   1_800))        # 30 min
HISTORY_TTL  = int(os.getenv("HISTORY_TTL_SECONDS",  31_536_000))   # 365 days


# ---------------------------------------------------------------------------
# Profile store
# ---------------------------------------------------------------------------

EMPTY_PROFILE = {
    "skin_types":    "",   # comma-separated: "oily,combination"
    "concerns":      "",   # comma-separated: "acne,dark_spots"
    "category":      "",   # single value: "sunscreen"
    "texture":       "",   # single value: "lightweight"
    "allergen_free": "",   # comma-separated: "fragrance,alcohol"
    "price_tier":    "",   # tier: "under_300"|"under_600"|"under_1000"|"any"
    "size_pref":     "",   # "travel"|"standard"|"value"
    "season":        "",   # inferred at session/init
    "city":          "",   # inferred at session/init
    "created_at":    "",
    "last_seen":     "",
    # Persistent sensitivity / allergen memory (set by sensitivity_memory.py)
    "avoid_fragrance":       "false",
    "avoid_essential_oils":  "false",
    "avoid_known_allergens": "false",
    "fragrance_sensitive":   "false",
    "allergy_prone":         "false",
    "reactive_skin":         "false",
    "eczema_prone":          "false",
}


def _profile_key(profile_id: str) -> str:
    return f"profile:{profile_id}"


def load_profile(profile_id: str) -> dict:
    """Return profile dict. Missing fields default to empty string."""
    r = get_redis()
    data = r.hgetall(_profile_key(profile_id))
    if not data:
        return {}
    return data


def save_profile(profile_id: str, updates: dict) -> dict:
    """Merge updates into the profile hash and reset TTL.

    List fields (skin_types, concerns, allergen_free) are stored as
    comma-separated strings so they fit cleanly in a Redis Hash field.
    Callers can pass them as Python lists; this function serialises them.
    """
    r = get_redis()
    key = _profile_key(profile_id)

    # normalise list fields
    for list_field in ("skin_types", "concerns", "allergen_free"):
        if list_field in updates and isinstance(updates[list_field], list):
            updates[list_field] = ",".join(updates[list_field])

    # serialise boolean sensitivity flags → "true"/"false" strings for Redis
    from backend.sensitivity_memory import SENSITIVITY_FLAG_FIELDS
    for flag in SENSITIVITY_FLAG_FIELDS:
        if flag in updates and isinstance(updates[flag], bool):
            updates[flag] = "true" if updates[flag] else "false"

    now = datetime.now(timezone.utc).isoformat()
    updates["last_seen"] = now

    existing = r.hgetall(key)
    if not existing:
        updates.setdefault("created_at", now)
        r.hset(key, mapping={**EMPTY_PROFILE, **updates})
    else:
        r.hset(key, mapping=updates)

    r.expire(key, PROFILE_TTL)
    return r.hgetall(key)


def parse_profile(raw: dict) -> dict:
    """Convert comma-separated Redis strings back to Python lists,
    and "true"/"false" strings to Python booleans.
    Safe to call on an already-parsed dict."""
    out = dict(raw)
    for list_field in ("skin_types", "concerns", "allergen_free"):
        val = out.get(list_field, "")
        if isinstance(val, list):
            out[list_field] = [v for v in val if v]   # already parsed
        elif val:
            out[list_field] = [v for v in val.split(",") if v]
        else:
            out[list_field] = []

    from backend.sensitivity_memory import SENSITIVITY_FLAG_FIELDS
    for flag in SENSITIVITY_FLAG_FIELDS:
        val = out.get(flag, "false")
        if isinstance(val, bool):
            pass   # already a bool (in-memory dict passed to parse_profile)
        else:
            out[flag] = str(val).lower() in ("true", "1", "yes")
    return out


def profile_missing_fields(profile: dict) -> list[str]:
    """Return which fields are still empty — drives which question to ask next.

    Required fields (block auto-recommend until filled):
      category → skin_types → allergen_free → price_tier → size_pref

    Optional fields (collected after profile is ready, improve retrieval):
      concerns
    """
    parsed = parse_profile(profile)
    missing = []
    if not parsed.get("category"):
        missing.append("category")
    if not parsed.get("skin_types"):
        missing.append("skin_types")
    if not parsed.get("allergen_free"):
        missing.append("allergen_free")
    if not parsed.get("price_tier"):
        missing.append("price_tier")
    if not parsed.get("size_pref"):
        missing.append("size_pref")
    # optional refinement — only surfaced once all required fields are done
    if (parsed.get("skin_types") and parsed.get("allergen_free")
            and parsed.get("price_tier")):
        if not parsed.get("concerns"):
            missing.append("concerns")
    return missing


def profile_is_ready(profile: dict) -> bool:
    """True when the five required intake fields are set.
    Accepts both raw Redis dict and already-parsed dict."""
    parsed = parse_profile(profile)
    return bool(
        parsed.get("category")
        and parsed.get("skin_types")
        and parsed.get("price_tier")
        and parsed.get("allergen_free")
        and parsed.get("size_pref")
    )


def delete_profile(profile_id: str) -> None:
    get_redis().delete(_profile_key(profile_id))


# ---------------------------------------------------------------------------
# Session store (conversation turns)
# ---------------------------------------------------------------------------

def _session_key(profile_id: str) -> str:
    return f"session:{profile_id}"


def load_session(profile_id: str) -> list[dict]:
    """Return conversation history as list of {role, content} dicts."""
    r = get_redis()
    raw = r.hget(_session_key(profile_id), "history")
    if not raw:
        return []
    return json.loads(raw)


def append_turn(profile_id: str, role: str, content: str) -> None:
    """Append one turn and keep only the last 4 turns (2 user + 2 assistant).
    Older context is captured in the profile — no need to repeat it.
    """
    r = get_redis()
    key = _session_key(profile_id)
    history = load_session(profile_id)
    history.append({"role": role, "content": content})
    history = history[-4:]          # sliding window: last 4 turns
    r.hset(key, "history", json.dumps(history))
    r.expire(key, SESSION_TTL)


def clear_session(profile_id: str) -> None:
    get_redis().delete(_session_key(profile_id))


# ---------------------------------------------------------------------------
# History store (product events)
# ---------------------------------------------------------------------------
# Category abbreviation map for compact pipe format
CAT_ABBR = {
    "sunscreen": "SC", "moisturizer": "MZ", "face_wash": "FW",
    "serum": "SR", "toner": "TN", "mask": "MK",
    "lip_care": "LP", "eye_care": "EC", "body_care": "BC",
    "hair_care": "HC", "combo": "CO",
}

HISTORY_LEGEND = (
    "# h:sku|cat|ev|price|date "
    "cat:SC=sunscreen MZ=moisturizer FW=facewash SR=serum "
    "TN=toner MK=mask LP=lip EC=eye BC=body HC=hair CO=combo "
    "ev:P=bought R=shown"
)


def _history_key(profile_id: str) -> str:
    return f"history:{profile_id}"


def record_event(profile_id: str, sku: str, category: str,
                 event: str, price: float) -> None:
    """Record R (recommended) or P (purchased) event.
    Prunes entries older than 365 days on every write.
    """
    r = get_redis()
    key = _history_key(profile_id)
    cat_code = CAT_ABBR.get(category, "XX")
    date_str = datetime.now(timezone.utc).strftime("%y-%m-%d")
    member = f"{sku}|{cat_code}|{event}|{int(price)}|{date_str}"
    score = time.time()

    r.zadd(key, {member: score})
    r.expire(key, HISTORY_TTL)

    # prune entries older than 365 days
    cutoff = time.time() - HISTORY_TTL
    r.zremrangebyscore(key, "-inf", cutoff)


def load_history(profile_id: str) -> dict:
    """Return history analysis dict for use in system prompt."""
    r = get_redis()
    since = time.time() - HISTORY_TTL
    rows = r.zrangebyscore(_history_key(profile_id), since, "+inf")

    if not rows:
        return {"status": "lapsed", "block": "# history: none in past year"}

    purchased = [row for row in rows if row.split("|")[2] == "P"]
    status = "active" if purchased else "browsing"

    block = HISTORY_LEGEND + "\n" + "\n".join(rows)
    return {"status": status, "block": block, "has_purchases": bool(purchased)}


def compact_profile_for_prompt(profile_id: str) -> str:
    """Return a token-efficient single-line representation of the profile
    for inclusion in the system prompt."""
    raw = load_profile(profile_id)
    if not raw:
        return "profile: new_user"
    p = parse_profile(raw)
    parts = []
    if p.get("skin_types"):
        parts.append("skin:" + "+".join(p["skin_types"]))
    if p.get("concerns"):
        parts.append("concerns:" + "+".join(p["concerns"]))
    if p.get("category"):
        parts.append("cat:" + p["category"])
    if p.get("texture"):
        parts.append("texture:" + p["texture"])
    if p.get("allergen_free"):
        parts.append("avoid:" + "+".join(p["allergen_free"]))
    if p.get("price_tier"):
        parts.append("price:" + p["price_tier"])
    if p.get("size_pref"):
        parts.append("size:" + p["size_pref"])
    if p.get("season"):
        parts.append("season:" + p["season"])
    # Include active sensitivity flags so the LLM intro is aware of constraints
    from backend.sensitivity_memory import SENSITIVITY_FLAG_FIELDS
    active_flags = [f for f in SENSITIVITY_FLAG_FIELDS if p.get(f)]
    if active_flags:
        parts.append("sensitivity:" + "+".join(active_flags))
    return "profile: " + " ".join(parts) if parts else "profile: new_user"