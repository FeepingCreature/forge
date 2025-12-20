"""
Grep for a pattern and add matching files to context
"""

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.vfs.base import VFS


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "function": {
            "name": "grep_open",
            "description": """Search for a pattern in all files and add matching files to context.

Use this to discover which files reference a function, class, variable, or string.
This is the primary way to find all call sites or usages before making changes.

Example: To find all files that use `MyClass`, call grep_open with pattern="MyClass"

The pattern is a Python regex. Common patterns:
- "MyClass" - literal string match
- "def my_function" - find function definitions
- "import.*module" - find imports of a module
- "TODO|FIXME" - find multiple patterns

Returns list of matching files with match counts. All matching files are added to context.""",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Python regex pattern to search for",
                    },
                    "include_extensions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Only search files with these extensions (e.g., ['.py', '.js']). Empty = all files.",
                        "default": [],
                    },
                    "exclude_dirs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Directory names to exclude (e.g., ['node_modules', '.git'])",
                        "default": [".git", "__pycache__", "node_modules", ".venv", "venv"],
                    },
                },
                "required": ["pattern"],
            },
        },
    }


def execute(vfs: "VFS", args: dict[str, Any]) -> dict[str, Any]:
    """Search for pattern and add matching files to context"""
    pattern = args.get("pattern")
    include_extensions = args.get("include_extensions", [])
    exclude_dirs = args.get(
        "exclude_dirs", [".git", "__pycache__", "node_modules", ".venv", "venv"]
    )

    if not isinstance(pattern, str):
        return {"success": False, "error": "pattern must be a string"}

    # Compile regex
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return {"success": False, "error": f"Invalid regex pattern: {e}"}

    # Get all files
    all_files = vfs.list_files()

    # Filter and search
    matches: list[dict[str, Any]] = []
    files_to_add: list[str] = []

    for filepath in all_files:
        # Check exclusions
        skip = False
        for exclude_dir in exclude_dirs:
            if f"/{exclude_dir}/" in f"/{filepath}" or filepath.startswith(f"{exclude_dir}/"):
                skip = True
                break
        if skip:
            continue

        # Check extensions
        if include_extensions:
            ext_match = False
            for ext in include_extensions:
                if filepath.endswith(ext):
                    ext_match = True
                    break
            if not ext_match:
                continue

        # Read and search
        try:
            content = vfs.read_file(filepath)
        except (FileNotFoundError, UnicodeDecodeError):
            continue

        # Count matches
        found = regex.findall(content)
        if found:
            matches.append(
                {
                    "filepath": filepath,
                    "match_count": len(found),
                    "first_match": found[0][:50] if found else "",
                }
            )
            files_to_add.append(filepath)

    if not matches:
        return {
            "success": True,
            "message": f"No files match pattern: {pattern}",
            "matches": [],
            "action": "update_context",
            "add": [],
            "remove": [],
        }

    return {
        "success": True,
        "message": f"Found {len(matches)} files matching '{pattern}'",
        "matches": matches,
        "action": "update_context",
        "add": files_to_add,
        "remove": [],
    }
