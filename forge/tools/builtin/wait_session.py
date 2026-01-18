"""
wait_session tool - Wait for one of the specified child sessions to complete or ask a question.

This tool checks the state of child sessions. If any has completed or is waiting
for input, it returns that information. If all are still running, the current
session yields and waits.

Uses Tool API v2 (ToolContext) for clean access to repo and branch_name.
"""

from typing import TYPE_CHECKING, Any

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
        "type": "function",
        "function": {
            "name": "wait_session",
            "description": (
                "Wait for one of the specified child sessions to complete or ask a question. "
                "Returns immediately if any child is ready, otherwise the current session "
                "yields until a child reaches a stopping point. The returned message is the "
                "child's done() output - use this to evaluate completion or answer questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "branches": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Array of child session branch names to wait on. "
                            'Example: ["ai/my-task", "ai/other-task"]. '
                            "Returns when ANY of them is ready."
                        ),
                    },
                },
                "required": ["branches"],
            },
        },
    }


def execute(ctx: "ToolContext", args: dict[str, Any]) -> dict[str, Any]:
    """Check child sessions and wait if needed."""
    from forge.session.live_session import SessionState
    from forge.session.registry import SESSION_REGISTRY

    branches = args.get("branches", [])

    if not branches:
        return {"success": False, "error": "At least one branch is required"}

    # Validate branches is actually a list, not a stringified JSON
    if isinstance(branches, str):
        return {
            "success": False,
            "error": (
                f"'branches' must be an array, not a string. "
                f'Got: {branches!r}. Use ["branch-name"] not "[\\"branch-name\\"]"'
            ),
        }

    repo = ctx.repo
    parent_branch = ctx.branch_name

    # Check each child's state
    ready_children = []
    running_children = []

    for branch in branches:
        if branch not in repo.repo.branches:
            return {"success": False, "error": f"Branch '{branch}' does not exist"}

        # Get the child session - it should be loaded if it's our child
        child = SESSION_REGISTRY.get(branch)

        if child is None:
            # Not loaded - try to get info from disk for display
            info = SESSION_REGISTRY.get_session_display_info(branch, repo)
            if info is None:
                return {
                    "success": False,
                    "error": f"Branch '{branch}' is not a session (no .forge/session.json)",
                }

            # Verify this is our child
            if info.get("parent_session") != parent_branch:
                return {
                    "success": False,
                    "error": f"Branch '{branch}' is not a child of current session",
                }

            state = info.get("state", "idle")
            yield_message = info.get("yield_message")
            last_response = None  # Can't get this without loading
        else:
            # Loaded - use live state
            state = child.state
            yield_message = child._yield_message

            # Verify this is our child
            if child.parent_session != parent_branch:
                return {
                    "success": False,
                    "error": f"Branch '{branch}' is not a child of current session",
                }

            # Get the child's last assistant message
            last_response = None
            for msg in reversed(child.messages):
                if msg.get("role") == "assistant" and not msg.get("_ui_only"):
                    last_response = msg.get("content", "")
                    break

        task = ""  # Task tracking removed for simplicity

        if state in (
            SessionState.COMPLETED,
            SessionState.WAITING_INPUT,
            SessionState.WAITING_CHILDREN,
            SessionState.IDLE,
        ):
            # idle means the turn finished - child is ready
            merge_clean = _check_merge_clean(repo, parent_branch, branch)

            ready_children.append(
                {
                    "branch": branch,
                    "state": state,
                    "message": yield_message or "Task completed",
                    "last_response": last_response,
                    "task": task,
                    "merge_clean": merge_clean,
                }
            )
        elif state == SessionState.RUNNING:
            running_children.append(branch)
        elif state == SessionState.ERROR:
            ready_children.append(
                {
                    "branch": branch,
                    "state": "error",
                    "message": yield_message or "Unknown error",
                    "task": task,
                    "merge_clean": False,
                }
            )

    # If any child is ready, return immediately
    if ready_children:
        child = ready_children[0]  # Return first ready child
        return {
            "success": True,
            "branch": child["branch"],
            "state": child["state"],
            "message": child["message"],
            "last_response": child.get("last_response"),
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
            # Signal to LiveSession to yield
            "_yield": True,
            "_yield_message": f"Waiting on child sessions: {', '.join(running_children)}",
            "side_effects": [SideEffect.MID_TURN_COMMIT],  # Force commit before yield
        }

    # No children found (shouldn't happen given earlier checks)
    return {"success": False, "error": "No valid child sessions found"}