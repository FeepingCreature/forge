"""
Add a file to the active context (full content will be included in future turns)
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.vfs.base import VFS


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "function": {
            "name": "add_file_to_context",
            "description": "Add a file to active context. Its full content will be included in future AI turns. Use this when you need to see the complete file content to make changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path to the file to add to context",
                    },
                },
                "required": ["filepath"],
            },
        },
    }


def execute(vfs: "VFS", args: dict[str, Any]) -> dict[str, Any]:
    """Add file to active context"""
    filepath = args.get("filepath")

    if not isinstance(filepath, str):
        return {"success": False, "error": "filepath must be a string"}

    # Check if file exists
    if not vfs.file_exists(filepath):
        return {"success": False, "error": f"File not found: {filepath}"}

    # Note: The actual context management happens in SessionManager
    # This tool just signals the intent - SessionManager will handle it
    return {
        "success": True,
        "message": f"File {filepath} will be added to context",
        "action": "add_to_context",
        "filepath": filepath,
    }
