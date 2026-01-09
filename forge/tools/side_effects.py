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
