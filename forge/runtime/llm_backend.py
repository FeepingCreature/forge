"""
LLMBackend — seam for the LLM streaming API.

Production code uses `OpenRouterBackend`, which wraps the existing
`LLMClient.chat_stream` and translates its SSE chunks into typed events.

Tests use `ScriptedBackend`, which serves pre-queued responses without
touching the network.

Why a seam:
- Tests can drive the session pipeline end-to-end without mocking
  `requests` or hand-rolling SSE chunks.
- Tool-call assembly (joining streamed argument fragments into complete
  tool calls) lives in *one* place — the backend — instead of being
  duplicated across every test that wants to verify tool execution.

Event protocol:
- The backend yields `StreamChunk` (assistant text) and
  `StreamToolCallDelta` (incremental tool-call updates) during the
  stream, then a final `StreamFinished` carrying the complete content
  and tool_calls. Callers accumulate text themselves if they want
  per-chunk side effects, but `StreamFinished` is authoritative.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol

from forge.runtime.events import StreamChunk, StreamToolCallDelta


@dataclass
class StreamFinished:
    """Final event of a stream — complete assembled response."""

    content: str | None
    tool_calls: list[dict[str, Any]] | None


# Union type for events yielded by LLMBackend.stream(). Plain isinstance
# dispatch on the consumer side. Kept as a comment-style alias because
# Python doesn't have real sum types.
StreamEvent = StreamChunk | StreamToolCallDelta | StreamFinished


class LLMBackend(Protocol):
    """Protocol for LLM streaming backends.

    Implementations:
    - OpenRouterBackend (production)
    - ScriptedBackend (tests)
    """

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> Iterator[StreamEvent]:
        """Yield events for a streaming completion.

        Must yield zero or more StreamChunk/StreamToolCallDelta events,
        then exactly one StreamFinished as the final event. Errors are
        raised as exceptions; the caller's TaskRunner routes them to
        on_error.
        """
        ...


class OpenRouterBackend:
    """Production LLMBackend backed by `forge.llm.client.LLMClient`.

    Accepts a constructed `LLMClient` rather than (api_key, model) so the
    same client can be reused across requests if desired.
    """

    def __init__(self, client: Any) -> None:
        # `Any` rather than LLMClient to avoid a hard import here; the
        # module is light enough that tests don't pay for it, and we
        # don't want runtime/llm_backend.py to depend on llm/client.py.
        self._client = client

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> Iterator[StreamEvent]:
        current_content = ""
        current_tool_calls: list[dict[str, Any]] = []

        for chunk in self._client.chat_stream(messages, tools):
            if "choices" not in chunk or not chunk["choices"]:
                continue
            delta = chunk["choices"][0].get("delta", {})

            if "content" in delta and delta["content"]:
                text = delta["content"]
                current_content += text
                yield StreamChunk(text)

            if "tool_calls" in delta:
                for tc_delta in delta["tool_calls"]:
                    idx = tc_delta.get("index", 0)
                    while len(current_tool_calls) <= idx:
                        current_tool_calls.append(
                            {
                                "id": "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        )
                    if "id" in tc_delta:
                        current_tool_calls[idx]["id"] = tc_delta["id"]
                    if "function" in tc_delta:
                        func = tc_delta["function"]
                        if "name" in func:
                            current_tool_calls[idx]["function"]["name"] = func["name"]
                        if "arguments" in func:
                            current_tool_calls[idx]["function"]["arguments"] += func["arguments"]
                    yield StreamToolCallDelta(idx, current_tool_calls[idx].copy())

        yield StreamFinished(
            content=current_content if current_content else None,
            tool_calls=current_tool_calls if current_tool_calls else None,
        )


# --- ScriptedBackend (test impl) ---


@dataclass
class _ScriptedResponse:
    """One queued response in ScriptedBackend's queue."""

    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    error: str | None = None
    optional: bool = False
    consumed: bool = False
    # For per-chunk streaming simulation. If chunk_size is set, content
    # is split into chunks of that size and emitted as StreamChunk events
    # before the final StreamFinished. Otherwise content is delivered
    # only via StreamFinished.
    chunk_size: int | None = None


