"""
Remove a file from the active context
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.vfs.base import VFS


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "function": {
            "name": "remove_file_from_context",
            "description": "Remove a file from active context to reduce token usage. Use this when you no longer need to see the full file content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path to the file to remove from context",
                    },
                },
                "required": ["filepath"],
            },
        },
    }


def execute(vfs: "VFS", args: dict[str, Any]) -> dict[str, Any]:
    """Remove file from active context"""
    filepath = args.get("filepath")

    if not isinstance(filepath, str):
        return {"success": False, "error": "filepath must be a string"}

    # Note: The actual context management happens in SessionManager
    # This tool just signals the intent - SessionManager will handle it
    return {
        "success": True,
        "message": f"File {filepath} will be removed from context",
        "action": "remove_from_context",
        "filepath": filepath,
    }
