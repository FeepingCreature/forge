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
from forge.runtime.llm_backend import (
    LLMBackend,
    OpenRouterBackend,
    ScriptedBackend,
    StreamEvent,
    StreamFinished,
)
from forge.runtime.tasks import (
    CancelToken,
    QtTaskRunner,
    SyncTaskRunner,
    TaskHandle,
    TaskRunner,
)

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
]
