"""
Narrate progress between tool calls without ending the turn.

The provider (Anthropic) ends the assistant's turn after the final tool
call's results are returned. That means any prose written *after* the last
tool call cannot keep the pipeline going — the turn is already over. `say`
exists to solve exactly this: it is itself a tool call, so emitting it keeps
the turn alive, and its `message` argument is rendered to the user as plain
narration (not a tool card).

This lets the model interleave narration with actions in a single turn:

    say("Editing the parser…") → edit(…) → say("Running the tests…")
    → run_tests() → say("All green, committing.") → commit(…) → done()

`say` is an API tool only — there is no inline `<say>` form, because inline
prose can't keep the pipeline alive in the first place (that's the whole
reason this tool is needed).

execute() is a deliberate no-op: the narration lives entirely in the tool
call's arguments (and is surfaced by the UI from there). There are no side
effects on the VFS or session.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.vfs.work_in_progress import WorkInProgressVFS


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM."""
    return {
        "type": "function",
        "function": {
            "name": "say",
            "description": (
                "Narrate progress to the user mid-turn, between other tool "
                "calls, WITHOUT ending your turn. The turn ends after your "
                "last tool call's results come back, so prose written after "
                "tool calls is lost — use `say` to narrate instead. Each `say` "
                "renders as plain text to the user. Typical flow: "
                "say('Editing X')→edit(...)→say('Running tests')→run_tests()→"
                "say('Committing')→commit(...). Keep messages short. This has "
                "no side effects."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": (
                            "The narration to show the user. Rendered as "
                            "markdown prose, not a tool card."
                        ),
                    },
                },
                "required": ["message"],
            },
        },
    }


def execute(vfs: "WorkInProgressVFS", args: dict[str, Any]) -> dict[str, Any]:
    """No-op: the narration lives in the tool-call arguments.

    The UI renders the `message` argument as prose; there is nothing to do
    here and no side effects to declare.
    """
    return {"success": True}
