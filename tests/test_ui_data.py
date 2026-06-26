"""
tests/test_ui_data.py

Tests for backend/playbooks/base.py's emit_ui_data/try_extract_ui_data
sentinel mechanism, and backend/chip_options.py's field->chip mapping.

This is the channel playbooks use to hand the widget structured data
(chip suggestions, product cards) without it ever appearing as visible
chat text — see base.py module docstring for the design rationale.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from backend.playbooks.base import emit_ui_data, try_extract_ui_data
from backend.chip_options import chips_for_field, FIELD_CHIP_MAP


class TestUIDataSentinel:

    def test_roundtrip(self):
        data = {"top_picks": [{"sku": "A", "price": 445}], "remaining": []}
        token = emit_ui_data(data)
        assert try_extract_ui_data(token) == data

    def test_encoded_token_is_a_string(self):
        token = emit_ui_data({"x": 1})
        assert isinstance(token, str)

    def test_ordinary_text_is_not_mistaken_for_sentinel(self):
        assert try_extract_ui_data("hello, this is normal chat text") is None

    def test_text_containing_braces_is_not_mistaken_for_sentinel(self):
        """A plain LLM response that happens to mention JSON-like text
        shouldn't be misdetected — only the exact sentinel prefix counts."""
        assert try_extract_ui_data('use {"this": "syntax"} in your code') is None

    def test_empty_string_is_not_a_sentinel(self):
        assert try_extract_ui_data("") is None

    def test_malformed_sentinel_payload_returns_none_not_raise(self):
        """If the JSON after the sentinel is corrupt, fail soft (None),
        never raise — a streaming response must not crash app.py."""
        from backend.playbooks.base import _SENTINEL
        broken = _SENTINEL + "{not valid json"
        assert try_extract_ui_data(broken) is None

    def test_nested_structures_survive_roundtrip(self):
        data = {
            "suggested_chips": {
                "field": "concerns",
                "multi_select": True,
                "options": [
                    {"value": "acne", "label": "Acne / breakouts"},
                    {"value": "dark_spots", "label": "Dark spots"},
                ],
            }
        }
        token = emit_ui_data(data)
        assert try_extract_ui_data(token) == data


class TestChipsForField:

    def test_known_field_returns_options(self):
        result = chips_for_field("skin_types")
        assert result["field"] == "skin_types"
        assert len(result["options"]) > 0

    def test_concerns_is_multi_select(self):
        result = chips_for_field("concerns")
        assert result["multi_select"] is True

    def test_category_is_single_select(self):
        result = chips_for_field("category")
        assert result["multi_select"] is False

    def test_empty_field_returns_empty_options(self):
        """next_field == '' means the profile is complete — no more
        chips to show."""
        result = chips_for_field("")
        assert result["options"] == []

    def test_unknown_field_returns_empty_options_not_raise(self):
        result = chips_for_field("some_field_that_does_not_exist")
        assert result["options"] == []

    def test_no_chip_set_has_free_text_escape_hatch(self):
        """Free-text "something else" chips are intentionally removed —
        the main text input is always available for anything not in the list."""
        for field, (options, _multi) in FIELD_CHIP_MAP.items():
            for opt in options:
                assert not opt.get("free_text"), (
                    f"{field} still has a free_text chip: {opt['label']}"
                )

    def test_all_chip_values_are_lowercase_snake_case(self):
        """Chip values must match the taxonomy vocabulary used elsewhere
        (graph node names, keyword_extract output) — no casing drift."""
        for field, (options, _multi) in FIELD_CHIP_MAP.items():
            for opt in options:
                val = opt["value"]
                assert val == val.lower(), f"{field}: {val} not lowercase"
                assert " " not in val, f"{field}: {val} contains a space"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))