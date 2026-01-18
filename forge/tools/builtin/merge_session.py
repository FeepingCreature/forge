"""
merge_session tool - Merge a completed child session back into the current branch.

This tool performs a git merge of the child branch into the current branch,
incorporating all the child's changes. After a successful merge, the child
branch can optionally be deleted.

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
        "type": "function",
        "function": {
            "name": "merge_session",
            "description": (
                "Merge a completed child session's changes into the current branch. "
                "This performs a git merge. If there are conflicts, they are left as "
                "conflict markers that you can resolve with the edit tool. After merge, "
                "the child is removed from your child_sessions list."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "branch": {
                        "type": "string",
                        "description": "Branch name of the child session to merge.",
                    },
                    "delete_branch": {
                        "type": "boolean",
                        "description": "Whether to delete the child branch after merging. Default: true",
                        "default": True,
                    },
                    "allow_conflicts": {
                        "type": "boolean",
                        "description": (
                            "If true, commit even with conflicts (using <<<>>> markers). "
                            "If false (default), report conflicts without committing."
                        ),
                        "default": False,
                    },
                },
                "required": ["branch"],
            },
        },
    }


def execute(ctx: "ToolContext", args: dict[str, Any]) -> dict[str, Any]:
    """Merge child session into current branch."""
    from forge.session.registry import SESSION_REGISTRY

    branch = args.get("branch", "")
    delete_branch = args.get("delete_branch", True)
    allow_conflicts = args.get("allow_conflicts", False)

    if not branch:
        return {"success": False, "error": "Branch name is required"}

    repo = ctx.repo
    parent_branch = ctx.branch_name

    # Check if branch exists
    if branch not in repo.repo.branches.local:
        return {"success": False, "error": f"Branch '{branch}' does not exist"}

    try:
        # Check registry for live state (source of truth for running sessions)
        live_runner = SESSION_REGISTRY.get(branch)

        if live_runner is not None:
            # Live runner - use its state directly
            child_state = str(live_runner.state)
            parent_session = live_runner._parent_session
            print(f"🔍 merge_session: {branch} LIVE state={child_state}")
        else:
            # No live runner - read from session.json
            child_vfs = ctx.get_branch_vfs(branch)
            try:
                session_content = child_vfs.read_file(SESSION_FILE)
                session_data = json.loads(session_content)
            except (FileNotFoundError, json.JSONDecodeError):
                session_data = {}
            child_state = session_data.get("state", "idle")
            parent_session = session_data.get("parent_session")
            print(f"🔍 merge_session: {branch} PERSISTED state={child_state}")

        if parent_session != parent_branch:
            return {
                "success": False,
                "error": f"Branch '{branch}' is not a child of current session",
            }

        # Check child state - idle means turn finished, so it's ready
        if child_state not in ("completed", "waiting_input", "idle"):
            return {
                "success": False,
                "error": f"Child session is not ready for merge (state: {child_state})",
            }

        # Get branch references
        parent_ref = repo.repo.branches.get(parent_branch)
        child_ref = repo.repo.branches.get(branch)

        if parent_ref is None or child_ref is None:
            return {"success": False, "error": "Could not find branch references"}  # type: ignore[unreachable]

        parent_commit = parent_ref.peel(pygit2.Commit)
        child_commit = child_ref.peel(pygit2.Commit)

        # Perform the merge analysis
        merge_result = repo.repo.merge_analysis(child_commit.id)

        conflicts: list[str] = []

        if merge_result[0] & pygit2.GIT_MERGE_ANALYSIS_UP_TO_DATE:
            # Already up to date
            result_msg = "Already up to date, no merge needed"
        elif merge_result[0] & pygit2.GIT_MERGE_ANALYSIS_FASTFORWARD:
            # Fast-forward merge
            parent_ref.set_target(child_commit.id)
            result_msg = f"Fast-forward merge to {str(child_commit.id)[:8]}"
        else:
            # Regular merge needed - do it in memory to avoid workdir conflicts
            # Find merge base
            merge_base_oid = repo.repo.merge_base(parent_commit.id, child_commit.id)
            if not merge_base_oid:
                return {"success": False, "error": "No common ancestor found - cannot merge"}

            base_commit = repo.repo.get(merge_base_oid)
            if base_commit is None:
                return {"success": False, "error": "Could not load merge base commit"}
            base_tree = base_commit.peel(pygit2.Tree)

            # Do in-memory three-way merge
            merge_index = repo.repo.merge_trees(base_tree, parent_commit.tree, child_commit.tree)

            # Check for conflicts
            if merge_index.conflicts:
                # index.conflicts iterates as (ancestor, ours, theirs) tuples
                conflict_paths: list[str] = []
                resolved_paths: list[str] = []

                for _ancestor, ours, theirs in merge_index.conflicts:
                    if ours and theirs:
                        conflict_path = ours.path

                        # Auto-resolve session.json conflicts - keep parent's version
                        # Each branch has its own session state, we don't want to merge them
                        if conflict_path == SESSION_FILE:
                            # Keep ours (parent's session), resolve the conflict
                            resolved_paths.append(conflict_path)
                            # The index will be updated below when we write the tree
                            continue

                        conflict_paths.append(conflict_path)

                        # Read both versions
                        ours_obj = repo.repo.get(ours.id)
                        theirs_obj = repo.repo.get(theirs.id)

                        if ours_obj and theirs_obj:
                            ours_blob = ours_obj.peel(pygit2.Blob)
                            theirs_blob = theirs_obj.peel(pygit2.Blob)
                            ours_content = ours_blob.data.decode("utf-8", errors="replace")
                            theirs_content = theirs_blob.data.decode("utf-8", errors="replace")

                            # Write conflict markers (simple version)
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
                        # Commit with conflict markers - AI will resolve them
                        # Add the conflicted files to the in-memory merge index
                        for conflict_path in conflict_paths:
                            # Read the conflict content we wrote to VFS
                            conflict_content = ctx.vfs.pending_changes.get(conflict_path, "")
                            if conflict_content:
                                # Create blob and add to merge index
                                blob_id = repo.repo.create_blob(conflict_content.encode("utf-8"))
                                merge_index.add(
                                    pygit2.IndexEntry(
                                        conflict_path, blob_id, pygit2.enums.FileMode.BLOB
                                    )
                                )

                        # Also add session.json from parent (auto-resolve)
                        for path in resolved_paths:
                            try:
                                entry = parent_commit.tree[path]
                                merge_index.add(pygit2.IndexEntry(path, entry.id, entry.filemode))
                            except KeyError:
                                pass

                        # Create merge commit with conflicts from in-memory index
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
                        result_msg = (
                            f"Merged with {len(conflicts)} conflict(s) - resolve the <<<>>> markers"
                        )
                        # Don't delete branch when there are conflicts
                        delete_branch = False
                    else:
                        result_msg = f"Merge has conflicts in {len(conflicts)} file(s): {', '.join(conflicts)}"
                elif resolved_paths:
                    # All conflicts were auto-resolved (just session.json)
                    # Add parent's version to merge index
                    for path in resolved_paths:
                        try:
                            entry = parent_commit.tree[path]
                            merge_index.add(pygit2.IndexEntry(path, entry.id, entry.filemode))
                        except KeyError:
                            pass

                    # Write tree and create merge commit from in-memory index
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
                    result_msg = f"Merged child session '{branch}' (auto-resolved session.json)"
            else:
                # No conflicts - create merge commit from in-memory index
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

        # Delete child branch if requested and no conflicts
        if delete_branch and not conflicts:
            try:
                repo.repo.branches.delete(branch)
                result_msg += f". Deleted branch '{branch}'"
            except Exception as e:
                result_msg += f". Could not delete branch: {e}"

        # Success if no conflicts, OR if conflicts were allowed and committed
        success = len(conflicts) == 0 or (allow_conflicts and len(conflicts) > 0)
        merged = success

        return {
            "success": success,
            "message": result_msg,
            "conflicts": conflicts,
            "merged": merged,
            "conflicts_committed": allow_conflicts and len(conflicts) > 0,
        }

    except Exception as e:
        return {"success": False, "error": str(e)}
