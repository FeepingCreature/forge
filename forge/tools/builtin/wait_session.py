"""
wait_session tool - Wait for one of the specified child sessions to complete or ask a question.

This tool checks the state of child sessions. If any has completed or is waiting
for input, it returns that information. If all are still running, the current
session yields and waits.

Uses Tool API v2 (ToolContext) for clean access to repo and branch_name.
"""

import json
from typing import TYPE_CHECKING, Any

from forge.constants import SESSION_FILE
from forge.tools.side_effects import SideEffect

if TYPE_CHECKING:
    from forge.git_backend.repository import ForgeRepository
    from forge.tools.context import ToolContext


def _check_merge_clean(repo: "ForgeRepository", parent_branch: str, child_branch: str) -> bool:
    """Check if merging child into parent would be clean (no conflicts)."""
    import pygit2

    parent_ref = repo.repo.branches.get(parent_branch)
    child_ref = repo.repo.branches.get(child_branch)

    if parent_ref is None or child_ref is None:
        return False  # type: ignore[unreachable]

    parent_commit = parent_ref.peel(pygit2.Commit)
    child_commit = child_ref.peel(pygit2.Commit)

    # Check merge analysis
    merge_result = repo.repo.merge_analysis(child_commit.id)

    if merge_result[0] & pygit2.GIT_MERGE_ANALYSIS_UP_TO_DATE:
        return True  # Already up to date
    if merge_result[0] & pygit2.GIT_MERGE_ANALYSIS_FASTFORWARD:
        return True  # Fast-forward possible

    # Need to actually try the merge to check for conflicts
    # We do this in memory without committing
    try:
        base_oid = repo.repo.merge_base(parent_commit.id, child_commit.id)
        base_commit = repo.repo.get(base_oid)
        if base_commit is None:
            return False
        merge_index = repo.repo.merge_trees(
            base_commit.peel(pygit2.Tree),
            parent_commit.tree,
            child_commit.tree,
        )
        # Check for conflicts, excluding session.json (auto-resolved)
        if merge_index.conflicts:
            for _ancestor, ours, theirs in merge_index.conflicts:
                entry = ours or theirs
                if entry is not None:
                    path = entry.path
                    if path != ".forge/session.json":
                        return False  # Real conflict
        return True  # No conflicts (or only session.json)
    except Exception:
        return False


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM."""
    return {
        "name": "wait_session",
        "description": (
            "Wait for one of the specified child sessions to complete or ask a question. "
            "Returns immediately if any child is ready, otherwise the current session "
            "yields until a child reaches a stopping point. The returned message is the "
            "child's done() output - use this to evaluate completion or answer questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "branches": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of child session branch names to wait on. "
                        "Returns when ANY of them is ready."
                    ),
                },
            },
            "required": ["branches"],
        },
    }


def execute(ctx: "ToolContext", args: dict[str, Any]) -> dict[str, Any]:
    """Check child sessions and wait if needed."""
    branches = args.get("branches", [])

    if not branches:
        return {"success": False, "error": "At least one branch is required"}

    repo = ctx.repo
    parent_branch = ctx.branch_name

    # Check each child's state
    ready_children = []
    running_children = []

    for branch in branches:
        if branch not in repo.repo.branches:
            return {"success": False, "error": f"Branch '{branch}' does not exist"}

        try:
            child_vfs = ctx.get_branch_vfs(branch)
            session_content = child_vfs.read_file(SESSION_FILE)
            session_data = json.loads(session_content)

            # Verify this is our child
            if session_data.get("parent_session") != parent_branch:
                return {
                    "success": False,
                    "error": f"Branch '{branch}' is not a child of current session",
                }

            state = session_data.get("state", "idle")
            yield_message = session_data.get("yield_message")

            if state in ("completed", "waiting_input", "waiting_children"):
                # Check if merge would be clean
                merge_clean = _check_merge_clean(repo, parent_branch, branch)

                ready_children.append(
                    {
                        "branch": branch,
                        "state": state,
                        "message": yield_message,
                        "task": session_data.get("task", ""),
                        "merge_clean": merge_clean,
                    }
                )
            elif state == "running":
                running_children.append(branch)
            elif state == "idle":
                # Child hasn't been started yet
                return {
                    "success": False,
                    "error": f"Child session '{branch}' hasn't been started. Use resume_session first.",
                }
            elif state == "error":
                ready_children.append(
                    {
                        "branch": branch,
                        "state": "error",
                        "message": yield_message or "Unknown error",
                        "task": session_data.get("task", ""),
                    }
                )

        except (FileNotFoundError, json.JSONDecodeError) as e:
            return {"success": False, "error": f"Error reading session for '{branch}': {e}"}

    # If any child is ready, return immediately
    if ready_children:
        child = ready_children[0]  # Return first ready child
        return {
            "success": True,
            "branch": child["branch"],
            "state": child["state"],
            "message": child["message"],
            "task": child["task"],
            "ready": True,
            "merge_clean": child["merge_clean"],
        }

    # All children still running - we need to yield
    if running_children:
        return {
            "success": True,
            "ready": False,
            "waiting_on": running_children,
            "message": "All child sessions still running. Current session will yield.",
            # Signal to SessionRunner to yield
            "_yield": True,
            "_yield_message": f"Waiting on child sessions: {', '.join(running_children)}",
            "side_effects": [SideEffect.MID_TURN_COMMIT],  # Force commit before yield
        }

    # No children found (shouldn't happen given earlier checks)
    return {"success": False, "error": "No valid child sessions found"}
