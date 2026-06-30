"""
tests/test_track_order.py

Tests for backend/playbooks/other.py::track_order() — the deterministic
(non-LLM) order-tracking response.

Deliberately not LLM-streamed: the tracking URL must never be paraphrased,
dropped, or hallucinated, so this playbook just yields fixed text. These
tests confirm the exact URL is present verbatim regardless of the user's
message wording.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from backend.playbooks.other import track_order, CLICKPOST_TRACKING_URL


async def _collect(agen):
    return [tok async for tok in agen]


class TestTrackOrder:

    @pytest.mark.asyncio
    async def test_response_contains_exact_tracking_url(self):
        tokens = await _collect(track_order("pid", "where is my order", {}))
        full_text = "".join(tokens)
        assert CLICKPOST_TRACKING_URL in full_text

    @pytest.mark.asyncio
    async def test_url_is_the_real_clickpost_domain(self):
        assert CLICKPOST_TRACKING_URL == "https://dotandkey.clickpost.ai/"

    @pytest.mark.asyncio
    async def test_response_independent_of_message_wording(self):
        """Deterministic — same response regardless of exact phrasing,
        since there's nothing to interpret (no LLM call)."""
        r1 = "".join(await _collect(track_order("pid", "track my package", {})))
        r2 = "".join(await _collect(track_order("pid", "order status please?", {})))
        assert r1 == r2

    @pytest.mark.asyncio
    async def test_no_llm_call_made(self, monkeypatch):
        """Confirm this never touches llm_adapter — if it did, this test
        would raise/hang since the LLM client isn't configured in tests."""
        def _boom(*a, **kw):
            raise AssertionError("track_order must not call the LLM")
        monkeypatch.setattr("backend.llm_adapter.chat", _boom)
        monkeypatch.setattr("backend.llm_adapter.one_shot", _boom)
        tokens = await _collect(track_order("pid", "where is my order", {}))
        assert tokens   # still produced a response


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
