"""
Grep for a pattern and show surrounding context without adding files to active context.
Useful for peeking at matches to decide if you need the full file.
"""

import re
from typing import TYPE_CHECKING, Any

from forge.tools.builtin.grep_utils import DEFAULT_EXCLUDE_DIRS, get_files_to_search

if TYPE_CHECKING:
    from forge.vfs.base import VFS


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "function": {
            "name": "grep_context",
            "description": """Search for a pattern and show lines around each match WITHOUT adding files to context.

Use this to peek at code before deciding if you need the full file. Unlike grep_open, this
returns snippets inline and does NOT add files to your active context.

Good for:
- Quickly checking how a function is called without loading the whole file
- Scanning multiple files for a pattern to find the relevant one
- Investigating before committing to loading large files

The pattern is a Python regex.""",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Python regex pattern to search for",
                    },
                    "context_before": {
                        "type": "integer",
                        "description": "Lines to show before each match (default: 3)",
                        "default": 3,
                    },
                    "context_after": {
                        "type": "integer",
                        "description": "Lines to show after each match (default: 3)",
                        "default": 3,
                    },
                    "file": {
                        "type": "string",
                        "description": "Optional: limit search to a single file",
                    },
                    "max_matches": {
                        "type": "integer",
                        "description": "Maximum matches to return (default: 10)",
                        "default": 10,
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
                        "description": "Directory names to exclude",
                        "default": [".git", "__pycache__", "node_modules", ".venv", "venv"],
                    },
                },
                "required": ["pattern"],
            },
        },
    }


def execute(vfs: "VFS", args: dict[str, Any]) -> dict[str, Any]:
    """Search for pattern and return context snippets (no context modification)"""
    pattern = args.get("pattern")
    context_before = args.get("context_before", 3)
    context_after = args.get("context_after", 3)
    single_file = args.get("file")
    max_matches = args.get("max_matches", 10)
    include_extensions = args.get("include_extensions", [])
    exclude_dirs = args.get("exclude_dirs", DEFAULT_EXCLUDE_DIRS)

    if not isinstance(pattern, str):
        return {"success": False, "error": "pattern must be a string"}

    if not isinstance(context_before, int) or context_before < 0:
        return {"success": False, "error": "context_before must be a non-negative integer"}

    if not isinstance(context_after, int) or context_after < 0:
        return {"success": False, "error": "context_after must be a non-negative integer"}

    # Compile regex
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return {"success": False, "error": f"Invalid regex pattern: {e}"}

    # Get files to search
    if single_file:
        files_to_search = [single_file]
    else:
        files_to_search = get_files_to_search(vfs, exclude_dirs, include_extensions)

    # Search and collect snippets
    snippets: list[dict[str, Any]] = []
    total_matches = 0

    for filepath in files_to_search:
        if len(snippets) >= max_matches:
            break

        try:
            content = vfs.read_file(filepath)
        except FileNotFoundError:
            if single_file:
                return {"success": False, "error": f"File not found: {filepath}"}
            continue
        except UnicodeDecodeError:
            continue

        lines = content.split("\n")

        # Find all matches with line numbers
        for i, line in enumerate(lines):
            if regex.search(line):
                total_matches += 1
                if len(snippets) >= max_matches:
                    break

                # Extract context
                start = max(0, i - context_before)
                end = min(len(lines), i + context_after + 1)

                snippet_lines = []
                for j in range(start, end):
                    marker = ">>>" if j == i else "   "
                    snippet_lines.append(f"{marker} {j + 1:4d} | {lines[j]}")

                snippets.append(
                    {
                        "filepath": filepath,
                        "line": i + 1,
                        "snippet": "\n".join(snippet_lines),
                    }
                )

    if not snippets:
        return {
            "success": True,
            "message": f"No matches found for pattern: {pattern}",
            "snippets": [],
            "total_matches": 0,
        }

    # Format output
    output_parts = []
    for s in snippets:
        output_parts.append(f"── {s['filepath']}:{s['line']} ──\n{s['snippet']}")

    truncated_msg = ""
    if total_matches > max_matches:
        truncated_msg = f" (showing {max_matches} of {total_matches} total matches)"

    return {
        "success": True,
        "message": f"Found {len(snippets)} matches{truncated_msg}",
        "output": "\n\n".join(output_parts),
        "total_matches": total_matches,
    }
