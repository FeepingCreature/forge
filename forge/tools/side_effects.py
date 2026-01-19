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

    # Marks that this tool result is ephemeral - only available for one AI response.
    # After the AI sees this result, it's replaced with a placeholder message.
    # Use for tools that return large results used for immediate decision-making
    # (e.g., grep_context snippets that help decide what files to load).
    EPHEMERAL_RESULT = "ephemeral_result"
