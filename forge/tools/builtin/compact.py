"""
Compact tool - replace tool results with summaries to reduce context size.

Use this when:
- A diff is redundant because the full file is now in context
- Old search results are no longer relevant
- You want to consolidate multiple operations into a single summary
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.vfs.base import VFS


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "function": {
            "name": "compact",
            "description": (
                "Replace previous tool results with a summary to reduce context size. "
                "Use when diffs are redundant (file is in context), old search results "
                "are stale, or you want to consolidate multiple operations. "
                "The summary replaces the original tool result content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "from_id": {
                        "type": "string",
                        "description": "First tool_call_id to compact (inclusive)",
                    },
                    "to_id": {
                        "type": "string",
                        "description": "Last tool_call_id to compact (inclusive)",
                    },
                    "summary": {
                        "type": "string",
                        "description": (
                            "Summary of what those tool calls did. Include enough detail "
                            "to stay oriented: which functions/classes were added or modified, "
                            "key logic changes, etc. E.g., 'Added calculate_totals() and "
                            "format_output() to utils.py, updated main() to call them'"
                        ),
                    },
                },
                "required": ["from_id", "to_id", "summary"],
            },
        },
    }


def execute(vfs: "VFS", args: dict[str, Any]) -> dict[str, Any]:
    """
    Compact tool results by replacing them with a summary.

    This is a special tool - it doesn't use VFS directly but signals
    to the PromptManager to replace tool result blocks.
    """
    from_id = args.get("from_id", "")
    to_id = args.get("to_id", "")
    summary = args.get("summary", "")

    if not from_id:
        return {"success": False, "error": "No from_id provided"}

    if not to_id:
        return {"success": False, "error": "No to_id provided"}

    if not summary:
        return {"success": False, "error": "No summary provided"}

    # Return special result that signals compaction
    # The session manager will handle the actual compaction
    return {
        "success": True,
        "compact": True,
        "from_id": from_id,
        "to_id": to_id,
        "summary": summary,
    }
