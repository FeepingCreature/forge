"""Tests for forge.runtime.streaming.stream_to_events.

The helper drives an LLMBackend and separates the terminal
StreamFinished from the incremental events. Tests use ScriptedBackend
to drive deterministic streams and a list to capture emitted events.
"""

from forge.runtime import (
    ScriptedBackend,
    StreamChunk,
    StreamToolCallDelta,
    stream_to_events,
)


def _emit_to(events: list) -> object:
    """Return a callable that appends events to the given list."""
    return events.append


class TestStreamToEvents:
    def test_returns_content_from_finished_marker(self) -> None:
        backend = ScriptedBackend()
        backend.queue_response(content="hello")
        events: list = []

        result = stream_to_events(backend, [], None, _emit_to(events))

        assert result == {"content": "hello", "tool_calls": None}

    def test_returns_tool_calls_from_finished_marker(self) -> None:
        backend = ScriptedBackend()
        tc = {"id": "1", "type": "function", "function": {"name": "x", "arguments": "{}"}}
        backend.queue_response(tool_calls=[tc])
        events: list = []

        result = stream_to_events(backend, [], None, _emit_to(events))

        assert result["tool_calls"] == [tc]

    def test_forwards_chunks_to_emit_in_order(self) -> None:
        backend = ScriptedBackend()
        backend.queue_response(content="abcdef", chunk_size=2)
        events: list = []

        stream_to_events(backend, [], None, _emit_to(events))

        chunks = [e for e in events if isinstance(e, StreamChunk)]
        assert [c.text for c in chunks] == ["ab", "cd", "ef"]

    def test_does_not_forward_finished_event(self) -> None:
        # The terminal StreamFinished is consumed, not forwarded — the
        # caller gets it as the return value instead.
        from forge.runtime.llm_backend import StreamFinished

        backend = ScriptedBackend()
        backend.queue_response(content="ok")
        events: list = []

        stream_to_events(backend, [], None, _emit_to(events))

        assert not any(isinstance(e, StreamFinished) for e in events)

    def test_forwards_tool_call_deltas(self) -> None:
        backend = ScriptedBackend()
        tc = {"id": "1", "type": "function", "function": {"name": "x", "arguments": "{}"}}
        backend.queue_response(tool_calls=[tc])
        events: list = []

        stream_to_events(backend, [], None, _emit_to(events))

        deltas = [e for e in events if isinstance(e, StreamToolCallDelta)]
        assert len(deltas) == 1
        assert deltas[0].index == 0

    def test_passes_messages_and_tools_to_backend(self) -> None:
        backend = ScriptedBackend()
        backend.queue_response(content="ok")
        msgs = [{"role": "user", "content": "hi"}]
        tools = [{"name": "edit"}]

        stream_to_events(backend, msgs, tools, _emit_to([]))

        assert backend.stream_calls[0][1] == tools

    def test_empty_stream_returns_none_fields(self) -> None:
        # Backend that yields only StreamFinished(None, None) — no
        # content, no tool calls, no incremental events.
        backend = ScriptedBackend()
        backend.queue_response()  # both content and tool_calls default to None
        events: list = []

        result = stream_to_events(backend, [], None, _emit_to(events))

        assert result == {"content": None, "tool_calls": None}
        assert events == []