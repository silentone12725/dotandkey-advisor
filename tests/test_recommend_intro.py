import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from backend.playbooks.recommend import _first_sentence_only


async def _gen(chunks):
    for c in chunks:
        yield c


async def _run(chunks):
    return "".join([t async for t in _first_sentence_only(_gen(chunks))])


@pytest.mark.asyncio
async def test_stops_after_first_sentence():
    out = await _run(["Here are some ", "picks for dry skin.", " 1. Cica Gel 2. Face Wash"])
    assert out == "Here are some picks for dry skin."


@pytest.mark.asyncio
async def test_stops_at_newline_before_a_list():
    out = await _run(["Suggestions for dry skin", "\n1. Cica Gel\n2. Wash"])
    assert out == "Suggestions for dry skin"


@pytest.mark.asyncio
async def test_decimal_is_not_a_sentence_end():
    out = await _run(["Contains 2.5% niacinamide, nice."])
    assert out == "Contains 2.5% niacinamide, nice."


@pytest.mark.asyncio
async def test_caps_runaway_text_without_punctuation():
    out = await _run(["word " * 100])
    assert len(out) <= 225
