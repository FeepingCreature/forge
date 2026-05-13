"""
Signal that the current AI turn is complete and hand control back to the user.

Only meaningful when the `llm.require_done_tag` setting is enabled. In that
mode the orchestrator will NOT end a turn after a text-only response; the
assistant must invoke this tool inline (as a self-closing `done` tag) to
yield. Without it, the orchestrator injects a reminder user message and
continues the loop.

When `llm.require_done_tag` is disabled, this tool is still callable but is
effectively a no-op — turns end naturally after text-only responses anyway.
"""

import re
from typing import TYPE_CHECKING, Any

from forge.tools.side_effects import SideEffect

if TYPE_CHECKING:
    from forge.vfs.work_in_progress import WorkInProgressVFS


# Pattern: self-closing done tag, no arguments
_INLINE_PATTERN = re.compile(r"<done\s*/>")


def get_inline_pattern() -> re.Pattern[str]:
    """Return compiled regex for inline invocation."""
    return _INLINE_PATTERN


def parse_inline_match(match: re.Match[str]) -> dict[str, Any]:
    """Parse regex match into tool arguments (none)."""
    return {}


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM."""
    return {
        "type": "function",
        "invocation": "inline",
        "inline_syntax": "<done/>",
        "function": {
            "name": "done",
            "description": (
                "Signal that you are finished with the current turn and want "
                "to hand control back to the user. In strict mode "
                "(llm.require_done_tag), the turn will NOT end until you "
                "emit this — otherwise a reminder is injected and you are "
                "called again. Use this when you have nothing more to do, "
                "or when you have a question and want the user to respond. "
                "Do NOT emit this between edits if you intend to keep "
                "working in the same turn."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    }


def execute(vfs: "WorkInProgressVFS", args: dict[str, Any]) -> dict[str, Any]:
    """Mark the turn as voluntarily ended.

    The actual control-flow decision happens in LiveSession, which looks for
    SideEffect.END_TURN among the inline-command results before deciding
    whether to inject a reminder and loop again.
    """
    return {
        "success": True,
        "message": "Turn ended.",
        "side_effects": [SideEffect.END_TURN],
    }