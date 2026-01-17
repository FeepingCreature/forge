"""
SessionRegistry - Global singleton managing all active SessionRunners.

This is the coordination layer that:
- Tracks all running sessions by branch name
- Allows spawn/resume/wait tools to interact with sessions
- Notifies parent sessions when children complete
- Provides session state for the dropdown UI
"""

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal

if TYPE_CHECKING:
    from forge.session.runner import SessionRunner


class SessionRegistry(QObject):
    """
    Global registry of all active SessionRunners.

    Singleton - use SESSION_REGISTRY global instance.
    """

    # Signals for UI updates
    session_registered = Signal(str)  # branch_name
    session_unregistered = Signal(str)  # branch_name
    session_state_changed = Signal(str, str)  # branch_name, new_state

    def __init__(self) -> None:
        super().__init__()
        self._runners: dict[str, SessionRunner] = {}

    def register(self, branch_name: str, runner: "SessionRunner") -> None:
        """
        Register a SessionRunner for a branch.

        Called when a session is created or loaded.
        """
        self._runners[branch_name] = runner

        # Connect to state changes to re-emit for UI
        runner.state_changed.connect(
            lambda state, bn=branch_name: self.session_state_changed.emit(bn, state)
        )

        self.session_registered.emit(branch_name)

    def unregister(self, branch_name: str) -> None:
        """
        Unregister a SessionRunner.

        Called when a session is closed/deleted.
        """
        if branch_name in self._runners:
            del self._runners[branch_name]
            self.session_unregistered.emit(branch_name)

    def get(self, branch_name: str) -> "SessionRunner | None":
        """Get the SessionRunner for a branch, or None if not registered."""
        return self._runners.get(branch_name)

    def get_all(self) -> dict[str, "SessionRunner"]:
        """Get all registered runners."""
        return dict(self._runners)

    def get_session_states(self) -> dict[str, dict[str, Any]]:
        """
        Get state info for all sessions (for dropdown UI).

        Returns dict of branch_name -> {state, is_child, parent, has_children}
        """
        states = {}
        for branch_name, runner in self._runners.items():
            states[branch_name] = {
                "state": runner.state,
                "is_child": runner._parent_session is not None,
                "parent": runner._parent_session,
                "has_children": bool(runner._child_sessions),
                "children": list(runner._child_sessions),
                "yield_message": runner._yield_message,
            }
        return states

    def get_children_states(self, parent_branch: str) -> dict[str, str]:
        """
        Get states of all child sessions for a parent.

        Used by wait_session to check if any children are ready.
        Returns dict of child_branch -> state
        """
        parent = self._runners.get(parent_branch)
        if not parent:
            return {}

        result = {}
        for child_branch in parent._child_sessions:
            child = self._runners.get(child_branch)
            if child:
                result[child_branch] = child.state
        return result

    def notify_parent(self, child_branch: str) -> None:
        """
        Notify parent session that a child has updated.

        Called when a child session changes state (completes, asks question, etc.)
        If parent is waiting on children, this may resume it.
        """
        child = self._runners.get(child_branch)
        if not child:
            print(f"⚠️ notify_parent: child {child_branch} not in registry")
            return
        if not child._parent_session:
            print(f"⚠️ notify_parent: child {child_branch} has no _parent_session set")
            return

        parent = self._runners.get(child._parent_session)
        if not parent:
            print(f"⚠️ notify_parent: parent {child._parent_session} not in registry")
            return
        
        print(f"📣 notify_parent: child={child_branch}, parent={child._parent_session}, parent.state={parent.state}")

        from forge.session.runner import SessionState

        # If parent is waiting on children, check if any child is ready
        if parent.state == SessionState.WAITING_CHILDREN:
            # A child is "ready" if it's completed or waiting for input
            child_states = self.get_children_states(child._parent_session)
            ready_states = {SessionState.COMPLETED, SessionState.WAITING_INPUT, SessionState.IDLE}
            
            print(f"📣 Parent is WAITING_CHILDREN, child_states={child_states}")

            for branch, state in child_states.items():
                if state in ready_states:
                    print(f"📣 Child {branch} is ready (state={state}), resuming parent")
                    # A child is ready - actually resume the parent by re-executing
                    # the pending wait_session call
                    if parent._pending_wait_call:
                        print(f"📣 Parent has _pending_wait_call, calling send_message('')")
                        # Use send_message with empty string to trigger _resume_pending_wait
                        parent.send_message("")
                    else:
                        print(f"⚠️ Parent has no _pending_wait_call! Setting to IDLE as fallback")
                        # Fallback: just set to IDLE (shouldn't happen)
                        parent.state = SessionState.IDLE
                    break
        else:
            print(f"📣 Parent state is {parent.state}, not WAITING_CHILDREN - not resuming")


# Global singleton instance
SESSION_REGISTRY = SessionRegistry()
