"""Tests for forge.runtime.tool_executor.execute_tool_calls.

This is the biggest extracted helper. It owns:
  - JSON argument parsing (with the doubly-encoded-string unwrap quirk)
  - VFS claim/release brackets
  - Per-tool ToolStarted / ToolFinished emission
  - Chain-stop-on-first-failure semantics

Tests use minimal fakes for tool_manager and session_manager so the
helper's behavior is observable without touching real tools or VFS.
"""

from typing import Any

import pytest

from forge.runtime import ToolFinished, ToolStarted, execute_tool_calls


# --- Fakes ---


class _FakeVFS:
    def __init__(self) -> None:
        self.claims = 0
        self.releases = 0

    def claim_thread(self) -> None:
        self.claims += 1

    def release_thread(self) -> None:
        self.releases += 1


class _FakeSessionManager:
    def __init__(self) -> None:
        self.vfs = _FakeVFS()


class _FakeToolManager:
    """Returns canned results keyed by (tool_name, call index).

    The 'calls' list records every (tool_name, args, session_manager)
    tuple so tests can assert the dispatch order and arguments.
    """

    def __init__(self, results: list[dict[str, Any]]) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, dict[str, Any], Any]] = []

    def execute_tool(
        self, tool_name: str, args: dict[str, Any], session_manager: Any
    ) -> dict[str, Any]:
        self.calls.append((tool_name, args, session_manager))
        if not self._results:
            raise AssertionError(f"unexpected extra tool call: {tool_name}")
        return self._results.pop(0)


def _tool_call(
    name: str, arguments: str = "{}", call_id: str = "call_x"
) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _emit_to(events: list) -> Any:
    return events.append


# --- Happy path ---


class TestExecuteToolCallsBasics:
    def test_single_successful_call_returns_one_result(self) -> None:
        session = _FakeSessionManager()
        tools = _FakeToolManager([{"success": True, "value": 42}])
        events: list = []

        results = execute_tool_calls(
            [_tool_call("noop")], tools, session, _emit_to(events)
        )

        assert len(results) == 1
        assert results[0]["result"] == {"success": True, "value": 42}
        assert results[0]["args"] == {}
        assert results[0]["tool_call"]["function"]["name"] == "noop"

    def test_emits_tool_started_then_tool_finished_per_call(self) -> None:
        session = _FakeSessionManager()
        tools = _FakeToolManager([{"success": True}])
        events: list = []

        execute_tool_calls(
            [_tool_call("noop", call_id="abc")], tools, session, _emit_to(events)
        )

        assert len(events) == 2
        assert isinstance(events[0], ToolStarted)
        assert events[0].tool_name == "noop"
        assert isinstance(events[1], ToolFinished)
        assert events[1].tool_call_id == "abc"
        assert events[1].result == {"success": True}

    def test_multiple_calls_dispatched_in_order(self) -> None:
        session = _FakeSessionManager()
        tools = _FakeToolManager(
            [{"success": True, "n": 1}, {"success": True, "n": 2}]
        )
        events: list = []

        results = execute_tool_calls(
            [_tool_call("a", call_id="1"), _tool_call("b", call_id="2")],
            tools,
            session,
            _emit_to(events),
        )

        assert [r["result"]["n"] for r in results] == [1, 2]
        assert [c[0] for c in tools.calls] == ["a", "b"]

    def test_passes_session_manager_to_tool(self) -> None:
        session = _FakeSessionManager()
        tools = _FakeToolManager([{"success": True}])

        execute_tool_calls([_tool_call("x")], tools, session, _emit_to([]))

        assert tools.calls[0][2] is session


# --- Chain stop on failure ---


class TestExecuteToolCallsChainStop:
    def test_stops_at_first_failure(self) -> None:
        session = _FakeSessionManager()
        tools = _FakeToolManager(
            [
                {"success": True, "n": 1},
                {"success": False, "error": "bad"},
                # Third result must NOT be consumed.
                {"success": True, "n": 3},
            ]
        )
        events: list = []

        results = execute_tool_calls(
            [
                _tool_call("a", call_id="1"),
                _tool_call("b", call_id="2"),
                _tool_call("c", call_id="3"),
            ],
            tools,
            session,
            _emit_to(events),
        )

        # Two results recorded (1 success + 1 failure).
        assert len(results) == 2
        assert results[1]["result"]["error"] == "bad"
        # Tool manager only called twice — third call short-circuited.
        assert len(tools.calls) == 2
        # Started/Finished pairs only for the two attempted calls.
        starts = [e for e in events if isinstance(e, ToolStarted)]
        assert len(starts) == 2

    def test_missing_success_field_treated_as_success(self) -> None:
        # Tools that don't bother setting success default to True so the
        # chain continues. Mirrors the legacy behavior.
        session = _FakeSessionManager()
        tools = _FakeToolManager([{"value": "ok"}, {"success": True}])

        results = execute_tool_calls(
            [_tool_call("a"), _tool_call("b")],
            tools,
            session,
            _emit_to([]),
        )

        assert len(results) == 2


