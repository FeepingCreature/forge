"""
Runtime infrastructure: seams between the session core and the
underlying execution environment (threads, LLM client).

These boundaries exist so tests can substitute synchronous, scripted
implementations without touching production code paths.
"""

from forge.runtime.events import (
    StreamChunk,
    StreamToolCallDelta,
    SummaryProgress,
    ToolFinished,
    ToolStarted,
)
from forge.runtime.inline_executor import run_inline_commands
from forge.runtime.llm_backend import (
    LLMBackend,
    OpenRouterBackend,
    ScriptedBackend,
    StreamEvent,
    StreamFinished,
)
from forge.runtime.streaming import stream_to_events
from forge.runtime.tasks import (
    CancelToken,
    QtTaskRunner,
    SyncTaskRunner,
    TaskHandle,
    TaskRunner,
)
from forge.runtime.tool_executor import execute_tool_calls

__all__ = [
    "CancelToken",
    "LLMBackend",
    "OpenRouterBackend",
    "QtTaskRunner",
    "ScriptedBackend",
    "StreamChunk",
    "StreamEvent",
    "StreamFinished",
    "StreamToolCallDelta",
    "SummaryProgress",
    "SyncTaskRunner",
    "TaskHandle",
    "TaskRunner",
    "ToolFinished",
    "ToolStarted",
    "execute_tool_calls",
    "run_inline_commands",
    "stream_to_events",
]
