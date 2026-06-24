"""Event dataclasses for TaskRunner work functions.

Workers used to communicate progress to LiveSession via Qt Signals (one
signal per event type). Now they emit dataclass instances through the
TaskRunner's `emit` callback, and LiveSession dispatches by isinstance.
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class StreamChunk:
    """A piece of streamed assistant text."""

    text: str


@dataclass
class ReasoningChunk:
    """A piece of streamed reasoning/thinking text.

    Emitted when the model returns a `reasoning_content` (or `reasoning`)
    delta — i.e. the chain-of-thought scratchpad some providers expose before
    the actual assistant response. Rendered as a thought bubble in the UI,
    not persisted as part of the assistant message content.
    """

    text: str


@dataclass
class StreamToolCallDelta:
    """An update to a tool call being assembled mid-stream."""

    index: int
    tool_call: dict[str, Any]


@dataclass
class ToolStarted:
    """A tool has started executing."""

    tool_name: str
    tool_args: dict[str, Any]


@dataclass
class ToolFinished:
    """A tool has finished executing."""

    tool_call_id: str
    tool_name: str
    tool_args: dict[str, Any]
    result: dict[str, Any]


@dataclass
class SummaryProgress:
    """Progress update during repository summary generation."""

    current: int
    total: int
    filepath: str


@dataclass
class PromptProgressEvent:
    """Progress update for prompt processing (LLM prefix processing)."""

    processed: int | None
    total: int | None
    cache: int | None
