"""
Tests for the `say` tool.

`say` lets the model narrate progress between tool calls without ending the
turn (the provider ends the turn after the final tool call's results return,
so prose written *after* tool calls is lost). It is an API tool only — there
is no inline `<say>` form — and its execute() is a deliberate no-op: the
narration lives entirely in the tool-call arguments and is surfaced by the UI.

These tests pin three things:
  1. The schema shape (name, required `message`, no `invocation=inline`).
  2. execute() is a pure no-op returning {"success": True}.
  3. Rendering: `say` renders its `message` as prose (not a tool card),
     while `done` renders to nothing — and neither falls through to the
     generic tool-card branch.
"""

from unittest.mock import MagicMock

from forge.tools.builtin import say
from forge.ui.tool_rendering import (
    parse_partial_json,
    render_completed_tool_html,
    render_streaming_tool_html,
)


class TestSaySchema:
    """The schema the model sees."""

    def test_name_is_say(self):
        schema = say.get_schema()
        assert schema["function"]["name"] == "say"

    def test_message_is_required(self):
        params = say.get_schema()["function"]["parameters"]
        assert "message" in params["properties"]
        assert params["required"] == ["message"]

    def test_is_api_tool_not_inline(self):
        """`say` is an API tool only — no inline invocation/syntax."""
        schema = say.get_schema()
        # Default invocation is "api"; must never be "inline".
        assert schema.get("invocation", "api") != "inline"
        assert "inline_syntax" not in schema


class TestSayExecute:
    """execute() is a side-effect-free no-op."""

    def test_execute_returns_success(self):
        vfs = MagicMock()
        result = say.execute(vfs, {"message": "hello"})
        assert result == {"success": True}

    def test_execute_does_not_touch_vfs(self):
        vfs = MagicMock()
        say.execute(vfs, {"message": "anything"})
        vfs.write_file.assert_not_called()
        vfs.read_file.assert_not_called()

    def test_execute_ignores_missing_message(self):
        """Even with no message, execute() is still a clean no-op."""
        vfs = MagicMock()
        assert say.execute(vfs, {}) == {"success": True}


class TestSayRendering:
    """`say` renders its message as prose; `done` renders to nothing."""

    def _say_call(self, message: str) -> dict:
        return {"function": {"name": "say", "arguments": f'{{"message": "{message}"}}'}}

    def test_completed_say_renders_message_as_prose(self):
        html = render_completed_tool_html("say", {"message": "Editing the parser"})
        assert html is not None
        assert "Editing the parser" in html
        # Prose, not a tool card.
        assert "tool-card" not in html

    def test_streaming_say_renders_message_as_prose(self):
        html = render_streaming_tool_html(self._say_call("Running the tests"))
        assert html is not None
        assert "Running the tests" in html
        assert "tool-card" not in html

    def test_empty_say_renders_nothing_but_is_handled(self):
        """An empty message renders to "" — handled, NOT None (which would fall
        through to the generic tool-card branch)."""
        assert render_completed_tool_html("say", {"message": ""}) == ""
        assert render_completed_tool_html("say", {}) == ""

    def test_done_renders_empty_string_not_none(self):
        """`done` is invisible: it must return "" (handled), never None."""
        assert render_completed_tool_html("done", {}) == ""
        assert render_streaming_tool_html({"function": {"name": "done", "arguments": ""}}) == ""

    def test_partial_json_extracts_message(self):
        """Regression: while a say call streams, parse_partial_json must extract
        `message` so it renders as prose instead of falling through to a
        generic '🔧 say' tool card."""
        # Complete JSON.
        assert parse_partial_json('{"message": "hello"}')["message"] == "hello"
        # Incomplete (still streaming) JSON — quote not yet closed.
        assert parse_partial_json('{"message": "hel')["message"] == "hel"

    def test_streaming_partial_say_renders_prose_not_card(self):
        """The exact bug reported: a mid-stream say must not render a tool card."""
        partial = {"function": {"name": "say", "arguments": '{"message": "Committ'}}
        html = render_streaming_tool_html(partial)
        assert html is not None
        assert "Committ" in html
        assert "tool-card" not in html
