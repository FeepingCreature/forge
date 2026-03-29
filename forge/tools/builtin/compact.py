"""
Compact tool - replace conversation messages with summaries to reduce context size.

Check <context_stats> first! Compact only reduces the conversation portion of context.
If conversation is small relative to files/summaries, compacting saves almost nothing.

Use this when conversation is large (20k+ tokens) and contains:
- Old tool results/diffs that are redundant (files are in context showing current state)
- Debug output you've already understood and acted on
- Failed approaches you've moved past

Do NOT use when conversation is small — you lose useful history for negligible savings.
For reducing file context, use update_context to remove files instead.
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
                "Check <context_stats> first — compact only helps when conversation tokens are "
                "a large portion of context. If conversation is small (under ~10k tokens), "
                "compacting saves almost nothing and loses useful history. "
                "For reducing file context, use update_context to remove files instead. "
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
