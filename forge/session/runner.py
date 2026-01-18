"""
Backwards compatibility shim.

SessionRunner has been renamed to LiveSession. This module re-exports
from live_session.py for backwards compatibility.
"""

# Re-export everything from live_session for backwards compatibility
from forge.session.live_session import (
    ChunkEvent,
    ErrorEvent,
    LiveSession,
    MessageAddedEvent,
    MessagesTruncatedEvent,
    MessageUpdatedEvent,
    SessionEvent,
    SessionState,
    StateChangedEvent,
    ToolCallDeltaEvent,
    ToolFinishedEvent,
    ToolStartedEvent,
    TurnFinishedEvent,
)

# Backwards compatibility alias
SessionRunner = LiveSession

__all__ = [
    "SessionRunner",
    "LiveSession",
    "SessionState",
    "SessionEvent",
    "ChunkEvent",
    "ToolCallDeltaEvent",
    "ToolStartedEvent",
    "ToolFinishedEvent",
    "StateChangedEvent",
    "TurnFinishedEvent",
    "ErrorEvent",
    "MessageAddedEvent",
    "MessageUpdatedEvent",
    "MessagesTruncatedEvent",
]
