"""
backend/session.py

Handles /session/init:
  1. IP → city (ip-api.com, free, no key)
  2. City → weather (tomorrow.io, cached 6hrs in Redis)
  3. Weather → India 4-season classification
  4. Returns: {session_id, season, city, greeting, is_returning}
"""

import hashlib
import ipaddress
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

_log = logging.getLogger(__name__)

import httpx
import redis as redis_lib

from backend.llm_adapter import one_shot
from backend.profile import (
    compact_profile_for_prompt,
    get_redis,
    load_profile,
    parse_profile,
    save_profile,
)

TOMORROW_KEY = os.getenv("TOMORROW_API_KEY", "")
WEATHER_TTL  = 6 * 3600   # 6 hours


# ---------------------------------------------------------------------------
# IP → City
# ---------------------------------------------------------------------------

def _is_private_ip(ip: str) -> bool:
    """True for loopback, link-local, private RFC-1918, and Docker bridge ranges."""
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return True   # unparseable (e.g. "testclient") → treat as private


async def _resolve_public_ip(client: httpx.AsyncClient) -> str:
    """Fetch the server's own public IP via ipify.org, cached 24h in Redis."""
    cache_key = "server:public_ip"
    try:
        cached = get_redis().get(cache_key)
        if cached:
            return cached if isinstance(cached, str) else cached.decode()
    except Exception:
        pass
    try:
        resp = await client.get("https://api.ipify.org?format=json", timeout=3.0)
        pub_ip = resp.json().get("ip", "")
        if pub_ip:
            try:
                get_redis().setex(cache_key, 24 * 3600, pub_ip)
            except Exception:
                pass
            return pub_ip
    except Exception:
        pass
    return ""


async def _get_city(client: httpx.AsyncClient, ip: str) -> tuple[str, str]:
    """Returns (city, country_code). Falls back to ('Mumbai', 'IN'). Cached 24h by IP."""
    if not ip or _is_private_ip(ip):
        # Private/loopback IP (localhost, Docker bridge, LAN) — use the server's
        # own public IP so local dev still shows the developer's real city.
        ip = await _resolve_public_ip(client)
        if not ip:
            return "Mumbai", "IN"
    cache_key = f"ip2city:{ip}"
    try:
        cached = get_redis().get(cache_key)
        if cached:
            cached_str = cached if isinstance(cached, str) else cached.decode()
            city, cc = cached_str.split("|", 1)
            return city, cc
    except Exception:
        pass
    try:
        resp = await client.get(f"http://ip-api.com/json/{ip}?fields=city,countryCode",
                                timeout=3.0)
        data = resp.json()
        city = data.get("city", "Mumbai")
        cc = data.get("countryCode", "IN")
        try:
            get_redis().setex(cache_key, 24 * 3600, f"{city}|{cc}")
        except Exception:
            pass
        return city, cc
    except Exception:
        return "Mumbai", "IN"


# ---------------------------------------------------------------------------
# City → Weather → Season
# ---------------------------------------------------------------------------

async def _get_weather(client: httpx.AsyncClient, city: str) -> dict:
    """Fetch weather for city, cached in Redis under weather:{city_slug}."""
    r = get_redis()
    cache_key = f"weather:{city.lower().replace(' ', '_')}"
    cached = r.get(cache_key)
    if cached:
        return json.loads(cached)

    if not TOMORROW_KEY:
        # no API key — return a sensible default
        return {"temp": 28, "humidity": 70, "condition": "cloudy"}

    try:
        url = "https://api.tomorrow.io/v4/weather/realtime"
        resp = await client.get(url, params={
            "location": city,
            "apikey":   TOMORROW_KEY,
            "units":    "metric",
        }, timeout=5.0)
        data = resp.json()
        values = data["data"]["values"]
        weather = {
            "temp":      values.get("temperature", 28),
            "humidity":  values.get("humidity", 60),
            "condition": values.get("weatherCode", 1000),
        }
    except Exception:
        weather = {"temp": 28, "humidity": 60, "condition": 1000}

    r.setex(cache_key, WEATHER_TTL, json.dumps(weather))
    return weather


def _classify_season(month: int, humidity: float, temp: float) -> str:
    """India 4-season classifier."""
    if 7 <= month <= 9 and humidity >= 65:
        return "monsoon"
    if month in (10, 11):
        return "post_monsoon"
    if month in (12, 1, 2):
        return "winter"
    return "summer"   # March – June


# ---------------------------------------------------------------------------
# Greeting generation
# ---------------------------------------------------------------------------

