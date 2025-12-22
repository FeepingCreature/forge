"""
Commit pending VFS changes mid-turn with a descriptive message.
"""

from typing import TYPE_CHECKING, Any

from forge.git_backend.commit_types import CommitType

if TYPE_CHECKING:
    from forge.vfs.work_in_progress import WorkInProgressVFS


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "function": {
            "name": "commit",
            "description": "Commit pending changes mid-turn with a descriptive message. Use this to create atomic commits for each logical change rather than one big commit at the end. After commit, you can continue making more changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Commit message describing the change (e.g., 'Add cancel button to AI chat')",
                    },
                },
                "required": ["message"],
            },
        },
    }


def execute(vfs: "WorkInProgressVFS", args: dict[str, Any]) -> dict[str, Any]:
    """Commit pending changes and reset VFS for more work"""
    message = args.get("message")

    if not isinstance(message, str) or not message.strip():
        return {"success": False, "error": "message must be a non-empty string"}

    # Check if there are changes to commit
    pending = vfs.get_pending_changes()
    deleted = vfs.get_deleted_files()

    if not pending and not deleted:
        return {"success": False, "error": "No pending changes to commit"}

    # Build summary of what's being committed
    summary_parts = []
    if pending:
        summary_parts.append(f"{len(pending)} file(s) modified/created")
    if deleted:
        summary_parts.append(f"{len(deleted)} file(s) deleted")
    summary = ", ".join(summary_parts)

    # Commit with MAJOR type (these are intentional atomic commits)
    commit_oid = vfs.commit(
        message=message.strip(),
        commit_type=CommitType.MAJOR,
    )

    # Update base_vfs to point to new commit so subsequent reads see committed state
    from forge.vfs.git_commit import GitCommitVFS

    new_commit = vfs.repo.get_branch_head(vfs.branch_name)
    vfs.base_vfs = GitCommitVFS(vfs.repo.repo, new_commit)

    return {
        "success": True,
        "message": f"Committed: {summary}",
        "commit": commit_oid[:12],
    }
