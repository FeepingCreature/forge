"""
List all files in the repository
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.vfs.base import VFS


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List all files in the repository. Returns file paths only - use read_file to see content.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    }


def execute(vfs: "VFS", args: dict[str, Any]) -> dict[str, Any]:
    """List all files in VFS"""
    files = vfs.list_files()
    # Filter out .forge metadata
    files = [f for f in files if not f.startswith(".forge/")]
    return {
        "success": True,
        "files": files,
        "count": len(files),
    }
