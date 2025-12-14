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
            "description": """Add or remove files from active context in a single call.

IMPORTANT: 
- Load ALL files you need in ONE call (batch operation) to minimize round-trips
- CLOSE files you no longer need to keep context size small
- Files in active context have full content included in every turn (costs tokens)
- Use file summaries to decide what to load - don't load speculatively

Example: {"add": ["src/a.py", "src/b.py"], "remove": ["src/old.py"]}""",
            "parameters": {
                "type": "object",
                "properties": {
                    "add": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "File paths to add to context (load multiple at once!)",
                        "default": [],
                    },
                    "remove": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "File paths to remove from context (close files you're done with)",
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
