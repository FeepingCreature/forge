"""Session management for Forge"""

from forge.session.live_session import LiveSession, SessionState
from forge.session.registry import SESSION_REGISTRY, SessionRegistry

# Backwards compatibility
SessionRunner = LiveSession

__all__ = [
    "LiveSession",
    "SessionRunner",  # Alias for backwards compatibility
    "SessionState",
    "SessionRegistry",
    "SESSION_REGISTRY",
]
