"""
Delete a file from VFS
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.vfs.base import VFS


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a file from VFS. The deletion will be committed with other changes at end of turn.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path to the file to delete",
                    },
                },
                "required": ["filepath"],
            },
        },
    }


def execute(vfs: "VFS", args: dict[str, Any]) -> dict[str, Any]:
    """Delete file from VFS"""
    filepath = args.get("filepath")

    if not isinstance(filepath, str):
        return {"success": False, "error": "filepath must be a string"}

    try:
        vfs.delete_file(filepath)
        return {"success": True, "message": f"Deleted {filepath}"}
    except FileNotFoundError:
        return {"success": False, "error": f"File not found: {filepath}"}
