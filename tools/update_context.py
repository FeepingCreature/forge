"""
Update active context by adding/removing files
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.vfs.base import VFS


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "function": {
            "name": "update_context",
            "description": "Add or remove files from active context. Files in active context have their full content included in future AI turns. Use this to manage token usage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "add": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of file paths to add to active context",
                        "default": [],
                    },
                    "remove": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of file paths to remove from active context",
                        "default": [],
                    },
                },
            },
        },
    }


def execute(vfs: "VFS", args: dict[str, Any]) -> dict[str, Any]:
    """Update active context"""
    add_files = args.get("add", [])
    remove_files = args.get("remove", [])

    if not isinstance(add_files, list) or not isinstance(remove_files, list):
        return {"success": False, "error": "add and remove must be arrays"}

    # Validate all paths are strings
    if not all(isinstance(f, str) for f in add_files):
        return {"success": False, "error": "all add paths must be strings"}
    if not all(isinstance(f, str) for f in remove_files):
        return {"success": False, "error": "all remove paths must be strings"}

    # Check that files to add exist
    for filepath in add_files:
        if not vfs.file_exists(filepath):
            return {"success": False, "error": f"File not found: {filepath}"}

    # Note: The actual context management happens in SessionManager
    # This tool just signals the intent - SessionManager will handle it
    return {
        "success": True,
        "action": "update_context",
        "add": add_files,
        "remove": remove_files,
        "message": f"Added {len(add_files)} files, removed {len(remove_files)} files from context",
    }
