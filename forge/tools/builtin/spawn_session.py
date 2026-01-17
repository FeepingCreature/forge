"""
spawn_session tool - Create a child AI session on a new branch.

This tool forks the current branch, creates a new session, and immediately
starts it running with the given instruction. The child works independently
and the parent can check on it later with wait_session.

Uses Tool API v2 (ToolContext) for clean access to repo and branch_name.
"""

import json
from typing import TYPE_CHECKING, Any

import pygit2

from forge.constants import SESSION_FILE

if TYPE_CHECKING:
    from forge.tools.context import ToolContext


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM."""
    return {
        "name": "spawn_session",
        "description": (
            "Create a child AI session on a new branch and start it immediately. "
            "The child works independently on the given instruction. Use wait_session "
            "later to check on progress or get results. The child inherits the current "
            "codebase state and can make its own commits.\n\n"
            "IMPORTANT: The child session has NO context from the parent - it starts fresh "
            "with only the instruction you provide. Be very detailed and explicit in your "
            "instructions, including: what files to look at, what problem to solve, what "
            "approach to take, and what the expected outcome is."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "branch_name": {
                    "type": "string",
                    "description": (
                        "Branch name for the child session (e.g., 'ai/fix-login-bug'). "
                        "Use 'ai/' prefix by convention."
                    ),
                },
                "instruction": {
                    "type": "string",
                    "description": (
                        "Detailed instruction for the child session. Be explicit - include "
                        "relevant file paths, the specific problem/task, context the child "
                        "needs to understand, and what 'done' looks like. The child cannot "
                        "see your conversation history."
                    ),
                },
            },
            "required": ["branch_name", "instruction"],
        },
    }


def execute(ctx: "ToolContext", args: dict[str, Any]) -> dict[str, Any]:
    """Create a child session branch and start it."""
    branch_name = args.get("branch_name", "")
    instruction = args.get("instruction", "")

    if not branch_name:
        return {"success": False, "error": "branch_name is required"}
    if not instruction:
        return {"success": False, "error": "instruction is required"}

    # Get the repository and current branch from context
    repo = ctx.repo
    parent_branch = ctx.branch_name

    try:
        # Get current branch HEAD
        parent_ref = repo.repo.branches.get(parent_branch)
        if parent_ref is None:
            return {"success": False, "error": f"Parent branch '{parent_branch}' not found"}  # type: ignore[unreachable]

        parent_commit = parent_ref.peel(pygit2.Commit)

        # Check if branch already exists
        if branch_name in repo.repo.branches.local:
            return {"success": False, "error": f"Branch '{branch_name}' already exists"}

        # Create the new branch
        repo.repo.branches.create(branch_name, parent_commit)

        # Read current session to get parent info
        try:
            current_session_content = ctx.read_file(SESSION_FILE)
            current_session = json.loads(current_session_content)
        except (FileNotFoundError, json.JSONDecodeError):
            current_session = {}

        # Update current session's child list
        child_sessions = current_session.get("child_sessions", [])
        if branch_name not in child_sessions:
            child_sessions.append(branch_name)
        current_session["child_sessions"] = child_sessions

        # Write back to current session (will be committed with parent's next commit)
        ctx.write_file(SESSION_FILE, json.dumps(current_session, indent=2))

        # Create initial session for child branch using context helper
        child_vfs = ctx.get_branch_vfs(branch_name)
        child_session: dict[str, Any] = {
            "messages": [],
            "active_files": [],
            "parent_session": parent_branch,
            "child_sessions": [],
            "state": "idle",
            "yield_message": None,
        }
        child_vfs.write_file(SESSION_FILE, json.dumps(child_session, indent=2))
        child_vfs.commit(f"Initialize child session: {branch_name}")

        return {
            "success": True,
            "branch": branch_name,
            "message": (
                f"Started child session on branch '{branch_name}'. "
                f"Use wait_session(['{branch_name}']) to check on progress or get results."
            ),
            # Flag for SessionRunner to track this child and start it
            "_spawned_child": branch_name,
            "_start_session": branch_name,
            "_start_message": instruction,
        }

    except Exception as e:
        return {"success": False, "error": str(e)}
