"""
Compact tool - replace conversation messages with summaries to reduce context size.

Use this when:
- Previous edits are redundant because the file is now in context
- Old search results or tool outputs are no longer relevant
- You want to consolidate multiple operations into a single summary

The conversation recap shows message IDs in brackets like [1], [2], etc.
Use these IDs to specify the range to compact.
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
                "Replace previous conversation messages with a summary to reduce context size. "
                "Use when previous edits are redundant (file is in context), old search results "
                "are stale, or you want to consolidate multiple operations. "
                "The summary replaces the original message content. "
                "Message IDs are shown in the conversation recap as [id 1], [id 2], etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "from_id": {
                        "type": "string",
                        "description": "First message ID to compact (inclusive). See conversation recap for IDs.",
                    },
                    "to_id": {
                        "type": "string",
                        "description": "Last message ID to compact (inclusive). See conversation recap for IDs.",
                    },
                    "summary": {
                        "type": "string",
                        "description": (
                            "Summary of what those messages did. Include enough detail "
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
    Compact conversation messages by replacing them with a summary.

    This is a special tool - it doesn't use VFS directly but signals
    to the PromptManager to replace message blocks.
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
