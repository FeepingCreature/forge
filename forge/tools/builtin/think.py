"""
Think tool - extended reasoning scratchpad with auto-compaction.

Use this when you need to work through complex logic, explore options,
or reason step-by-step. The scratchpad content is discarded after use
to avoid bloating context - only the conclusion is kept.

This gives you "out-loud thinking" without the context cost.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.vfs.base import VFS


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "function": {
            "name": "think",
            "description": (
                "Extended reasoning scratchpad. Use when you need to think through "
                "complex problems step-by-step. The scratchpad is automatically "
                "discarded to save context - only your conclusion is kept. "
                "Good for: planning multi-step changes, weighing tradeoffs, "
                "working through logic, debugging hypotheses."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scratchpad": {
                        "type": "string",
                        "description": (
                            "Your working space for extended reasoning. Write out your "
                            "thought process, explore options, work through logic. "
                            "This content will be discarded after the call."
                        ),
                    },
                    "conclusion": {
                        "type": "string",
                        "description": (
                            "Your conclusion or summary. This is kept in context for "
                            "future reference. Should capture the key decision or insight."
                        ),
                    },
                },
                "required": ["scratchpad", "conclusion"],
            },
        },
    }


def execute(vfs: "VFS", args: dict[str, Any]) -> dict[str, Any]:
    """
    Execute the think tool.

    The actual "thinking" happened when the model generated the scratchpad.
    We just return the conclusion and signal that this call should be compacted.
    """
    scratchpad = args.get("scratchpad", "")
    conclusion = args.get("conclusion", "")

    if not conclusion:
        return {"success": False, "error": "No conclusion provided"}

    # Count tokens roughly (words * 1.3 is a rough approximation)
    scratchpad_words = len(scratchpad.split())
    scratchpad_tokens_approx = int(scratchpad_words * 1.3)

    return {
        "success": True,
        "think": True,  # Signal for special handling
        "conclusion": conclusion,
        "message": f"Thought through {scratchpad_tokens_approx} tokens, concluded.",
    }
