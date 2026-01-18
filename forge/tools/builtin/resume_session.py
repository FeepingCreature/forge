"""
resume_session tool - Send a message to a child session and start/resume it.

This tool appends a message to a child session's conversation and kicks off
its execution. The child runs asynchronously - use wait_session to check
for completion or questions.

Uses Tool API v2 (ToolContext) for clean access to repo and branch_name.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.tools.context import ToolContext


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM."""
    return {
        "type": "function",
        "function": {
            "name": "resume_session",
            "description": (
                "Send a message to a child session and start/resume its execution. "
                "The child runs asynchronously. Use wait_session to check for "
                "completion or questions from the child."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "branch": {
                        "type": "string",
                        "description": "Branch name of the child session to resume.",
                    },
                    "message": {
                        "type": "string",
                        "description": (
                            "Message to send to the child session. For initial start, "
                            "this should be the task instructions. For resuming after "
                            "a question, this should answer the child's question."
                        ),
                    },
                },
                "required": ["branch", "message"],
            },
        },
    }


def execute(ctx: "ToolContext", args: dict[str, Any]) -> dict[str, Any]:
    """Send message to child session and start/resume it."""
    from forge.session.registry import SESSION_REGISTRY

    branch = args.get("branch", "")
    message = args.get("message", "")

    if not branch:
        return {"success": False, "error": "Branch name is required"}
    if not message:
        return {"success": False, "error": "Message is required"}

    repo = ctx.repo
    parent_branch = ctx.branch_name

    # Check if branch exists
    if branch not in repo.repo.branches:
        return {"success": False, "error": f"Branch '{branch}' does not exist"}

    # Check registry first - it's the source of truth for live sessions
    live_runner = SESSION_REGISTRY.get(branch)

    if live_runner is not None:
        # Live runner exists - check its state
        if live_runner._parent_session != parent_branch:
            return {
                "success": False,
                "error": f"Branch '{branch}' is not a child of current session",
            }

        from forge.session.runner import SessionState

        if live_runner.state == SessionState.RUNNING:
            return {
                "success": False,
                "error": "Child session is already running",
            }

        # Live runner exists and is not running - we can resume it directly
        # The _start_session flag will trigger SessionRunner to call send_message on it
        return {
            "success": True,
            "branch": branch,
            "state": "running",
            "message": f"Resumed child session '{branch}'. Use wait_session to check for completion.",
            "_start_session": branch,
            "_start_message": message,
        }

    # No live runner - session needs to be loaded first
    # The _start_session flag tells SessionRunner to load and start it
    return {
        "success": True,
        "branch": branch,
        "state": "running",
        "message": f"Starting child session '{branch}'. Use wait_session to check for completion.",
        "_start_session": branch,
        "_start_message": message,
    }
