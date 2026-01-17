"""
merge_session tool - Merge a completed child session back into the current branch.

This tool performs a git merge of the child branch into the current branch,
incorporating all the child's changes. After a successful merge, the child
branch can optionally be deleted.
"""

import json
from typing import Any

import pygit2

from forge.constants import SESSION_FILE


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM."""
    return {
        "name": "merge_session",
        "description": (
            "Merge a completed child session's changes into the current branch. "
            "This performs a git merge. If there are conflicts, they are left as "
            "conflict markers that you can resolve with the edit tool. After merge, "
            "the child is removed from your child_sessions list."
        ),
        "input_schema": {
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
            },
            "required": ["branch"],
        },
    }


def execute(vfs: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Merge child session into current branch."""
    branch = args.get("branch", "")
    delete_branch = args.get("delete_branch", True)
    
    if not branch:
        return {"success": False, "error": "Branch name is required"}
    
    repo = vfs._repo
    parent_branch = vfs._branch_name
    
    # Check if branch exists
    if branch not in repo.repo.branches:
        return {"success": False, "error": f"Branch '{branch}' does not exist"}
    
    try:
        # Verify this is our child
        from forge.vfs.work_in_progress import WorkInProgressVFS
        
        child_vfs = WorkInProgressVFS(repo, branch)
        try:
            session_content = child_vfs.read_file(SESSION_FILE)
            session_data = json.loads(session_content)
        except (FileNotFoundError, json.JSONDecodeError):
            session_data = {}
        
        if session_data.get("parent_session") != parent_branch:
            return {
                "success": False,
                "error": f"Branch '{branch}' is not a child of current session",
            }
        
        # Check child state
        child_state = session_data.get("state", "idle")
        if child_state not in ("completed", "waiting_input"):
            return {
                "success": False,
                "error": f"Child session is not ready for merge (state: {child_state})",
            }
        
        # Get branch references
        parent_ref = repo.repo.branches.get(parent_branch)
        child_ref = repo.repo.branches.get(branch)
        
        if parent_ref is None or child_ref is None:
            return {"success": False, "error": "Could not find branch references"}
        
        parent_commit = parent_ref.peel(pygit2.Commit)
        child_commit = child_ref.peel(pygit2.Commit)
        
        # Perform the merge
        merge_result = repo.repo.merge_analysis(child_commit.id)
        
        if merge_result[0] & pygit2.GIT_MERGE_ANALYSIS_UP_TO_DATE:
            # Already up to date
            result_msg = "Already up to date, no merge needed"
            conflicts = []
        elif merge_result[0] & pygit2.GIT_MERGE_ANALYSIS_FASTFORWARD:
            # Fast-forward merge
            parent_ref.set_target(child_commit.id)
            result_msg = f"Fast-forward merge to {str(child_commit.id)[:8]}"
            conflicts = []
        else:
            # Regular merge needed
            repo.repo.merge(child_commit.id)
            
            # Check for conflicts
            if repo.repo.index.conflicts:
                conflicts = [path for path, _ in repo.repo.index.conflicts]
                result_msg = f"Merge has conflicts in {len(conflicts)} file(s)"
                
                # Write conflict markers to files so AI can resolve them
                for conflict_entry in repo.repo.index.conflicts:
                    if conflict_entry is None:
                        continue
                    # conflict_entry is (ancestor, ours, theirs)
                    ancestor, ours, theirs = conflict_entry
                    if ours and theirs:
                        path = ours.path
                        # Read both versions
                        ours_blob = repo.repo.get(ours.id)
                        theirs_blob = repo.repo.get(theirs.id)
                        
                        if ours_blob and theirs_blob:
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
                            vfs.write_file(path, conflict_content)
            else:
                # No conflicts - create merge commit
                tree = repo.repo.index.write_tree()
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
            our_session_content = vfs.read_file(SESSION_FILE)
            our_session = json.loads(our_session_content)
        except (FileNotFoundError, json.JSONDecodeError):
            our_session = {}
        
        child_sessions = our_session.get("child_sessions", [])
        if branch in child_sessions:
            child_sessions.remove(branch)
        our_session["child_sessions"] = child_sessions
        vfs.write_file(SESSION_FILE, json.dumps(our_session, indent=2))
        
        # Delete child branch if requested and no conflicts
        if delete_branch and not conflicts:
            try:
                repo.repo.branches.delete(branch)
                result_msg += f". Deleted branch '{branch}'"
            except Exception as e:
                result_msg += f". Could not delete branch: {e}"
        
        return {
            "success": len(conflicts) == 0,
            "message": result_msg,
            "conflicts": conflicts,
            "merged": len(conflicts) == 0,
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}