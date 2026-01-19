"""
Inspect git history including commits, merges, and diffs.
Useful for understanding recent changes and debugging.
"""

from typing import TYPE_CHECKING, Any

import pygit2

if TYPE_CHECKING:
    from forge.tools.context import ToolContext


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "function": {
            "name": "git_history",
            "description": "Inspect git history. Shows commits with author, date, message, and optionally diffs. Handles merges and shows the commit graph structure.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of commits to show (default: 10, max: 50)",
                    },
                    "base": {
                        "type": "string",
                        "description": "Starting point - branch name, commit SHA, or 'HEAD' (default: current branch)",
                    },
                    "show_diff": {
                        "type": "string",
                        "description": "Show diff for a specific commit SHA. When provided, only shows that commit with its full diff.",
                    },
                },
                "required": [],
            },
        },
    }


def execute(ctx: "ToolContext", args: dict[str, Any]) -> dict[str, Any]:
    """Inspect git history"""
    limit = args.get("limit", 10)
    base = args.get("base")
    show_diff = args.get("show_diff")

    # Validate limit
    if not isinstance(limit, int) or limit < 1:
        return {"success": False, "error": "limit must be a positive integer"}
    limit = min(limit, 50)  # Cap at 50

    repo = ctx.repo.repo  # pygit2.Repository

    # If showing diff for a specific commit
    if show_diff:
        return _show_commit_diff(repo, show_diff)

    # Resolve base to a commit
    try:
        start_commit = _resolve_ref(repo, base, ctx.branch_name)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    # Walk history
    commits = []
    walker = repo.walk(start_commit.id, pygit2.GIT_SORT_TOPOLOGICAL | pygit2.GIT_SORT_TIME)  # type: ignore[arg-type]

    for i, commit in enumerate(walker):
        if i >= limit:
            break

        commit_info = _format_commit(commit)
        commits.append(commit_info)

    # Format output
    output_lines = []
    for c in commits:
        # Commit header
        merge_marker = " (merge)" if c["is_merge"] else ""
        output_lines.append(f"commit {c['sha']}{merge_marker}")
        output_lines.append(f"Author: {c['author']}")
        output_lines.append(f"Date:   {c['date']}")
        if c["is_merge"]:
            output_lines.append(f"Merge:  {' '.join(c['parents'])}")
        output_lines.append("")
        # Indent message
        for line in c["message"].split("\n"):
            output_lines.append(f"    {line}")
        output_lines.append("")

    return {
        "success": True,
        "commit_count": len(commits),
        "base": str(start_commit.id)[:12],
        "history": "\n".join(output_lines),
    }


def _resolve_ref(repo: pygit2.Repository, ref: str | None, default_branch: str) -> pygit2.Commit:
    """Resolve a reference string to a commit"""
    if ref is None:
        # Use current branch
        ref = default_branch

    # Try as branch name first
    if ref in repo.branches:
        branch = repo.branches[ref]
        return branch.peel(pygit2.Commit)

    # Try as commit SHA
    if len(ref) >= 4:
        try:
            # Try full or partial SHA
            obj = repo.revparse_single(ref)
            if isinstance(obj, pygit2.Commit):
                return obj
            # Could be a tag pointing to a commit
            return obj.peel(pygit2.Commit)
        except (KeyError, ValueError):
            pass

    raise ValueError(f"Could not resolve '{ref}' to a commit")


def _format_commit(commit: pygit2.Commit) -> dict[str, Any]:
    """Format a commit for display"""
    from datetime import datetime, timezone

    # Convert timestamp to readable date
    dt = datetime.fromtimestamp(commit.commit_time, tz=timezone.utc)
    date_str = dt.strftime("%Y-%m-%d %H:%M:%S %z")

    return {
        "sha": str(commit.id)[:12],
        "author": f"{commit.author.name} <{commit.author.email}>",
        "date": date_str,
        "message": commit.message.strip(),
        "is_merge": len(commit.parents) > 1,
        "parents": [str(p.id)[:12] for p in commit.parents],
    }


def _show_commit_diff(repo: pygit2.Repository, commit_sha: str) -> dict[str, Any]:
    """Show the diff for a specific commit"""
    # Resolve commit
    try:
        obj = repo.revparse_single(commit_sha)
        commit: pygit2.Commit = obj if isinstance(obj, pygit2.Commit) else obj.peel(pygit2.Commit)
    except (KeyError, ValueError, pygit2.GitError):
        return {"success": False, "error": f"Could not find commit: {commit_sha}"}

    commit_info = _format_commit(commit)

    # Get diff
    if commit.parents:
        # Diff against first parent
        parent = commit.parents[0]
        diff = repo.diff(parent, commit)
    else:
        # Initial commit - diff against empty tree
        diff = commit.tree.diff_to_tree(swap=True)

    # Format diff output
    diff_lines = []
    for patch in diff:
        file_path = patch.delta.new_file.path or patch.delta.old_file.path

        # Skip session.json - it's noise in diffs
        if file_path == ".forge/session.json":
            continue

        # File header
        if patch.delta.status == pygit2.GIT_DELTA_ADDED:
            diff_lines.append("--- /dev/null")
            diff_lines.append(f"+++ b/{file_path}")
        elif patch.delta.status == pygit2.GIT_DELTA_DELETED:
            diff_lines.append(f"--- a/{file_path}")
            diff_lines.append("+++ /dev/null")
        else:
            diff_lines.append(f"--- a/{file_path}")
            diff_lines.append(f"+++ b/{file_path}")

        # Hunks
        for hunk in patch.hunks:
            diff_lines.append(hunk.header.rstrip())
            for line in hunk.lines:
                origin = line.origin
                if origin in ("+", "-", " "):
                    diff_lines.append(f"{origin}{line.content.rstrip()}")

        diff_lines.append("")

    # Build output
    output_lines = [
        f"commit {commit_info['sha']}",
        f"Author: {commit_info['author']}",
        f"Date:   {commit_info['date']}",
    ]
    if commit_info["is_merge"]:
        output_lines.append(f"Merge:  {' '.join(commit_info['parents'])}")
    output_lines.append("")
    for line in commit_info["message"].split("\n"):
        output_lines.append(f"    {line}")
    output_lines.append("")
    output_lines.append("---")
    output_lines.append("")
    output_lines.extend(diff_lines)

    return {
        "success": True,
        "commit": commit_info["sha"],
        "is_merge": commit_info["is_merge"],
        "diff": "\n".join(output_lines),
    }
