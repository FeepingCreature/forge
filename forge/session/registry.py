"""
SessionRegistry - Global singleton managing all session state.

This is the single source of truth for session information:
- Scans all branches on init to find sessions (those with .forge/session.json)
- Stores lightweight SessionInfo for each session
- Attaches SessionRunner when a session is actually running
- Provides session state for tools and UI

Tools should ONLY query the registry, never read session.json directly.
"""

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal

if TYPE_CHECKING:
    from forge.git_backend.repository import ForgeRepository
    from forge.session.runner import SessionRunner


@dataclass
class SessionInfo:
    """Lightweight metadata for a session branch."""

    branch_name: str
    state: str = "idle"  # Normalized - "running" -> "idle" on load
    parent_session: str | None = None
    child_sessions: list[str] = field(default_factory=list)
    yield_message: str | None = None
    runner: "SessionRunner | None" = None  # None if not actively loaded

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for UI/tools."""
        return {
            "state": self.runner.state if self.runner else self.state,
            "is_child": self.parent_session is not None,
            "parent": self.parent_session,
            "has_children": bool(self.child_sessions),
            "children": list(self.child_sessions),
            "yield_message": self.runner._yield_message if self.runner else self.yield_message,
            "is_live": self.runner is not None,
        }


class SessionRegistry(QObject):
    """
    Global registry of all sessions.

    Singleton - use SESSION_REGISTRY global instance.
    Call initialize(repo) on startup to scan branches.
    """

    # Signals for UI updates
    session_registered = Signal(str)  # branch_name
    session_unregistered = Signal(str)  # branch_name
    session_state_changed = Signal(str, str)  # branch_name, new_state
    registry_initialized = Signal()  # Emitted after scan completes

    def __init__(self) -> None:
        super().__init__()
        self._sessions: dict[str, SessionInfo] = {}
        self._repo: ForgeRepository | None = None

    def initialize(self, repo: "ForgeRepository") -> None:
        """
        Initialize registry by scanning all branches for sessions.

        Call this once on app startup after repo is available.
        """
        self._repo = repo
        self._scan_branches()
        self.registry_initialized.emit()

    def _scan_branches(self) -> None:
        """Scan all branches and load session metadata."""
        if not self._repo:
            return

        self._sessions.clear()

        for branch_name in self._repo.repo.branches.local:
            info = self._load_session_info(branch_name)
            if info:
                self._sessions[branch_name] = info

    def _load_session_info(self, branch_name: str) -> SessionInfo | None:
        """Load session info from a branch's session.json, if it exists."""
        if not self._repo:
            return None

        try:
            content = self._repo.get_file_content(".forge/session.json", branch_name)
            data = json.loads(content)

            # Normalize state - "running" means crashed mid-run, treat as idle
            state = data.get("state", "idle")
            if state == "running":
                state = "idle"

            return SessionInfo(
                branch_name=branch_name,
                state=state,
                parent_session=data.get("parent_session"),
                child_sessions=data.get("child_sessions", []),
                yield_message=data.get("yield_message"),
            )
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            return None

    def refresh_branch(self, branch_name: str) -> None:
        """
        Refresh session info for a specific branch.

        Call after commits that might change session.json.
        """
        info = self._load_session_info(branch_name)
        if info:
            # Preserve runner if one exists
            existing = self._sessions.get(branch_name)
            if existing and existing.runner:
                info.runner = existing.runner
            self._sessions[branch_name] = info
            self.session_registered.emit(branch_name)
        elif branch_name in self._sessions:
            # Session was deleted
            del self._sessions[branch_name]
            self.session_unregistered.emit(branch_name)

    def register_runner(self, branch_name: str, runner: "SessionRunner") -> None:
        """
        Attach a SessionRunner to a session.

        Called when a session is started/loaded.
        """
        if branch_name not in self._sessions:
            # Session not yet known - create info for it
            self._sessions[branch_name] = SessionInfo(branch_name=branch_name)

        self._sessions[branch_name].runner = runner

        # Connect to state changes to re-emit for UI
        runner.state_changed.connect(
            lambda state, bn=branch_name: self.session_state_changed.emit(bn, state)
        )

        self.session_registered.emit(branch_name)

    def unregister_runner(self, branch_name: str) -> None:
        """
        Detach a SessionRunner from a session.

        Called when a session tab is closed. Session info remains.
        """
        if branch_name in self._sessions:
            self._sessions[branch_name].runner = None
            # Don't emit unregistered - session still exists, just not live

    def remove_session(self, branch_name: str) -> None:
        """
        Completely remove a session (branch deleted).
        """
        if branch_name in self._sessions:
            del self._sessions[branch_name]
            self.session_unregistered.emit(branch_name)

    def get(self, branch_name: str) -> SessionInfo | None:
        """Get session info for a branch, or None if not a session."""
        return self._sessions.get(branch_name)

    def get_runner(self, branch_name: str) -> "SessionRunner | None":
        """Get the SessionRunner for a branch, or None if not live."""
        info = self._sessions.get(branch_name)
        return info.runner if info else None

    def get_all(self) -> dict[str, SessionInfo]:
        """Get all known sessions."""
        return dict(self._sessions)

    def get_session_states(self) -> dict[str, dict[str, Any]]:
        """
        Get state info for all sessions (for dropdown UI).

        Returns dict of branch_name -> {state, is_child, parent, has_children, ...}
        """
        return {name: info.to_dict() for name, info in self._sessions.items()}

    def get_children_states(self, parent_branch: str) -> dict[str, str]:
        """
        Get states of all child sessions for a parent.

        Used by wait_session to check if any children are ready.
        Returns dict of child_branch -> state
        """
        parent_info = self._sessions.get(parent_branch)
        if not parent_info:
            return {}

        result = {}
        for child_branch in parent_info.child_sessions:
            child_info = self._sessions.get(child_branch)
            if child_info:
                # Use live state if runner exists, otherwise stored state
                if child_info.runner:
                    result[child_branch] = child_info.runner.state
                else:
                    result[child_branch] = child_info.state
        return result

    def notify_parent(self, child_branch: str) -> None:
        """
        Notify parent session that a child has updated.

        Called when a child session changes state (completes, asks question, etc.)
        If parent is waiting on children, this may resume it.
        """
        child_info = self._sessions.get(child_branch)
        if not child_info or not child_info.parent_session:
            return

        parent_info = self._sessions.get(child_info.parent_session)
        if not parent_info or not parent_info.runner:
            return

        parent = parent_info.runner

        from forge.session.runner import SessionState

        # If parent is waiting on children, check if any child is ready
        if parent.state == SessionState.WAITING_CHILDREN:
            # A child is "ready" if it's completed or waiting for input
            child_states = self.get_children_states(child_info.parent_session)
            ready_states = {SessionState.COMPLETED, SessionState.WAITING_INPUT, SessionState.IDLE}

            for _branch, state in child_states.items():
                if state in ready_states:
                    # A child is ready - trigger resume via QTimer to avoid reentrancy
                    if parent._pending_wait_call:
                        from PySide6.QtCore import QTimer

                        QTimer.singleShot(0, parent._do_resume_from_wait)
                    else:
                        parent.state = SessionState.IDLE
                    break

    # === Backwards compatibility ===
    # These methods maintain the old API while we migrate callers

    def register(self, branch_name: str, runner: "SessionRunner") -> None:
        """Backwards compatible - use register_runner instead."""
        self.register_runner(branch_name, runner)

    def unregister(self, branch_name: str) -> None:
        """Backwards compatible - use unregister_runner instead."""
        self.unregister_runner(branch_name)


# Global singleton instance
SESSION_REGISTRY = SessionRegistry()
