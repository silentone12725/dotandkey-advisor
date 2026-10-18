"""
tests/test_auth.py

Guards the profile-token boundary. The hole these cover: profile_id used to be
a client-supplied header used directly as the Redis key, so naming someone
else's id returned their profile, history and recent conversation.
"""

import importlib

import pytest

import backend.auth as auth


@pytest.fixture(autouse=True)
def _fixed_secret(monkeypatch):
    """Pin a known secret and reset the module's cached key between tests."""
    monkeypatch.setenv("PROFILE_TOKEN_SECRET", "unit-test-secret")
    monkeypatch.setattr(auth, "_secret", None)
    monkeypatch.setattr(auth, "ALLOW_UNSIGNED", False)
    yield
    auth._secret = None


# ── round trip ────────────────────────────────────────────────────────────────

def test_issued_token_verifies_back_to_the_same_id():
    token = auth.issue_token("profile-abc")
    assert auth.verify_token(token) == "profile-abc"


def test_token_survives_ids_with_awkward_characters():
    for pid in ("dk-123.456", "a/b+c=d", "ünïcødé-id", "x" * 200):
        assert auth.verify_token(auth.issue_token(pid)) == pid


# ── forgery ───────────────────────────────────────────────────────────────────

def test_token_signed_with_a_different_secret_is_rejected(monkeypatch):
    token = auth.issue_token("victim")
    monkeypatch.setenv("PROFILE_TOKEN_SECRET", "a-different-secret")
    auth._secret = None
    assert auth.verify_token(token) is None


def test_tampering_with_the_id_invalidates_the_signature():
    token = auth.issue_token("victim")
    _, sig = token.rsplit(".", 1)
    import base64
    attacker = base64.urlsafe_b64encode(b"someone-else").decode().rstrip("=")
    assert auth.verify_token(f"{attacker}.{sig}") is None


@pytest.mark.parametrize("bad", [
    "", "no-dot", ".", "abc.", ".abc", "!!!.!!!", "a.b.c",
])
def test_malformed_tokens_are_rejected_without_raising(bad):
    assert auth.verify_token(bad) is None


# ── resolve_profile_id ────────────────────────────────────────────────────────

def test_bare_id_is_refused_when_unsigned_ids_are_disabled():
    assert auth.resolve_profile_id(None, "victim") is None


def test_bare_id_is_honoured_only_under_the_migration_flag(monkeypatch):
    monkeypatch.setattr(auth, "ALLOW_UNSIGNED", True)
    assert auth.resolve_profile_id(None, "victim") == "victim"


def test_valid_token_wins_and_a_mismatched_raw_id_is_ignored():
    token = auth.issue_token("real-owner")
    assert auth.resolve_profile_id(token, "attacker-supplied") == "real-owner"


def test_invalid_token_is_refused_even_with_the_migration_flag_on(monkeypatch):
    """A bad signature is an attack signal, not a reason to fall back."""
    monkeypatch.setattr(auth, "ALLOW_UNSIGNED", True)
    assert auth.resolve_profile_id("forged.token", "victim") is None


# ── endpoint enforcement ──────────────────────────────────────────────────────

def _client(monkeypatch):
    from fastapi.testclient import TestClient
    import backend.app as app_module
    importlib.reload(app_module)
    return TestClient(app_module.app)


@pytest.mark.parametrize("method,path,body", [
    ("post", "/chat", {"message": "hi"}),
    ("post", "/context/product", {"handle": "x", "title": "x"}),
])
def test_protected_endpoints_reject_a_bare_profile_id(monkeypatch, method, path, body):
    monkeypatch.setenv("PROFILE_TOKEN_SECRET", "unit-test-secret")
    monkeypatch.delenv("ALLOW_UNSIGNED_PROFILE_ID", raising=False)
    client = _client(monkeypatch)
    resp = getattr(client, method)(path, json=body,
                                  headers={"X-Profile-Id": "victim-id"})
    assert resp.status_code == 401


def test_explain_no_longer_accepts_profile_id_as_a_query_param(monkeypatch):
    monkeypatch.setenv("PROFILE_TOKEN_SECRET", "unit-test-secret")
    monkeypatch.delenv("ALLOW_UNSIGNED_PROFILE_ID", raising=False)
    client = _client(monkeypatch)
    resp = client.get("/explain/DK_TEST?profile_id=victim-id")
    assert resp.status_code == 401


def test_session_init_issues_a_token_that_opens_other_endpoints(monkeypatch):
    monkeypatch.setenv("PROFILE_TOKEN_SECRET", "unit-test-secret")
    monkeypatch.delenv("ALLOW_UNSIGNED_PROFILE_ID", raising=False)
    client = _client(monkeypatch)

    init = client.post("/session/init", json={"page_context": "homepage"},
                       headers={"X-Profile-Id": "owner-id"})
    assert init.status_code == 200
    token = init.json().get("profile_token")
    assert token and auth.verify_token(token) == "owner-id"

    ok = client.post("/context/product", json={"handle": "x", "title": "x"},
                     headers={"X-Profile-Token": token})
    assert ok.status_code == 200
