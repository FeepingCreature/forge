"""
Side effects that tools can declare in their return values.

Tools are pure functions that operate on VFS, but some have session-level
side effects that the orchestrator needs to handle. Rather than special-casing
tool names, tools declare their side effects explicitly.
"""

from enum import Enum


class SideEffect(str, Enum):
    """Side effects a tool can declare.

    Inherits from str so it's JSON-serializable automatically.
    """

    # Marks that a commit happened mid-turn, affecting end-of-turn commit type
    MID_TURN_COMMIT = "mid_turn_commit"

    # Marks that files were modified via VFS writeback
    # Result must include "modified_files": [list of filepaths]
    FILES_MODIFIED = "files_modified"

    # Marks that new files were created (for summary generation)
    # Result must include "new_files": [list of filepaths]
    NEW_FILES_CREATED = "new_files_created"

    # Marks that the tool has displayable output for the UI
    # Result must include "display_output": str (the content to show)
    HAS_DISPLAY_OUTPUT = "has_display_output"

    # Marks that this tool result is ephemeral - only available within the current turn.
    # At the start of the NEXT turn (next user message), it's replaced with a placeholder.
    # This allows the AI to use the result across multiple tool calls within a single turn,
    # but saves context space in subsequent turns.
    # Use for tools that return large results used for immediate decision-making
    # (e.g., grep_context snippets that help decide what files to load).
    EPHEMERAL_RESULT = "ephemeral_result"

    # Marks that the assistant is voluntarily ending the current turn.
    # Only consulted when `llm.require_done_tag` is enabled: in that mode, a
    # turn does NOT end automatically after a text-only response; the AI must
    # emit a tool with this side effect (the built-in `done` tool, invoked
    # inline) to hand control back to the user. Without it, a reminder is
    # injected and the LLM is called again.
    END_TURN = "end_turn"

    # Marks that the session should be terminated: the current turn finishes,
    # the session transitions to COMPLETED, and no further user input is
    # accepted. Declared by the built-in `terminate` tool. Unlike END_TURN
    # (which just hands control back so the user can reply), TERMINATE_SESSION
    # permanently closes the session to new messages.
    TERMINATE_SESSION = "terminate_session"
