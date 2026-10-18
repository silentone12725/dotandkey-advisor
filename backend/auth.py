"""
backend/auth.py

Signed profile tokens.

The problem this solves: profile_id is a UUID the browser makes up, sent as
X-Profile-Id and used directly as the Redis key. Nothing bound that id to the
browser that owned it, so naming someone else's id returned their profile,
purchase history and recent conversation — which, via the order-tracking flow,
can contain their order number, phone and email.

The fix: /session/init returns a token that is the profile id plus an HMAC of
it. Every other endpoint takes the token instead of the bare id and recomputes
the HMAC before trusting it. Forging a token for an arbitrary id requires
PROFILE_TOKEN_SECRET, so a caller can only ever address the profile the server
issued to them.

Transport is a header rather than a cookie deliberately. The widget runs on
www.dotandkey.com (extension), on the proxy origin, or on a local demo page,
and calls the backend on a different origin — cross-site, so a cookie would
need SameSite=None; Secure and therefore HTTPS on both ends, which breaks
local development. A header works unchanged in all three. The tradeoff is that
the token is readable by page scripts, so this stops id-guessing/IDOR but is
not by itself XSS-proof; an HttpOnly cookie would add that once the deployment
is HTTPS end to end.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets

_log = logging.getLogger(__name__)

TOKEN_HEADER = "X-Profile-Token"

# Set true only while migrating an already-deployed widget: it lets the server
# keep honouring a bare X-Profile-Id, which is exactly the hole being closed.
ALLOW_UNSIGNED = os.getenv("ALLOW_UNSIGNED_PROFILE_ID", "").lower() in (
    "1", "true", "yes",
)

_secret: bytes | None = None


def _get_secret() -> bytes:
    """Return the signing key.

    With PROFILE_TOKEN_SECRET unset we mint a random per-process key rather
    than falling back to a constant: a hardcoded default would be public in the
    repo and forgeable by anyone. The cost is that tokens stop verifying when
    the process restarts (users transparently get a new profile), so deployments
    must set it.
    """
    global _secret
    if _secret is None:
        configured = os.getenv("PROFILE_TOKEN_SECRET", "")
        if configured:
            _secret = configured.encode()
        else:
            _secret = secrets.token_bytes(32)
            _log.warning(
                "PROFILE_TOKEN_SECRET is unset — using an ephemeral key. "
                "Profile tokens will be invalidated on restart. Set it in .env."
            )
    return _secret


def _sign(profile_id: str) -> str:
    digest = hmac.new(_get_secret(), profile_id.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def issue_token(profile_id: str) -> str:
    """Return a token binding profile_id to this server's secret."""
    pid = base64.urlsafe_b64encode(profile_id.encode()).decode().rstrip("=")
    return f"{pid}.{_sign(profile_id)}"


def verify_token(token: str) -> str | None:
    """Return the profile id a token attests to, or None if it isn't genuine."""
    if not token or "." not in token:
        return None
    pid_b64, sig = token.rsplit(".", 1)
    try:
        padding = "=" * (-len(pid_b64) % 4)
        profile_id = base64.urlsafe_b64decode(pid_b64 + padding).decode()
    except Exception:
        return None
    # compare_digest: a plain == leaks the correct prefix length via timing.
    if not hmac.compare_digest(sig, _sign(profile_id)):
        return None
    return profile_id


def resolve_profile_id(token: str | None, raw_id: str | None) -> str | None:
    """Resolve the caller's profile id from their token.

    raw_id is honoured only under ALLOW_UNSIGNED, for migrating a widget that
    is already deployed. Returns None when the caller cannot prove ownership.
    """
    if token:
        pid = verify_token(token)
        if pid:
            return pid
        _log.warning("rejected profile token with a bad signature")
        return None
    if raw_id and ALLOW_UNSIGNED:
        return raw_id
    return None
