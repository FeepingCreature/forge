"""
Read a file from the VFS (git + pending changes)
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.vfs.base import VFS


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the full content of a file from VFS (git commit + pending changes from this turn).",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path to the file to read",
                    },
                },
                "required": ["filepath"],
            },
        },
    }


def execute(vfs: "VFS", args: dict[str, Any]) -> dict[str, Any]:
    """Read file from VFS"""
    filepath = args.get("filepath")

    if not isinstance(filepath, str):
        return {"success": False, "error": "filepath must be a string"}

    try:
        content = vfs.read_file(filepath)
        return {"success": True, "content": content, "filepath": filepath}
    except FileNotFoundError:
        return {"success": False, "error": f"File not found: {filepath}"}
