"""
Say tool - emit text to the user mid-turn.

Use this after tools like `think` where you don't need to see the result
but want to continue narrating to the user. The message will be displayed
as regular assistant text, not as a tool result.

Example chain: think(...) → say("Based on my analysis...") → search_replace(...)
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.vfs.work_in_progress import WorkInProgressVFS


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "function": {
            "name": "say",
            "description": (
                "Emit text to the user mid-turn. Use this after tools like `think` "
                "where you don't need to see the result but want to continue "
                "narrating. The message appears as regular assistant text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Text to display to the user",
                    },
                },
                "required": ["message"],
            },
        },
    }


def execute(vfs: "WorkInProgressVFS", args: dict[str, Any]) -> dict[str, Any]:
    """Execute the say tool - returns message for display as assistant text"""
    message = args.get("message", "")

    if not message:
        return {"success": False, "error": "message is required"}

    return {
        "success": True,
        "say": True,  # Special flag for UI to display as assistant text
        "message": message,
    }
