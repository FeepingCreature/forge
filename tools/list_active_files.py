"""
List files currently in active context
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.vfs.base import VFS


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "function": {
            "name": "list_active_files",
            "description": "List all files currently in active context (files with full content included in AI turns).",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    }


def execute(vfs: "VFS", args: dict[str, Any]) -> dict[str, Any]:
    """List active files"""
    # Note: The actual active files list is in SessionManager
    # This tool just signals the query - SessionManager will provide the list
    return {
        "success": True,
        "action": "list_active_files",
    }
