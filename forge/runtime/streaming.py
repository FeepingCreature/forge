"""Stream an LLMBackend response into events + final result.

Pulled out of `LiveSession._process_llm_request` so the protocol-level
loop is testable as a plain function (no TaskRunner, no QObject).

Contract:
- Iterates `backend.stream(messages, tools)` and forwards every
  StreamChunk / StreamToolCallDelta to `emit`.
- The terminal StreamFinished event is consumed (not forwarded) and its
  payload is returned as a {"content", "tool_calls"} dict — the same
  shape the previous closure handed back to LiveSession.

If the backend never yields a StreamFinished (would be a backend bug),
both fields come back None.
"""

from typing import Any

from forge.runtime.llm_backend import LLMBackend, StreamFinished


def stream_to_events(
    backend: LLMBackend,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    emit: Any,
) -> dict[str, Any]:
    """Drive `backend.stream(...)` to completion.

    Forwards incremental events via `emit(event)` and returns the final
    {"content", "tool_calls"} dict from the StreamFinished marker.
    """
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None

    for event in backend.stream(messages, tools):
        if isinstance(event, StreamFinished):
            content = event.content
            tool_calls = event.tool_calls
        else:
            # Per the protocol, anything that's not StreamFinished is an
            # incremental event (StreamChunk / StreamToolCallDelta) and gets
            # forwarded to the caller's emit sink.
            emit(event)

    return {"content": content, "tool_calls": tool_calls}
