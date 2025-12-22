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
                    "tool_call_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of tool_call_ids to compact",
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
                "required": ["tool_call_ids", "summary"],
            },
        },
    }


def execute(vfs: "VFS", args: dict[str, Any]) -> dict[str, Any]:
    """
    Compact tool results by replacing them with a summary.

    This is a special tool - it doesn't use VFS directly but signals
    to the PromptManager to replace tool result blocks.
    """
    tool_call_ids = args.get("tool_call_ids", [])
    summary = args.get("summary", "")

    if not tool_call_ids:
        return {"success": False, "error": "No tool_call_ids provided"}

    if not summary:
        return {"success": False, "error": "No summary provided"}

    if not isinstance(tool_call_ids, list):
        return {"success": False, "error": "tool_call_ids must be a list"}

    # Return special result that signals compaction
    # The session manager will handle the actual compaction
    return {
        "success": True,
        "compact": True,
        "tool_call_ids": tool_call_ids,
        "summary": summary,
        "message": f"Compacted {len(tool_call_ids)} tool result(s)",
    }
