"""
Session startup helpers - reusable functions for starting/resuming sessions.

This module provides functions that can be used by:
- SessionRunner._start_child_session() when spawning children
- Application startup when recovering in-progress sessions
- UI when user clicks on a session in the dropdown
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.config.settings import Settings
    from forge.git_backend.repository import ForgeRepository
    from forge.session.runner import SessionRunner


def load_or_create_runner(
    repo: "ForgeRepository",
    branch_name: str,
    settings: "Settings",
) -> "SessionRunner | None":
    """
    Load a session from disk and create a SessionRunner for it.

    If the session doesn't exist, returns None.
    If the session exists, creates a SessionRunner (but doesn't start it).

    The runner is NOT registered with SESSION_REGISTRY - caller should do that.

    Args:
        repo: The git repository
        branch_name: Branch to load session from
        settings: Application settings

    Returns:
        SessionRunner if session exists, None otherwise
    """
    import contextlib
    import json

    from forge.constants import SESSION_FILE
    from forge.session.manager import SessionManager
    from forge.session.runner import SessionRunner

    # Try to load session data from branch
    try:
        session_content = repo.get_file_content(SESSION_FILE, branch_name)
        session_data = json.loads(session_content)
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return None

    messages = session_data.get("messages", [])

    # Create SessionManager for this branch
    session_manager = SessionManager(repo, branch_name, settings)

    # Restore active files
    for filepath in session_data.get("active_files", []):
        with contextlib.suppress(Exception):
            session_manager.add_active_file(filepath)

    # Create runner
    runner = SessionRunner(session_manager, messages)

    # Restore parent/child relationships from session data
    if session_data.get("parent_session"):
        runner.set_parent(session_data["parent_session"])

    for child in session_data.get("child_sessions", []):
        runner.spawn_child(child)

    # Restore yield state if session was waiting
    if session_data.get("yield_message"):
        runner._yield_message = session_data["yield_message"]

    # Restore pending wait call if session was waiting on children
    if session_data.get("pending_wait_call"):
        runner._pending_wait_call = session_data["pending_wait_call"]

    # Restore state (but don't change from IDLE if it was running -
    # that means we crashed mid-run and should let user decide)
    stored_state = session_data.get("state", "idle")
    if stored_state in ("waiting_input", "waiting_children", "completed"):
        runner._state = stored_state

    return runner


def start_or_resume_session(
    repo: "ForgeRepository",
    branch_name: str,
    settings: "Settings",
    message: str | None = None,
) -> "SessionRunner | None":
    """
    Load a session and optionally start it with a message.

    This is the main entry point for starting child sessions.
    It handles:
    - Loading from disk if not in registry
    - Using existing runner if in registry
    - Registering new runners
    - Sending the start message

    Args:
        repo: The git repository
        branch_name: Branch to start session on
        settings: Application settings
        message: Optional message to send (starts the session)

    Returns:
        The SessionRunner (new or existing), or None if session doesn't exist
    """
    from forge.session.registry import SESSION_REGISTRY

    # Check if already in registry
    runner = SESSION_REGISTRY.get(branch_name)
    was_newly_loaded = False

    if not runner:
        # Load from disk
        runner = load_or_create_runner(repo, branch_name, settings)
        if not runner:
            return None

        # Register it
        SESSION_REGISTRY.register(branch_name, runner)
        was_newly_loaded = True

    # Start the runner if message provided
    # If newly loaded, the message is already in session.json (from resume_session)
    # so we just need to trigger the run without adding another message
    # If already in registry, send the actual message
    if message:
        if was_newly_loaded:
            # Message already in session.json, just trigger the run
            runner.send_message("", _trigger_only=True)
        else:
            runner.send_message(message)

    return runner


def get_recoverable_sessions(repo: "ForgeRepository") -> list[dict[str, Any]]:
    """
    Find sessions that may need recovery (were running when app closed).

    Returns list of dicts with:
    - branch_name: str
    - state: str (the stored state)
    - yield_message: str | None
    - parent_session: str | None

    Used by application startup to show recovery options.
    """
    import json

    from forge.constants import SESSION_BRANCH_PREFIX, SESSION_FILE

    recoverable = []

    for branch_name in repo.repo.branches.local:
        # Only check session branches
        if not branch_name.startswith(SESSION_BRANCH_PREFIX):
            continue

        try:
            content = repo.get_file_content(SESSION_FILE, branch_name)
            session_data = json.loads(content)

            state = session_data.get("state", "idle")

            # Sessions that were actively running or waiting are recoverable
            if state in ("running", "waiting_children", "waiting_input"):
                recoverable.append(
                    {
                        "branch_name": branch_name,
                        "state": state,
                        "yield_message": session_data.get("yield_message"),
                        "parent_session": session_data.get("parent_session"),
                    }
                )
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            continue

    return recoverable
