"""
session tool - Unified interface for managing child AI sessions.

Actions:
- spawn: Create a child session on a new branch and start it
- wait: Wait for child sessions to complete or ask questions
- resume: Send a message to a child and resume execution
- merge: Merge a completed child's changes into current branch

Use get_skill("session") for detailed documentation and examples.
"""

import json
from typing import TYPE_CHECKING, Any

import pygit2

from forge.constants import SESSION_FILE
from forge.tools.side_effects import SideEffect

if TYPE_CHECKING:
    from forge.tools.context import ToolContext


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM."""
    return {
        "type": "function",
        "function": {
            "name": "session",
            "description": (
                "Manage child AI sessions for parallel work. Actions: spawn (create child), "
                "wait (check progress), resume (send message), merge (incorporate changes). "
                "Use get_skill('session') for detailed documentation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["spawn", "wait", "resume", "merge"],
                        "description": "The session action to perform.",
                    },
                    "branch": {
                        "type": "string",
                        "description": "Branch name (spawn/resume/merge) or omit for wait.",
                    },
                    "branches": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Branch names to wait on (wait action only).",
                    },
                    "instruction": {
                        "type": "string",
                        "description": "Detailed task instruction (spawn action only).",
                    },
                    "message": {
                        "type": "string",
                        "description": "Message to send (resume action only).",
                    },
                    "delete_branch": {
                        "type": "boolean",
                        "description": "Delete branch after merge (default: true).",
                    },
                    "allow_conflicts": {
                        "type": "boolean",
                        "description": "Commit with conflict markers (default: false).",
                    },
                },
                "required": ["action"],
            },
        },
    }


def execute(ctx: "ToolContext", args: dict[str, Any]) -> dict[str, Any]:
    """Execute the session action."""
    action = args.get("action", "")

    if action == "spawn":
        return _spawn(ctx, args)
    elif action == "wait":
        return _wait(ctx, args)
    elif action == "resume":
        return _resume(ctx, args)
    elif action == "merge":
        return _merge(ctx, args)
    else:
        return {
            "success": False,
            "error": f"Unknown action: {action}. Use: spawn, wait, resume, merge",
        }


def _spawn(ctx: "ToolContext", args: dict[str, Any]) -> dict[str, Any]:
    """Create a child session branch and start it."""
    branch_name = args.get("branch", "")
    instruction = args.get("instruction", "")

    if not branch_name:
        return {"success": False, "error": "branch is required for spawn"}
    if not instruction:
        return {"success": False, "error": "instruction is required for spawn"}

    repo = ctx.repo
    parent_branch = ctx.branch_name

    try:
        # Get current branch HEAD
        parent_ref = repo.repo.branches.get(parent_branch)
        if not parent_ref:
            return {"success": False, "error": f"Parent branch '{parent_branch}' not found"}

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

        # Write back to current session
        ctx.write_file(SESSION_FILE, json.dumps(current_session, indent=2))

        # Create initial session for child branch
        child_vfs = ctx.get_branch_vfs(branch_name)
        child_session: dict[str, Any] = {
            "messages": [{"role": "user", "content": instruction}],
            "active_files": [],
            "parent_session": parent_branch,
            "child_sessions": [],
            "state": "running",
            "yield_message": None,
        }
        child_vfs.write_file(SESSION_FILE, json.dumps(child_session, indent=2))
        child_vfs.commit(f"Initialize child session: {branch_name}")

        return {
            "success": True,
            "branch": branch_name,
            "message": (
                f"Started child session on branch '{branch_name}'. "
                f"Use session(action='wait', branches=['{branch_name}']) to check progress."
            ),
            "_spawned_child": branch_name,
            "_start_session": branch_name,
            "_start_message": instruction,
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


def _check_merge_clean(ctx: "ToolContext", parent_branch: str, child_branch: str) -> bool:
    """Check if merging child into parent would be clean."""
    parent_ref = ctx.repo.repo.branches.get(parent_branch)
    child_ref = ctx.repo.repo.branches.get(child_branch)

    if not parent_ref or not child_ref:
        return False

    parent_commit = parent_ref.peel(pygit2.Commit)
    child_commit = child_ref.peel(pygit2.Commit)

    merge_result = ctx.repo.repo.merge_analysis(child_commit.id)

    if merge_result[0] & pygit2.GIT_MERGE_ANALYSIS_UP_TO_DATE:
        return True
    if merge_result[0] & pygit2.GIT_MERGE_ANALYSIS_FASTFORWARD:
        return True

    try:
        base_oid = ctx.repo.repo.merge_base(parent_commit.id, child_commit.id)
        base_commit = ctx.repo.repo.get(base_oid)
        if not base_commit:
            return False
        merge_index = ctx.repo.repo.merge_trees(
            base_commit.peel(pygit2.Tree),
            parent_commit.tree,
            child_commit.tree,
        )
        if merge_index.conflicts:
            for _ancestor, ours, theirs in merge_index.conflicts:
                entry = ours or theirs
                if entry is not None:
                    path = entry.path
                    if path != ".forge/session.json":
                        return False
        return True
    except Exception:
        return False


def _wait(ctx: "ToolContext", args: dict[str, Any]) -> dict[str, Any]:
    """Check child sessions and wait if needed."""
    from forge.session.live_session import SessionState
    from forge.session.registry import SESSION_REGISTRY

    branches = args.get("branches", [])

    if not branches:
        return {"success": False, "error": "branches array is required for wait"}

    if isinstance(branches, str):
        return {
            "success": False,
            "error": f"'branches' must be an array, not a string. Got: {branches!r}",
        }

    repo = ctx.repo
    parent_branch = ctx.branch_name

    ready_children = []
    running_children = []

    for branch in branches:
        if branch not in repo.repo.branches:
            return {"success": False, "error": f"Branch '{branch}' does not exist"}

        child = SESSION_REGISTRY.get(branch)

        if child is None:
            info = SESSION_REGISTRY.get_session_display_info(branch, repo)
            if info is None:
                return {
                    "success": False,
                    "error": f"Branch '{branch}' is not a session",
                }

            if info.get("parent_session") != parent_branch:
                return {
                    "success": False,
                    "error": f"Branch '{branch}' is not a child of current session",
                }

            state = info.get("state", "idle")
            yield_message = info.get("yield_message")
            last_response = None
        else:
            state = child.state
            yield_message = child._yield_message

            if child.parent_session != parent_branch:
                return {
                    "success": False,
                    "error": f"Branch '{branch}' is not a child of current session",
                }

            last_response = None
            for msg in reversed(child.messages):
                if msg.get("role") == "assistant" and not msg.get("_ui_only"):
                    last_response = msg.get("content", "")
                    break

        if state in (
            SessionState.COMPLETED,
            SessionState.WAITING_INPUT,
            SessionState.WAITING_CHILDREN,
            SessionState.IDLE,
        ):
            merge_clean = _check_merge_clean(ctx, parent_branch, branch)
            ready_children.append(
                {
                    "branch": branch,
                    "state": state,
                    "message": yield_message or "Task completed",
                    "last_response": last_response,
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
                    "merge_clean": False,
                }
            )

    if ready_children:
        ready_child = ready_children[0]
        return {
            "success": True,
            "branch": ready_child["branch"],
            "state": ready_child["state"],
            "message": ready_child["message"],
            "last_response": ready_child.get("last_response"),
            "ready": True,
            "merge_clean": ready_child["merge_clean"],
        }

    if running_children:
        return {
            "success": True,
            "ready": False,
            "waiting_on": running_children,
            "message": "All child sessions still running. Current session will yield.",
            "_yield": True,
            "_yield_message": f"Waiting on child sessions: {', '.join(running_children)}",
            "side_effects": [SideEffect.MID_TURN_COMMIT],
        }

    return {"success": False, "error": "No valid child sessions found"}


def _resume(ctx: "ToolContext", args: dict[str, Any]) -> dict[str, Any]:
    """Send message to child session and start/resume it."""
    from forge.session.live_session import SessionState
    from forge.session.registry import SESSION_REGISTRY

    branch = args.get("branch", "")
    message = args.get("message", "")

    if not branch:
        return {"success": False, "error": "branch is required for resume"}
    if not message:
        return {"success": False, "error": "message is required for resume"}

    repo = ctx.repo
    parent_branch = ctx.branch_name

    if branch not in repo.repo.branches:
        return {"success": False, "error": f"Branch '{branch}' does not exist"}

    child = SESSION_REGISTRY.get(branch)

    if child is None:
        info = SESSION_REGISTRY.get_session_display_info(branch, repo)
        if info is None:
            return {
                "success": False,
                "error": f"Branch '{branch}' is not a session",
            }

        if info.get("parent_session") != parent_branch:
            return {
                "success": False,
                "error": f"Branch '{branch}' is not a child of current session",
            }
    else:
        if child.parent_session != parent_branch:
            return {
                "success": False,
                "error": f"Branch '{branch}' is not a child of current session",
            }

        if child.state == SessionState.RUNNING:
            return {"success": False, "error": "Child session is already running"}

    return {
        "success": True,
        "branch": branch,
        "state": "running",
        "message": f"Resuming child session '{branch}'.",
        "_start_session": branch,
        "_start_message": message,
    }


def _merge(ctx: "ToolContext", args: dict[str, Any]) -> dict[str, Any]:
    """Merge child session into current branch."""
    from forge.session.live_session import SessionState
    from forge.session.registry import SESSION_REGISTRY

    branch = args.get("branch", "")
    delete_branch = args.get("delete_branch", True)
    allow_conflicts = args.get("allow_conflicts", False)

    if not branch:
        return {"success": False, "error": "branch is required for merge"}

    repo = ctx.repo
    parent_branch = ctx.branch_name

    if branch not in repo.repo.branches.local:
        return {"success": False, "error": f"Branch '{branch}' does not exist"}

    try:
        child = SESSION_REGISTRY.get(branch)

        if child is not None:
            child_state = child.state
            parent_session = child.parent_session
        else:
            info = SESSION_REGISTRY.get_session_display_info(branch, repo)
            if info is None:
                return {
                    "success": False,
                    "error": f"Branch '{branch}' is not a session",
                }
            child_state = info.get("state", "idle")
            parent_session = info.get("parent_session")

        if parent_session != parent_branch:
            return {
                "success": False,
                "error": f"Branch '{branch}' is not a child of current session",
            }

        if child_state not in (
            SessionState.COMPLETED,
            SessionState.WAITING_INPUT,
            SessionState.IDLE,
        ):
            return {
                "success": False,
                "error": f"Child session is not ready for merge (state: {child_state})",
            }

        parent_ref = repo.repo.branches.get(parent_branch)
        child_ref = repo.repo.branches.get(branch)

        if not parent_ref or not child_ref:
            return {"success": False, "error": "Could not find branch references"}

        parent_commit = parent_ref.peel(pygit2.Commit)
        child_commit = child_ref.peel(pygit2.Commit)

        merge_result = repo.repo.merge_analysis(child_commit.id)

        conflicts: list[str] = []

        if merge_result[0] & pygit2.GIT_MERGE_ANALYSIS_UP_TO_DATE:
            result_msg = "Already up to date, no merge needed"
        elif merge_result[0] & pygit2.GIT_MERGE_ANALYSIS_FASTFORWARD:
            parent_ref.set_target(child_commit.id)
            result_msg = f"Fast-forward merge to {str(child_commit.id)[:8]}"
        else:
            merge_base_oid = repo.repo.merge_base(parent_commit.id, child_commit.id)
            if not merge_base_oid:
                return {"success": False, "error": "No common ancestor found"}

            base_commit = repo.repo.get(merge_base_oid)
            if base_commit is None:
                return {"success": False, "error": "Could not load merge base commit"}
            base_tree = base_commit.peel(pygit2.Tree)

            merge_index = repo.repo.merge_trees(base_tree, parent_commit.tree, child_commit.tree)

            if merge_index.conflicts:
                conflict_paths: list[str] = []
                resolved_paths: list[str] = []

                for _ancestor, ours, theirs in merge_index.conflicts:
                    if ours and theirs:
                        conflict_path = ours.path

                        if conflict_path == SESSION_FILE:
                            resolved_paths.append(conflict_path)
                            continue

                        conflict_paths.append(conflict_path)

                        ours_obj = repo.repo.get(ours.id)
                        theirs_obj = repo.repo.get(theirs.id)

                        if ours_obj and theirs_obj:
                            ours_blob = ours_obj.peel(pygit2.Blob)
                            theirs_blob = theirs_obj.peel(pygit2.Blob)
                            ours_content = ours_blob.data.decode("utf-8", errors="replace")
                            theirs_content = theirs_blob.data.decode("utf-8", errors="replace")

                            conflict_content = (
                                f"<<<<<<< {parent_branch}\n"
                                f"{ours_content}"
                                f"=======\n"
                                f"{theirs_content}"
                                f">>>>>>> {branch}\n"
                            )
                            ctx.write_file(conflict_path, conflict_content)

                conflicts = conflict_paths

                if conflicts:
                    if allow_conflicts:
                        for conflict_path in conflict_paths:
                            conflict_content = ctx.vfs.pending_changes.get(conflict_path, "")
                            if conflict_content:
                                blob_id = repo.repo.create_blob(conflict_content.encode("utf-8"))
                                merge_index.add(
                                    pygit2.IndexEntry(
                                        conflict_path, blob_id, pygit2.enums.FileMode.BLOB
                                    )
                                )
                            del merge_index.conflicts[conflict_path]

                        for path in resolved_paths:
                            try:
                                entry = parent_commit.tree[path]
                                merge_index.add(pygit2.IndexEntry(path, entry.id, entry.filemode))
                            except KeyError:
                                pass
                            del merge_index.conflicts[path]

                        tree = merge_index.write_tree(repo.repo)
                        author = pygit2.Signature("Forge", "forge@local")
                        repo.repo.create_commit(
                            f"refs/heads/{parent_branch}",
                            author,
                            author,
                            f"Merge child session '{branch}' (with conflicts to resolve)",
                            tree,
                            [parent_commit.id, child_commit.id],
                        )
                        result_msg = f"Merged with {len(conflicts)} conflict(s) - resolve markers"
                        delete_branch = False
                    else:
                        result_msg = f"Merge has conflicts: {', '.join(conflicts)}"
                elif resolved_paths:
                    for path in resolved_paths:
                        try:
                            entry = parent_commit.tree[path]
                            merge_index.add(pygit2.IndexEntry(path, entry.id, entry.filemode))
                            del merge_index.conflicts[path]
                        except KeyError:
                            pass

                    tree = merge_index.write_tree(repo.repo)
                    author = pygit2.Signature("Forge", "forge@local")
                    repo.repo.create_commit(
                        f"refs/heads/{parent_branch}",
                        author,
                        author,
                        f"Merge child session '{branch}'",
                        tree,
                        [parent_commit.id, child_commit.id],
                    )
                    result_msg = f"Merged child session '{branch}'"
            else:
                tree = merge_index.write_tree(repo.repo)
                author = pygit2.Signature("Forge", "forge@local")
                repo.repo.create_commit(
                    f"refs/heads/{parent_branch}",
                    author,
                    author,
                    f"Merge child session '{branch}'",
                    tree,
                    [parent_commit.id, child_commit.id],
                )
                conflicts = []
                result_msg = f"Merged child session '{branch}'"

        # Update our session to remove child from list
        try:
            our_session_content = ctx.read_file(SESSION_FILE)
            our_session = json.loads(our_session_content)
        except (FileNotFoundError, json.JSONDecodeError):
            our_session = {}

        child_sessions = our_session.get("child_sessions", [])
        if branch in child_sessions:
            child_sessions.remove(branch)
        our_session["child_sessions"] = child_sessions
        ctx.write_file(SESSION_FILE, json.dumps(our_session, indent=2))

        if delete_branch and not conflicts:
            try:
                repo.repo.branches.delete(branch)
                result_msg += f". Deleted branch '{branch}'"
                SESSION_REGISTRY.remove_session(branch)
            except Exception as e:
                result_msg += f". Could not delete branch: {e}"

        success = len(conflicts) == 0 or (allow_conflicts and len(conflicts) > 0)

        return {
            "success": success,
            "message": result_msg,
            "conflicts": conflicts,
            "merged": success,
            "conflicts_committed": allow_conflicts and len(conflicts) > 0,
        }

    except Exception as e:
        return {"success": False, "error": str(e)}