async def _generate_greeting(
    profile_id: str,
    is_returning: bool,
    city: str,
    season: str,
    page_context: str,        # "homepage" | "product:{product_name}"
) -> str:
    from backend.playbooks.base import load_prompt

    greeting_template = load_prompt("greeting.md")
    raw_profile = load_profile(profile_id)
    cache_key: str | None = None

    if page_context.startswith("product:"):
        product_name = page_context.split("product:", 1)[1]
        section = greeting_template.split("━━━ GREETING — PRODUCT PAGE ━━━")[1].strip()
        prompt = (section
                  .replace("{product_name}", product_name)
                  .replace("{season}", season)
                  .replace("{city}", city))

    elif is_returning and raw_profile:
        parsed = parse_profile(raw_profile)
        last_seen = raw_profile.get("last_seen", "")
        try:
            last_seen_dt = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
            if last_seen_dt.tzinfo is None:
                last_seen_dt = last_seen_dt.replace(tzinfo=timezone.utc)
            delta = (datetime.now(timezone.utc) - last_seen_dt).days
        except Exception:
            delta = 0
        skin_summary = ", ".join(parsed.get("skin_types", []) or ["your skin type"])
        section = greeting_template.split("━━━ GREETING — RETURNING USER ━━━")[1]
        section = section.split("━━━")[0].strip()
        prompt = (section
                  .replace("{profile_summary}", compact_profile_for_prompt(profile_id))
                  .replace("{last_seen_days}", str(delta)))

    else:
        # New user on homepage — cacheable by (season, city).
        cache_key = f"greeting:new:{season}:{city.lower()}"
        try:
            cached = get_redis().get(cache_key)
            if cached:
                return cached if isinstance(cached, str) else cached.decode()
        except Exception:
            cache_key = None

        section = greeting_template.split("━━━ GREETING — NEW USER (homepage) ━━━")[1]
        section = section.split("━━━")[0].strip()
        prompt = (section
                  .replace("{season}", season)
                  .replace("{city}", city))

    # Season-aware fallback greetings — used if LLM call fails or key missing
    _FALLBACKS = {
        "summer":      "Heading out in this {city} heat? Let's find you the right sunscreen and skincare for summer.",
        "monsoon":     "Mumbai's humidity can be tricky on skin — tell me your skin type and I'll help you find what works.",
        "post_monsoon":"Good time to refresh your routine — what are you looking for today?",
        "winter":      "Winter can be tough on skin. Tell me what you're working with and I'll point you in the right direction.",
    }

    # Reuse the same tone/formatting rules every other playbook follows —
    # loaded from prompts/system.md rather than duplicated here, so a
    # change to tone rules only needs to happen in one file.
    from backend.playbooks.base import load_prompt
    full_system_md = load_prompt("system.md")
    # Strip the CONTEXT section (profile/history placeholders) — greeting
    # doesn't need them, just the persona + tone + formatting rules.
    rules_only = full_system_md.split("━━━ CONTEXT")[0].strip()
    system = rules_only + "\n\nWrite ONLY the greeting text, nothing else."
    try:
        result = await one_shot(
            system=system,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=80,
            temperature=0.8,
        )
        if result and result.strip():
            greeting = result.strip()
            # Cache new-user homepage greetings for 6 hours.
            if cache_key:
                try:
                    get_redis().setex(cache_key, 6 * 3600, greeting)
                except Exception:
                    pass
            return greeting
        _log.warning("LLM returned empty greeting — using fallback")
    except Exception as exc:
        _log.error("LLM error in greeting: %s", exc)

    fallback = _FALLBACKS.get(season, "What are you looking for today?")
    return fallback.replace("{city}", city)


# ---------------------------------------------------------------------------
# Public: init_session
# ---------------------------------------------------------------------------

async def init_session(
    profile_id: str,
    client_ip: str,
    page_context: str = "homepage",   # "homepage" | "product:{name}"
) -> dict:
    """Full session init. Returns everything the widget needs to render."""

    async with httpx.AsyncClient() as client:
        city, country = await _get_city(client, client_ip)
        weather = await _get_weather(client, city)

    month   = datetime.now(timezone.utc).month
    season  = _classify_season(month, weather["humidity"], weather["temp"])

    # persist season + city into profile (used by retrieval + prompts)
    existing = load_profile(profile_id)
    is_returning = bool(existing)
    save_profile(profile_id, {"season": season, "city": city})

    # Seed the structured returning-user flow (see playbooks/returning_user.py)
    # so the very next message — including the entry-chip click the widget
    # renders for is_returning — is handled deterministically instead of
    # falling through to normal classify() and getting misrouted.
    # Homepage only: a returning user landing on a product page is asking
    # about THAT product, not resuming a prior session.
    if is_returning and page_context == "homepage":
        from backend.playbooks.returning_user import set_returning_step
        set_returning_step(profile_id, "awaiting_choice")

    greeting = await _generate_greeting(
        profile_id, is_returning, city, season, page_context
    )

    from backend.chip_options import CATEGORY_CHIPS
    return {
        "profile_id":   profile_id,
        "city":         city,
        "season":       season,
        "is_returning": is_returning,
        "greeting":     greeting,
        "weather": {
            "temp":     weather["temp"],
            "humidity": weather["humidity"],
        },
        "initial_chips": {
            "field": "category", "multi_select": False, "options": CATEGORY_CHIPS,
        },
        "returning_chips": {
            "field": "returning_check", "multi_select": False,
            "options": [
                {"value": "same",     "label": "Same as before"},
                {"value": "changed",  "label": "Something has changed"},
                {"value": "concerns", "label": "Have concerns with a previous purchase"},
            ],
        },
        "track_order": {"label": "Track my order", "url": "https://dotandkey.clickpost.ai/"},
    }