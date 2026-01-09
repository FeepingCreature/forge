"""
Write a complete file to VFS (creates or overwrites)
"""

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.vfs.base import VFS


# Pattern: <write file="path">content</write>
_INLINE_PATTERN = re.compile(
    r'<write\s+file="([^"]+)">\n?(.*?)\n?</write>',
    re.DOTALL,
)


def get_inline_pattern() -> re.Pattern[str]:
    """Return compiled regex for inline invocation."""
    return _INLINE_PATTERN


def parse_inline_match(match: re.Match[str]) -> dict[str, Any]:
    """Parse regex match into tool arguments."""
    return {
        "filepath": match.group(1),
        "content": match.group(2),
    }


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "invocation": "inline",
        "inline_syntax": '<write file="path/to/file.py">\nfile content here\n</write>',
        "function": {
            "name": "write_file",
            "description": "Write complete file content to VFS. Creates new file or overwrites existing. Use for new files or complete rewrites.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path to the file to write",
                    },
                    "content": {
                        "type": "string",
                        "description": "Complete file content to write",
                    },
                },
                "required": ["filepath", "content"],
            },
        },
    }


def execute(vfs: "VFS", args: dict[str, Any]) -> dict[str, Any]:
    """Write file to VFS"""
    filepath = args.get("filepath")
    content = args.get("content")

    if not isinstance(filepath, str) or not isinstance(content, str):
        return {"success": False, "error": "filepath and content must be strings"}

    vfs.write_file(filepath, content)
    return {"success": True, "message": f"Wrote {len(content)} bytes to {filepath}"}