class _ScriptDrainedError(AssertionError):
    """Raised when stream() is called but no responses are queued and the
    pipeline isn't allowed to run dry."""


class ScriptedBackend:
    """Test LLMBackend that serves pre-queued responses.

    Usage:
        backend = ScriptedBackend()
        backend.queue_response(content="Hello")
        backend.queue_response(tool_calls=[{...}])
        # ... drive the session ...
        backend.assert_drained()  # raises if non-optional responses unconsumed

    Strictness: each `queue_*` call accepts `optional=True` for responses
    that may or may not be consumed. By default, `assert_drained()` fails
    if any non-optional queued response was not consumed.
    """

    def __init__(self) -> None:
        self._queue: list[_ScriptedResponse] = []
        self._stream_calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]] = []

    # --- Setup API ---

    def queue_response(
        self,
        content: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        optional: bool = False,
        chunk_size: int | None = None,
    ) -> None:
        """Queue a successful response.

        Args:
            content: Assistant text. None means tool-call-only response.
            tool_calls: List of tool call dicts in OpenAI format.
            optional: If True, OK if this response is never consumed.
            chunk_size: If set, content is split into chunks of this size
                and yielded as StreamChunk events before StreamFinished.
                Useful for testing chunk-handling code paths.
        """
        self._queue.append(
            _ScriptedResponse(
                content=content,
                tool_calls=tool_calls,
                optional=optional,
                chunk_size=chunk_size,
            )
        )

    def queue_error(self, message: str, optional: bool = False) -> None:
        """Queue a response that raises an exception when consumed."""
        self._queue.append(_ScriptedResponse(error=message, optional=optional))

    # --- Inspection API ---

    @property
    def stream_calls(self) -> list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]]:
        """All (messages, tools) pairs that stream() has been called with."""
        return self._stream_calls

    def assert_drained(self) -> None:
        """Raise AssertionError if any non-optional response is unconsumed."""
        unconsumed = [
            (i, r) for i, r in enumerate(self._queue) if not r.consumed and not r.optional
        ]
        if unconsumed:
            details = ", ".join(f"#{i}" for i, _ in unconsumed)
            raise AssertionError(
                f"ScriptedBackend has {len(unconsumed)} unconsumed non-optional "
                f"response(s) at positions: {details}"
            )

    # --- Backend protocol ---

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> Iterator[StreamEvent]:
        # Record the call for test inspection. Copy messages because the
        # caller may mutate the list later (we want the snapshot at call
        # time, not at assertion time).
        self._stream_calls.append(([dict(m) for m in messages], tools))

        # Find first unconsumed response.
        response: _ScriptedResponse | None = None
        for r in self._queue:
            if not r.consumed:
                response = r
                break

        if response is None:
            raise _ScriptDrainedError(
                f"ScriptedBackend.stream() called but no responses queued "
                f"(this is call #{len(self._stream_calls)})"
            )

        response.consumed = True

        if response.error is not None:
            raise RuntimeError(response.error)

        # Emit content as chunks if chunk_size set.
        if response.content and response.chunk_size:
            text = response.content
            for i in range(0, len(text), response.chunk_size):
                yield StreamChunk(text[i : i + response.chunk_size])

        # Emit tool call deltas as a single update per call (we don't
        # simulate fragmentation by default — tests that need that can
        # call queue_response with raw events instead, in a future ext).
        if response.tool_calls:
            for idx, tc in enumerate(response.tool_calls):
                yield StreamToolCallDelta(idx, dict(tc))

        yield StreamFinished(content=response.content, tool_calls=response.tool_calls)


# Re-export for convenience.
__all__ = [
    "LLMBackend",
    "OpenRouterBackend",
    "ScriptedBackend",
    "StreamEvent",
    "StreamFinished",
]
