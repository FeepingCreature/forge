"""
SessionRegistry - Global singleton managing loaded sessions.

This is a simple index: branch_name → LiveSession | None

Key invariants:
- A session with state WAITING_CHILDREN or RUNNING must have a LiveSession loaded
- Parent/child relationships are owned by the LiveSession, not the registry
- The registry doesn't duplicate state - it just indexes loaded sessions

For display of unloaded sessions (e.g., session dropdown), read session.json directly.
That's a pure display operation, not used for operational logic.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal

if TYPE_CHECKING:
    from forge.config.settings import Settings
    from forge.git_backend.repository import ForgeRepository
    from forge.session.live_session import LiveSession
    from forge.session.manager import SessionManager


class SessionRegistry(QObject):
    """
    Global registry of loaded sessions.

    Singleton - use SESSION_REGISTRY global instance.

    This is just an index: branch_name → LiveSession

    The LiveSession owns all state (parent/child relationships, execution state).
    The registry just tracks which sessions are currently loaded in memory.
    """

    # Signals for UI updates
    session_loaded = Signal(str)  # branch_name
    session_unloaded = Signal(str)  # branch_name
    session_state_changed = Signal(str, str)  # branch_name, new_state

    # Backwards compatible signal names
    session_registered = Signal(str)  # Alias for session_loaded
    session_unregistered = Signal(str)  # Alias for session_unloaded
    registry_initialized = Signal()  # For startup

    def __init__(self) -> None:
        super().__init__()
        self._sessions: dict[str, LiveSession] = {}
        self._repo: ForgeRepository | None = None

        # Connect backwards compat signals to new signals
        self.session_loaded.connect(self.session_registered.emit)
        self.session_unloaded.connect(self.session_unregistered.emit)

    def initialize(self, repo: ForgeRepository) -> None:
        """
        Initialize registry with the repository.

        Call this once on app startup after repo is available.
        This doesn't load any sessions - they're loaded on demand.
        """
        self._repo = repo

    def load(
        self,
        branch_name: str,
        repo: ForgeRepository | None = None,
        settings: Settings | None = None,
        session_manager: SessionManager | None = None,
    ) -> LiveSession | None:
        """
        Load a session from disk, creating a LiveSession.

        If already loaded, returns the existing LiveSession.
        If session doesn't exist (no .forge/session.json), returns None.

        Args:
            branch_name: Branch to load session from
            repo: Repository (uses cached if not provided)
            settings: Application settings (required if not already loaded)
            session_manager: Optional existing SessionManager to use (avoids creating duplicate)

        Returns:
            LiveSession if session exists, None otherwise
        """
        # Return existing if already loaded
        if branch_name in self._sessions:
            return self._sessions[branch_name]

        repo = repo or self._repo
        if not repo or not settings:
            return None

        # Load from disk
        session = self._load_from_disk(branch_name, repo, settings, session_manager)
        if session:
            self._sessions[branch_name] = session
            # Connect to state changes
            session.state_changed.connect(
                lambda state, bn=branch_name: self.session_state_changed.emit(bn, state)
            )
            self.session_loaded.emit(branch_name)

        return session

    def _load_from_disk(
        self,
        branch_name: str,
        repo: ForgeRepository,
        settings: Settings,
        existing_session_manager: SessionManager | None = None,
    ) -> LiveSession | None:
        """Load a session from disk and create a LiveSession.

        Args:
            branch_name: Branch to load from
            repo: Repository
            settings: Application settings
            existing_session_manager: If provided, use this instead of creating a new one.
                                      This avoids duplicate SessionManagers when loading
                                      from UI code that already has a workspace.
        """
        import contextlib

        from forge.constants import SESSION_FILE
        from forge.session.live_session import LiveSession, SessionState
        from forge.session.manager import SessionManager
        from forge.session.startup import replay_messages_to_prompt_manager

        # Try to load session data from branch
        try:
            session_content = repo.get_file_content(SESSION_FILE, branch_name)
            session_data = json.loads(session_content)
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            return None

        messages = session_data.get("messages", [])

        # Use existing SessionManager or create new one
        session_manager = existing_session_manager or SessionManager(repo, branch_name, settings)

        # Restore active files (skip if using existing manager - it may already have them)
        if not existing_session_manager:
            for filepath in session_data.get("active_files", []):
                with contextlib.suppress(Exception):
                    session_manager.add_active_file(filepath)

        # Create LiveSession
        session = LiveSession(session_manager, messages)

        # Replay messages into prompt manager so LLM sees them
        replay_messages_to_prompt_manager(messages, session_manager)

        # Restore parent/child relationships
        if session_data.get("parent_session"):
            session.parent_session = session_data["parent_session"]

        for child in session_data.get("child_sessions", []):
            session.child_sessions.append(child)

        # Restore yield state if session was waiting
        if session_data.get("yield_message"):
            session._yield_message = session_data["yield_message"]

        # Restore pending wait call if session was waiting on children
        if session_data.get("pending_wait_call"):
            session._pending_wait_call = session_data["pending_wait_call"]

        # Restore state from session data.
        # "running" means we crashed mid-run - normalize to "idle" since we're not
        # actually running anymore. The user can restart the conversation.
        stored_state = session_data.get("state", "idle")
        if stored_state == "running":
            stored_state = "idle"  # Crashed mid-run, treat as idle
        if stored_state in (
            SessionState.IDLE,
            SessionState.WAITING_INPUT,
            SessionState.WAITING_CHILDREN,
            SessionState.COMPLETED,
            SessionState.ERROR,
        ):
            session._state = stored_state

        return session

    def unload(self, branch_name: str) -> bool:
        """
        Unload a session if safe to do so.

        Returns True if unloaded (or was not loaded).
        Returns False if session is active and can't be unloaded.
        """
        from forge.session.live_session import SessionState

        session = self._sessions.get(branch_name)
        if not session:
            return True  # Already unloaded

        # Can't unload active sessions
        if session.state not in (SessionState.IDLE, SessionState.COMPLETED, SessionState.ERROR):
            return False

        # Can't unload if UI is attached
        if session.has_attached_ui():
            return False

        del self._sessions[branch_name]
        self.session_unloaded.emit(branch_name)
        return True

    def get(self, branch_name: str) -> LiveSession | None:
        """Get a loaded session, or None if not loaded."""
        return self._sessions.get(branch_name)

    def ensure_loaded(
        self,
        branch_name: str,
        repo: ForgeRepository | None = None,
        settings: Settings | None = None,
    ) -> LiveSession | None:
        """Load session if not loaded, return it."""
        return self.load(branch_name, repo, settings)

    def get_all_loaded(self) -> dict[str, LiveSession]:
        """Get all currently loaded sessions."""
        return dict(self._sessions)

    def is_loaded(self, branch_name: str) -> bool:
        """Check if a session is currently loaded."""
        return branch_name in self._sessions

    def notify_child_updated(self, child_branch: str) -> None:
        """
        Notify that a child session has updated (completed, asked question, etc.)

        Looks up the child's parent and triggers resume if parent is waiting.
        """
        child = self._sessions.get(child_branch)
        if not child or not child.parent_session:
            return

        parent = self._sessions.get(child.parent_session)
        if not parent:
            return

        # Let the parent know a child is ready
        parent.child_ready(child_branch)

    def remove_session(self, branch_name: str) -> None:
        """
        Completely remove a session (branch deleted).
        """
        if branch_name in self._sessions:
            del self._sessions[branch_name]
            self.session_unloaded.emit(branch_name)

    def load_active_sessions_on_startup(
        self,
        repo: ForgeRepository,
        settings: Settings,
    ) -> list[str]:
        """
        Load sessions that need to be active on startup.

        This includes:
        - Sessions in WAITING_CHILDREN state (need to coordinate with children)
        - Children of WAITING_CHILDREN sessions (need to notify parent when done)

        Does NOT auto-run anything - just loads them into memory.

        Returns list of branch names that were loaded.
        """
        from forge.constants import SESSION_BRANCH_PREFIX, SESSION_FILE

        loaded = []
        waiting_parents: list[tuple[str, list[str]]] = []  # (parent_branch, child_branches)

        # First pass: find sessions that need to be loaded
        for branch_name in repo.repo.branches.local:
            if not branch_name.startswith(SESSION_BRANCH_PREFIX):
                continue

            try:
                content = repo.get_file_content(SESSION_FILE, branch_name)
                session_data = json.loads(content)
                state = session_data.get("state", "idle")

                # Load sessions that were waiting on children
                if state == "waiting_children":
                    child_branches = session_data.get("child_sessions", [])
                    waiting_parents.append((branch_name, child_branches))

            except (FileNotFoundError, KeyError, json.JSONDecodeError):
                continue

        # Load waiting parents and their children
        for parent_branch, child_branches in waiting_parents:
            # Load parent
            if self.load(parent_branch, repo, settings):
                loaded.append(parent_branch)

            # Load children so they can notify parent
            for child_branch in child_branches:
                if self.load(child_branch, repo, settings):
                    loaded.append(child_branch)

        return loaded

    # === Display helpers (for UI, not operational logic) ===

    def get_all_session_branches(self, repo: ForgeRepository | None = None) -> list[str]:
        """
        Get all branches that have sessions (loaded or not).

        For UI display purposes - scans git branches for .forge/session.json.
        """
        from forge.constants import SESSION_BRANCH_PREFIX, SESSION_FILE

        repo = repo or self._repo
        if not repo:
            return []

        branches = []
        for branch_name in repo.repo.branches.local:
            if not branch_name.startswith(SESSION_BRANCH_PREFIX):
                continue
            try:
                repo.get_file_content(SESSION_FILE, branch_name)
                branches.append(branch_name)
            except (FileNotFoundError, KeyError):
                continue

        return branches

    def get_session_display_info(
        self, branch_name: str, repo: ForgeRepository | None = None
    ) -> dict[str, Any] | None:
        """
        Get display info for a session (for UI).

        Returns info for display purposes. Uses live state if loaded,
        otherwise reads from session.json.
        """
        from forge.constants import SESSION_FILE

        # If loaded, use live state
        session = self._sessions.get(branch_name)
        if session:
            return {
                "branch_name": branch_name,
                "state": session.state,
                "is_loaded": True,
                "parent_session": session.parent_session,
                "child_sessions": list(session.child_sessions),
                "yield_message": session._yield_message,
                "has_attached_ui": session.has_attached_ui(),
            }

        # Not loaded - read from disk for display
        repo = repo or self._repo
        if not repo:
            return None

        try:
            content = repo.get_file_content(SESSION_FILE, branch_name)
            data = json.loads(content)

            # Normalize state for display
            state = data.get("state", "idle")
            if state == "running":
                state = "idle"  # Crashed mid-run

            return {
                "branch_name": branch_name,
                "state": state,
                "is_loaded": False,
                "parent_session": data.get("parent_session"),
                "child_sessions": data.get("child_sessions", []),
                "yield_message": data.get("yield_message"),
                "has_attached_ui": False,
            }
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            return None

    # === Backwards compatibility ===

    def register_runner(self, branch_name: str, session: LiveSession) -> None:
        """Backwards compatible - registers a session directly."""
        if branch_name not in self._sessions:
            self._sessions[branch_name] = session
            session.state_changed.connect(
                lambda state, bn=branch_name: self.session_state_changed.emit(bn, state)
            )
            self.session_loaded.emit(branch_name)

    def unregister_runner(self, branch_name: str) -> None:
        """Backwards compatible - same as unload but doesn't check safety."""
        if branch_name in self._sessions:
            del self._sessions[branch_name]
            self.session_unloaded.emit(branch_name)

    def get_runner(self, branch_name: str) -> LiveSession | None:
        """Backwards compatible - same as get()."""
        return self.get(branch_name)

    # Old API that needs SessionInfo - provide minimal compatibility
    def register(self, branch_name: str, session: LiveSession) -> None:
        """Backwards compatible."""
        self.register_runner(branch_name, session)

    def unregister(self, branch_name: str) -> None:
        """Backwards compatible."""
        self.unregister_runner(branch_name)

    def get_session_states(self) -> dict[str, dict[str, Any]]:
        """
        Get state info for all loaded sessions (for UI display).

        Returns dict of branch_name -> state info dict.
        """
        result = {}
        for branch_name, session in self._sessions.items():
            result[branch_name] = {
                "state": session.state,
                "is_child": session.parent_session is not None,
                "parent": session.parent_session,
                "has_children": bool(session.child_sessions),
                "children": list(session.child_sessions),
                "yield_message": session._yield_message,
                "is_live": True,
            }
        return result

    def get_children_states(self, parent_branch: str) -> dict[str, str]:
        """
        Get states of all child sessions for a parent.

        Used by wait_session to check if any children are ready.
        Returns dict of child_branch -> state
        """
        parent = self._sessions.get(parent_branch)
        if not parent:
            return {}

        result = {}
        for child_branch in parent.child_sessions:
            child = self._sessions.get(child_branch)
            if child:
                result[child_branch] = child.state
        return result


# Global singleton instance
SESSION_REGISTRY = SessionRegistry()