# --- Argument parsing ---


class TestExecuteToolCallsArgumentParsing:
    def test_empty_argument_string_yields_empty_dict(self) -> None:
        session = _FakeSessionManager()
        tools = _FakeToolManager([{"success": True}])

        results = execute_tool_calls(
            [_tool_call("a", arguments="")], tools, session, _emit_to([])
        )

        assert results[0]["args"] == {}

    def test_valid_json_arguments_parsed_into_dict(self) -> None:
        session = _FakeSessionManager()
        tools = _FakeToolManager([{"success": True}])

        results = execute_tool_calls(
            [_tool_call("a", arguments='{"path": "x.py", "n": 2}')],
            tools,
            session,
            _emit_to([]),
        )

        assert results[0]["args"] == {"path": "x.py", "n": 2}

    def test_doubly_encoded_list_value_unwrapped(self) -> None:
        # LLMs occasionally emit a JSON list as a JSON string. The
        # executor unwraps it transparently when the inner string parses
        # as a list/dict.
        session = _FakeSessionManager()
        tools = _FakeToolManager([{"success": True}])

        results = execute_tool_calls(
            [_tool_call("a", arguments='{"items": "[1,2,3]"}')],
            tools,
            session,
            _emit_to([]),
        )

        assert results[0]["args"]["items"] == [1, 2, 3]

    def test_doubly_encoded_dict_value_unwrapped(self) -> None:
        session = _FakeSessionManager()
        tools = _FakeToolManager([{"success": True}])

        results = execute_tool_calls(
            [_tool_call("a", arguments='{"opts": "{\\"k\\": 1}"}')],
            tools,
            session,
            _emit_to([]),
        )

        assert results[0]["args"]["opts"] == {"k": 1}

    def test_string_value_starting_with_bracket_but_invalid_json_kept(self) -> None:
        # Looks like a list but isn't valid JSON — keep the raw string.
        session = _FakeSessionManager()
        tools = _FakeToolManager([{"success": True}])

        results = execute_tool_calls(
            [_tool_call("a", arguments='{"raw": "[not valid"}')],
            tools,
            session,
            _emit_to([]),
        )

        assert results[0]["args"]["raw"] == "[not valid"

    def test_invalid_json_aborts_chain_and_marks_parse_error(self) -> None:
        session = _FakeSessionManager()
        # Tool manager should NOT be called when arguments fail to parse.
        tools = _FakeToolManager([])
        events: list = []

        results = execute_tool_calls(
            [
                _tool_call("a", arguments="not json {{"),
                _tool_call("b"),
            ],
            tools,
            session,
            _emit_to(events),
        )

        assert len(results) == 1
        assert results[0]["parse_error"] is True
        assert results[0]["args"]["INVALID_JSON"] == "not json {{"
        assert results[0]["result"]["success"] is False
        assert "Invalid JSON" in results[0]["result"]["error"]
        # No ToolStarted emitted for the failed-parse call (we never
        # got past argument parsing). Just the synthetic ToolFinished.
        assert not any(isinstance(e, ToolStarted) for e in events)
        assert sum(isinstance(e, ToolFinished) for e in events) == 1
        # Tool manager never called.
        assert tools.calls == []


# --- VFS hand-off ---


class TestExecuteToolCallsVFSHandoff:
    def test_brackets_claim_and_release_around_chain(self) -> None:
        session = _FakeSessionManager()
        tools = _FakeToolManager([{"success": True}, {"success": True}])

        execute_tool_calls(
            [_tool_call("a"), _tool_call("b")],
            tools,
            session,
            _emit_to([]),
        )

        # Single bracket pair around the *entire* chain — not per-tool.
        assert session.vfs.claims == 1
        assert session.vfs.releases == 1

    def test_release_runs_even_when_tool_raises(self) -> None:
        session = _FakeSessionManager()

        class _BoomToolManager:
            def execute_tool(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
                raise RuntimeError("tool went boom")

        with pytest.raises(RuntimeError, match="tool went boom"):
            execute_tool_calls(
                [_tool_call("a")],
                _BoomToolManager(),
                session,
                _emit_to([]),
            )

        assert session.vfs.claims == 1
        assert session.vfs.releases == 1