"""
Done tool - signals completion of AI turn with a final message.

This tool is special: when executed as part of a tool chain, it signals
that the AI has completed its work. The message is displayed to the user
as the AI's final response for this turn.

If any tool in the chain fails before `done`, the chain aborts and the AI
gets control back to handle the error. If all tools succeed including `done`,
the turn ends cleanly with the done message shown to the user.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.vfs.work_in_progress import WorkInProgressVFS


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "function": {
            "name": "done",
            "description": (
                "Signal that you've completed your work for this turn. "
                "The message will be shown to the user as your final response. "
                "Use this at the end of a tool chain to provide a summary. "
                "If any earlier tool in the chain failed, done won't execute "
                "and you'll get control back to handle the error."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Your final message to the user summarizing what you did",
                    },
                },
                "required": ["message"],
            },
        },
    }


def execute(vfs: "WorkInProgressVFS", args: dict[str, Any]) -> dict[str, Any]:
    """Execute the done tool - just returns the message for display"""
    message = args.get("message", "")

    if not message:
        return {"success": False, "error": "message is required"}

    return {
        "success": True,
        "done": True,  # Special flag to signal turn completion
        "message": message,
    }
