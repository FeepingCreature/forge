"""Tests that the edit-tool renderers handle both prefixed and unprefixed keys.

When ToolManager.prefix_tool_args is enabled, streamed `edit` tool-call
arguments arrive with numeric arg-order prefixes on every key (`1_edits`, and
within each entry `1_filepath`/`2_search`/`3_replace` or `1_filepath`/
`2_content`). The rendering layer reads the bare field names, so it must strip
those prefixes. These tests assert the diff/write cards render identically
whether or not the keys are prefixed.
"""

import json

from forge.ui.tool_rendering import (
    _deprefix_dict,
    _parse_partial_edits,
    _strip_key_prefix,
    parse_partial_json,
    render_completed_tool_html,
    render_streaming_tool_html,
)


def _edit_tool_call(arguments: str) -> dict[str, object]:
    return {"function": {"name": "edit", "arguments": arguments}}


class TestStripKeyPrefix:
    def test_strips_leading_numeric_prefix(self):
        assert _strip_key_prefix("1_edits") == "edits"
        assert _strip_key_prefix("2_search") == "search"
        assert _strip_key_prefix("10_replace") == "replace"

    def test_leaves_unprefixed_keys_untouched(self):
        assert _strip_key_prefix("edits") == "edits"
        assert _strip_key_prefix("filepath") == "filepath"

    def test_strips_only_one_prefix(self):
        # A field that legitimately starts with digits+underscore after the
        # arg prefix keeps its inner prefix (we only remove the outer one).
        assert _strip_key_prefix("1_2_search") == "2_search"

    def test_does_not_strip_bare_digits(self):
        # No underscore -> not an arg prefix.
        assert _strip_key_prefix("123abc") == "123abc"


class TestDeprefixDict:
    def test_deprefixes_all_keys(self):
        d = {"1_filepath": "a.py", "2_search": "x", "3_replace": "y"}
        assert _deprefix_dict(d) == {"filepath": "a.py", "search": "x", "replace": "y"}

    def test_passes_unprefixed_through(self):
        d = {"filepath": "a.py", "content": "body"}
        assert _deprefix_dict(d) == {"filepath": "a.py", "content": "body"}


class TestParsePartialJsonPrefixed:
    def test_complete_json_with_prefixed_keys(self):
        raw = json.dumps({"1_filepath": "a.py", "2_search": "old", "3_replace": "new"})
        parsed = parse_partial_json(raw)
        assert parsed == {"filepath": "a.py", "search": "old", "replace": "new"}

    def test_partial_json_with_prefixed_keys(self):
        # Incomplete JSON (no closing quote/brace) with prefixed field names.
        raw = '{"1_filepath": "a.py", "2_search": "hel'
        parsed = parse_partial_json(raw)
        assert parsed.get("filepath") == "a.py"
        assert parsed.get("search") == "hel"

    def test_partial_json_unprefixed_still_works(self):
        raw = '{"filepath": "a.py", "search": "hel'
        parsed = parse_partial_json(raw)
        assert parsed.get("filepath") == "a.py"
        assert parsed.get("search") == "hel"


class TestParsePartialEdits:
    def test_complete_prefixed_edits_array(self):
        raw = json.dumps(
            {
                "1_edits": [
                    {"1_filepath": "a.py", "2_search": "old", "3_replace": "new"},
                    {"1_filepath": "b.py", "2_content": "whole"},
                ]
            }
        )
        entries = _parse_partial_edits(raw)
        assert entries == [
            {"filepath": "a.py", "search": "old", "replace": "new"},
            {"filepath": "b.py", "content": "whole"},
        ]

    def test_complete_unprefixed_edits_array(self):
        raw = json.dumps(
            {
                "edits": [
                    {"filepath": "a.py", "search": "old", "replace": "new"},
                ]
            }
        )
        entries = _parse_partial_edits(raw)
        assert entries == [{"filepath": "a.py", "search": "old", "replace": "new"}]

    def test_prefixed_and_unprefixed_render_identically(self):
        prefixed = json.dumps(
            {"1_edits": [{"1_filepath": "a.py", "2_search": "old", "3_replace": "new"}]}
        )
        unprefixed = json.dumps(
            {"edits": [{"filepath": "a.py", "search": "old", "replace": "new"}]}
        )
        assert _parse_partial_edits(prefixed) == _parse_partial_edits(unprefixed)

    def test_partial_streaming_array_with_prefixed_keys(self):
        # A still-streaming edits array: first entry complete, second forming.
        raw = (
            '{"1_edits": ['
            '{"1_filepath": "a.py", "2_search": "old", "3_replace": "new"}, '
            '{"1_filepath": "b.py", "2_sea'
        )
        entries = _parse_partial_edits(raw)
        assert entries[0] == {"filepath": "a.py", "search": "old", "replace": "new"}
        # Trailing partial entry: filepath extracted, prefix stripped.
        assert entries[1].get("filepath") == "b.py"


class TestStreamingRenderParity:
    def test_streaming_edit_prefixed_matches_unprefixed(self):
        prefixed = _edit_tool_call(
            json.dumps({"1_edits": [{"1_filepath": "a.py", "2_search": "old", "3_replace": "new"}]})
        )
        unprefixed = _edit_tool_call(
            json.dumps({"edits": [{"filepath": "a.py", "search": "old", "replace": "new"}]})
        )
        assert render_streaming_tool_html(prefixed) == render_streaming_tool_html(unprefixed)

    def test_completed_edit_prefixed_matches_unprefixed(self):
        prefixed_args = {"1_edits": [{"1_filepath": "a.py", "2_content": "body"}]}
        unprefixed_args = {"edits": [{"filepath": "a.py", "content": "body"}]}
        assert render_completed_tool_html("edit", prefixed_args) == render_completed_tool_html(
            "edit", unprefixed_args
        )

    def test_completed_edit_prefixed_shows_filepath(self):
        html = render_completed_tool_html(
            "edit", {"1_edits": [{"1_filepath": "a.py", "2_search": "old", "3_replace": "new"}]}
        )
        assert html is not None
        assert "a.py" in html
        # The replacement text should appear in the rendered diff.
        assert "new" in html
