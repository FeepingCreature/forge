"""
SEARCH/REPLACE tool for making code edits using VFS
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.vfs.base import VFS


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "function": {
            "name": "search_replace",
            "description": "Make a SEARCH/REPLACE edit to a file. Works on VFS (git + pending changes).",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Path to the file to edit"},
                    "search": {"type": "string", "description": "Exact text to search for"},
                    "replace": {"type": "string", "description": "Text to replace with"},
                },
                "required": ["filepath", "search", "replace"],
            },
        },
    }


def execute(vfs: "VFS", args: dict[str, Any]) -> dict[str, Any]:
    """Execute the search/replace operation using VFS"""
    filepath = args.get("filepath")
    search = args.get("search")
    replace = args.get("replace")

    # Type check arguments
    if not isinstance(filepath, str) or not isinstance(search, str) or not isinstance(replace, str):
        return {"success": False, "error": "Missing required arguments"}

    # Read current state from VFS (includes pending changes from previous tools)
    try:
        content = vfs.read_file(filepath)
    except FileNotFoundError:
        return {"success": False, "error": f"File not found: {filepath}"}

    if search not in content:
        return {"success": False, "error": "Search text not found in file"}

    # Replace first occurrence
    new_content = content.replace(search, replace, 1)

    # Write back to VFS
    vfs.write_file(filepath, new_content)

    return {"success": True, "message": f"Replaced in {filepath}"}
