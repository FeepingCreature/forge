"""
Undo edits to a file by reverting to its state in the base commit.
Useful when search_replace goes wrong or you want to start fresh on a file.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.vfs.work_in_progress import WorkInProgressVFS


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "function": {
            "name": "undo_edit",
            "description": """Revert a file to its state in the base commit, undoing all pending edits.

Use this when:
- A search_replace went wrong and you want to start fresh
- You've made multiple edits and want to undo them all
- You accidentally deleted or corrupted a file

This reverts the file to its state at the start of this turn (the base commit).
If the file was created this turn, it will be deleted.
If the file was deleted this turn, it will be restored.

Note: This only works within a single turn. Once changes are committed (at end of turn
or via the commit tool), they cannot be undone with this tool.""",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path to the file to revert",
                    },
                },
                "required": ["filepath"],
            },
        },
    }


def execute(vfs: "WorkInProgressVFS", args: dict[str, Any]) -> dict[str, Any]:
    """Revert file to base commit state"""
    filepath = args.get("filepath")

    if not isinstance(filepath, str):
        return {"success": False, "error": "filepath must be a string"}

    # Check if this is a WorkInProgressVFS
    if not hasattr(vfs, "get_pending_changes") or not hasattr(vfs, "base_vfs"):
        return {
            "success": False,
            "error": "undo_edit requires a writable VFS with pending changes",
        }

    pending_changes = vfs.get_pending_changes()
    deleted_files = vfs.get_deleted_files()

    # Check if there are any changes to this file
    has_pending_edit = filepath in pending_changes
    has_pending_delete = filepath in deleted_files

    if not has_pending_edit and not has_pending_delete:
        return {
            "success": False,
            "error": f"No pending changes to undo for: {filepath}",
        }

    # Check if file exists in base
    base_exists = vfs.base_vfs.file_exists(filepath)

    # Determine what action to take
    if has_pending_delete:
        # File was deleted this turn - restore it
        if base_exists:
            # Remove from deleted set - file will be visible again from base
            deleted_files.discard(filepath)
            # Also remove any pending content changes
            if filepath in pending_changes:
                del pending_changes[filepath]
            return {
                "success": True,
                "action": "restored",
                "message": f"Restored deleted file: {filepath}",
            }
        else:
            # File was created and then deleted - nothing to restore
            deleted_files.discard(filepath)
            if filepath in pending_changes:
                del pending_changes[filepath]
            return {
                "success": True,
                "action": "cleared",
                "message": f"Cleared pending changes for: {filepath} (file did not exist in base)",
            }

    if has_pending_edit:
        # File was edited this turn
        if base_exists:
            # Remove pending changes - file will read from base
            del pending_changes[filepath]
            return {
                "success": True,
                "action": "reverted",
                "message": f"Reverted to base commit version: {filepath}",
            }
        else:
            # File was created this turn - delete it
            del pending_changes[filepath]
            return {
                "success": True,
                "action": "removed",
                "message": f"Removed newly created file: {filepath}",
            }

    # Should not reach here
    return {"success": False, "error": "Unexpected state"}
