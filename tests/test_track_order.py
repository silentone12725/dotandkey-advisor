"""
tests/test_track_order.py

Tests for backend/playbooks/other.py::track_order() — the deterministic
(non-LLM) order-tracking response.

Deliberately not LLM-streamed: the tracking URL must never be paraphrased,
dropped, or hallucinated. The URL itself travels as a link_chips UI-data
payload (rendered as a real hyperlink button by the widget), not embedded
in the visible chat text — these tests confirm both halves: the text
acknowledges the request, and the link_chips payload carries the exact
URL verbatim regardless of the user's message wording.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from backend.playbooks.other import track_order, CLICKPOST_TRACKING_URL, TRACK_ORDER_LINK_CHIP
from backend.playbooks.base import try_extract_ui_data


async def _collect(agen):
    return [tok async for tok in agen]


def _link_chips(tokens):
    for tok in tokens:
        data = try_extract_ui_data(tok)
        if data is not None:
            return data.get("link_chips")
    return None


class TestTrackOrder:

    @pytest.mark.asyncio
    async def test_response_includes_link_chips_with_exact_url(self):
        tokens = await _collect(track_order("pid", "where is my order", {}))
        chips = _link_chips(tokens)
        assert chips is not None
        assert chips[0]["url"] == CLICKPOST_TRACKING_URL

    @pytest.mark.asyncio
    async def test_link_chip_label_is_track_my_order(self):
        tokens = await _collect(track_order("pid", "where is my order", {}))
        chips = _link_chips(tokens)
        assert chips[0]["label"] == "Track my order"

    @pytest.mark.asyncio
    async def test_url_is_the_real_clickpost_domain(self):
        assert CLICKPOST_TRACKING_URL == "https://dotandkey.clickpost.ai/"

    @pytest.mark.asyncio
    async def test_track_order_link_chip_constant_matches_url(self):
        """Same constant the frontend mirrors — if this ever drifts from
        CLICKPOST_TRACKING_URL, the widget's hardcoded copy would silently
        go stale too."""
        assert TRACK_ORDER_LINK_CHIP["url"] == CLICKPOST_TRACKING_URL

    @pytest.mark.asyncio
    async def test_visible_text_does_not_contain_raw_url(self):
        """The URL belongs in the clickable chip, not duplicated as plain
        text the user would have to copy-paste."""
        tokens = await _collect(track_order("pid", "where is my order", {}))
        visible_text = "".join(
            tok for tok in tokens if try_extract_ui_data(tok) is None
        )
        assert CLICKPOST_TRACKING_URL not in visible_text

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
