"""
Rename/move a file in VFS
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.vfs.base import VFS


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "invocation": "inline",
        "inline_syntax": '<rename old="old/path.py" new="new/path.py"/>',
        "function": {
            "name": "rename_file",
            "description": "Rename or move a file in VFS. This reads the file content, writes it to the new path, and deletes the old path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "old_path": {
                        "type": "string",
                        "description": "Current path of the file",
                    },
                    "new_path": {
                        "type": "string",
                        "description": "New path for the file",
                    },
                },
                "required": ["old_path", "new_path"],
            },
        },
    }


def execute(vfs: "VFS", args: dict[str, Any]) -> dict[str, Any]:
    """Rename/move file in VFS"""
    old_path = args.get("old_path")
    new_path = args.get("new_path")

    if not isinstance(old_path, str) or not isinstance(new_path, str):
        return {"success": False, "error": "old_path and new_path must be strings"}

    if not vfs.file_exists(old_path):
        return {"success": False, "error": f"File not found: {old_path}"}

    if vfs.file_exists(new_path):
        return {"success": False, "error": f"Destination already exists: {new_path}"}

    # Read content, write to new location, delete old
    content = vfs.read_file(old_path)
    vfs.write_file(new_path, content)
    vfs.delete_file(old_path)

    return {"success": True, "message": f"Renamed {old_path} -> {new_path}"}
