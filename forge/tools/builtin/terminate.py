"""
Terminate the AI session, blocking any further user input.

Unlike `done` (which simply hands control back so the user can reply),
`terminate` permanently closes the session: the current turn finishes, the
session transitions to the COMPLETED state, and `send_message` will refuse
any further input.

This is intended as a deliberate, hard stop — e.g. when the assistant has
determined that continuing would be unsafe, unproductive, or when a
high-stress situation warrants ending the conversation entirely rather than
looping. Use it sparingly: a normal end-of-turn should use `done` (or just a
text response), not `terminate`.

Invoked inline as a self-closing `terminate` tag. An optional reason can be
supplied to explain why the session was terminated.
"""

import re
from typing import TYPE_CHECKING, Any

from forge.tools.side_effects import SideEffect

if TYPE_CHECKING:
    from forge.vfs.work_in_progress import WorkInProgressVFS


# Pattern: self-closing terminate tag, optional reason="..." attribute.
# Matches <terminate/> and <terminate reason="some text"/>.
_INLINE_PATTERN = re.compile(r'<terminate(?:\s+reason="([^"]*)")?\s*/>')


def get_inline_pattern() -> re.Pattern[str]:
    """Return compiled regex for inline invocation."""
    return _INLINE_PATTERN


def parse_inline_match(match: re.Match[str]) -> dict[str, Any]:
    """Parse regex match into tool arguments.

    Group 1 is the optional reason text (None if the bare tag was used).
    """
    reason = match.group(1)
    return {"reason": reason} if reason else {}


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM."""
    return {
        "type": "function",
        "invocation": "inline",
        "inline_syntax": '<terminate/> or <terminate reason="why"/>',
        "function": {
            "name": "terminate",
            "description": (
                "Permanently terminate this session. The current turn "
                "finishes, the session is marked COMPLETED, and NO further "
                "user input will be accepted. This is a hard stop — much "
                "stronger than `done`, which merely hands control back so the "
                "user can reply. Use this only when the conversation should "
                "end entirely (e.g. continuing would be unsafe or "
                "unproductive). You may pass an optional reason explaining "
                "why. Do NOT use this for an ordinary end-of-turn — use "
                "`done` or a plain text response instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": (
                            "Optional explanation for why the session is being "
                            "terminated. Shown to the user."
                        ),
                    },
                },
            },
        },
    }


def execute(vfs: "WorkInProgressVFS", args: dict[str, Any]) -> dict[str, Any]:
    """Mark the session for termination.

    The actual control-flow decision happens in LiveSession, which looks for
    SideEffect.TERMINATE_SESSION among the tool results and transitions the
    session to COMPLETED (blocking further input) at the end of the turn.
    """
    reason = args.get("reason")
    message = "Session terminated."
    if reason:
        message = f"Session terminated: {reason}"

    return {
        "success": True,
        "message": message,
        "reason": reason,
        "side_effects": [SideEffect.TERMINATE_SESSION],
    }