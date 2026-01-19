"""
Get lines surrounding a specific line number in a file.
Useful for investigating error messages that reference line numbers.
"""

from typing import TYPE_CHECKING, Any

from forge.tools.side_effects import SideEffect

if TYPE_CHECKING:
    from forge.vfs.base import VFS


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "function": {
            "name": "get_lines",
            "description": """Get lines surrounding a specific line number in a file. Useful for investigating errors that reference line numbers.

**EPHEMERAL**: This tool's results are only available for ONE turn. After you respond,
the full output is replaced with a placeholder to save context space. Use this for quick
lookups where you'll act immediately on the results.""",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path to the file",
                    },
                    "line": {
                        "type": "integer",
                        "description": "Line number to center on (1-indexed)",
                    },
                    "context": {
                        "type": "integer",
                        "description": "Number of lines to show before and after (default: 10)",
                    },
                },
                "required": ["filepath", "line"],
            },
        },
    }


def execute(vfs: "VFS", args: dict[str, Any]) -> dict[str, Any]:
    """Get lines surrounding a specific line number"""
    filepath = args.get("filepath")
    line_num = args.get("line")
    context = args.get("context", 10)

    if not isinstance(filepath, str):
        return {"success": False, "error": "filepath must be a string"}

    if not isinstance(line_num, int) or line_num < 1:
        return {"success": False, "error": "line must be a positive integer"}

    if not isinstance(context, int) or context < 0:
        return {"success": False, "error": "context must be a non-negative integer"}

    try:
        content = vfs.read_file(filepath)
    except FileNotFoundError:
        return {"success": False, "error": f"File not found: {filepath}"}

    lines = content.split("\n")
    total_lines = len(lines)

    if line_num > total_lines:
        return {
            "success": False,
            "error": f"Line {line_num} is beyond end of file ({total_lines} lines)",
        }

    # Calculate range (convert to 0-indexed)
    start = max(0, line_num - 1 - context)
    end = min(total_lines, line_num + context)

    # Build output with line numbers
    output_lines = []
    for i in range(start, end):
        line_indicator = ">>>" if i == line_num - 1 else "   "
        output_lines.append(f"{line_indicator} {i + 1:4d} | {lines[i]}")

    result = "\n".join(output_lines)

    return {
        "success": True,
        "filepath": filepath,
        "target_line": line_num,
        "range": f"{start + 1}-{end}",
        "total_lines": total_lines,
        "content": result,
        "side_effects": [SideEffect.EPHEMERAL_RESULT],
    }
