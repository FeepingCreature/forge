"""
Session startup helpers - reusable functions for starting/resuming sessions.

This module provides functions that can be used by:
- LiveSession._start_child_session() when spawning children
- Application startup when recovering in-progress sessions
- UI when user clicks on a session in the dropdown
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.config.settings import Settings
    from forge.git_backend.repository import ForgeRepository
    from forge.session.live_session import LiveSession
    from forge.session.manager import SessionManager


def replay_messages_to_prompt_manager(
    messages: list[dict[str, Any]],
    session_manager: "SessionManager",
    replay_compaction: bool = False,
) -> None:
    """
    Replay messages into the prompt manager so the LLM sees them.

    LiveSession.messages is for UI display, but PromptManager builds the actual
    LLM request. When loading a session from disk or attaching to an existing session,
    we need to replay messages so they're in the prompt.

    Args:
        messages: List of message dicts from session.json or session.messages
        session_manager: The SessionManager whose prompt_manager to populate
        replay_compaction: If True, replay compact tool calls to re-apply compaction
    """
    import json

    for msg in messages:
        if msg.get("_ui_only"):
            continue  # Skip UI-only messages (system notifications, etc.)

        role = msg.get("role")
        content = msg.get("content", "")

        if role == "user":
            session_manager.append_user_message(content)
        elif role == "assistant":
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                session_manager.append_tool_call(tool_calls, content)

                # Replay compact tool calls if requested
                if replay_compaction:
                    for tc in tool_calls:
                        func = tc.get("function", {})
                        if func.get("name") == "compact":
                            try:
                                args = json.loads(func.get("arguments", "{}"))
                                from_id = args.get("from_id", "")
                                to_id = args.get("to_id", "")
                                summary = args.get("summary", "")
                                if from_id and to_id:
                                    compacted, _ = session_manager.compact_tool_results(
                                        from_id, to_id, summary
                                    )
                                    print(f"📦 Replayed compaction: {compacted} tool result(s)")
                            except (json.JSONDecodeError, TypeError):
                                pass  # Malformed args, skip
            elif content:
                session_manager.append_assistant_message(content)
        elif role == "tool":
            tool_call_id = msg.get("tool_call_id", "")
            session_manager.append_tool_result(tool_call_id, content)


def load_or_create_session(
    repo: "ForgeRepository",
    branch_name: str,
    settings: "Settings",
) -> "LiveSession | None":
    """
    Load a session from disk and create a LiveSession for it.

    If the session doesn't exist, returns None.
    If the session exists, creates a LiveSession (but doesn't start it).

    The session IS registered with SESSION_REGISTRY.

    Args:
        repo: The git repository
        branch_name: Branch to load session from
        settings: Application settings

    Returns:
        LiveSession if session exists, None otherwise
    """
    from forge.session.registry import SESSION_REGISTRY

    return SESSION_REGISTRY.load(branch_name, repo, settings)


def start_or_resume_session(
    repo: "ForgeRepository",
    branch_name: str,
    settings: "Settings",
    message: str | None = None,
) -> "LiveSession | None":
    """
    Load a session and optionally start it with a message.

    This is the main entry point for starting child sessions.
    It handles:
    - Loading from disk if not in registry
    - Using existing session if in registry
    - Sending the start message

    Args:
        repo: The git repository
        branch_name: Branch to start session on
        settings: Application settings
        message: Optional message to send (starts the session)

    Returns:
        The LiveSession (new or existing), or None if session doesn't exist
    """
    from forge.session.registry import SESSION_REGISTRY

    # Load (or get existing)
    session = SESSION_REGISTRY.load(branch_name, repo, settings)
    if not session:
        return None

    was_newly_loaded = session.state == "idle" and not session.messages

    # Start the session if message provided
    # If newly loaded with messages already, the message is in session.json
    # so we just need to trigger the run without adding another message
    if message:
        if was_newly_loaded and session.messages:
            # Message already in session.json, just trigger the run
            session.send_message("", _trigger_only=True)
        else:
            session.send_message(message)

    return session


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
                # Normalize "running" to "idle" - we're not actually running after restart
                display_state = "idle" if state == "running" else state
                recoverable.append(
                    {
                        "branch_name": branch_name,
                        "state": display_state,
                        "yield_message": session_data.get("yield_message"),
                        "parent_session": session_data.get("parent_session"),
                    }
                )
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            continue

    return recoverable


# Backwards compatibility
load_or_create_runner = load_or_create_session
