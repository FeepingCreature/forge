"""
spawn_session tool - Create a child AI session on a new branch.

This tool forks the current branch, creates a new session, and registers
it as a child of the current session. The child session doesn't start
running until resume_session is called.
"""

import json
from typing import Any

import pygit2

from forge.constants import SESSION_FILE


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM."""
    return {
        "name": "spawn_session",
        "description": (
            "Create a child AI session on a new branch. The child session starts idle - "
            "use resume_session to start it with an initial message. Use this to delegate "
            "subtasks to a separate AI session that can work independently."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "Brief description of the task for the child session. "
                        "This becomes part of the branch name."
                    ),
                },
                "branch_name": {
                    "type": "string",
                    "description": (
                        "Optional explicit branch name. If not provided, one is generated "
                        "from the task description."
                    ),
                },
            },
            "required": ["task"],
        },
    }


def execute(vfs: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Create a child session branch."""
    import re
    
    task = args.get("task", "")
    if not task:
        return {"success": False, "error": "Task description is required"}
    
    # Generate branch name if not provided
    branch_name = args.get("branch_name")
    if not branch_name:
        # Sanitize task for branch name
        sanitized = re.sub(r"[^a-zA-Z0-9]+", "-", task.lower())
        sanitized = sanitized.strip("-")[:30]
        branch_name = f"ai/{sanitized}"
    
    # Get the repository and current branch from VFS
    repo = vfs.repo
    parent_branch = vfs.branch_name
    
    try:
        # Get current branch HEAD
        parent_ref = repo.repo.branches.get(parent_branch)
        if parent_ref is None:
            return {"success": False, "error": f"Parent branch '{parent_branch}' not found"}
        
        parent_commit = parent_ref.peel(pygit2.Commit)
        
        # Check if branch already exists
        if branch_name in repo.repo.branches:
            return {"success": False, "error": f"Branch '{branch_name}' already exists"}
        
        # Create the new branch
        repo.repo.branches.create(branch_name, parent_commit)
        
        # Read current session to get parent info
        try:
            current_session_content = vfs.read_file(SESSION_FILE)
            current_session = json.loads(current_session_content)
        except (FileNotFoundError, json.JSONDecodeError):
            current_session = {}
        
        # Update current session's child list
        child_sessions = current_session.get("child_sessions", [])
        if branch_name not in child_sessions:
            child_sessions.append(branch_name)
        current_session["child_sessions"] = child_sessions
        
        # Write back to current session (will be committed with parent's next commit)
        vfs.write_file(SESSION_FILE, json.dumps(current_session, indent=2))
        
        # Create initial session for child branch
        # We need to write directly to the child branch, not through current VFS
        from forge.vfs.work_in_progress import WorkInProgressVFS
        
        child_vfs = WorkInProgressVFS(repo, branch_name)
        child_session = {
            "messages": [],
            "active_files": [],
            "parent_session": parent_branch,
            "child_sessions": [],
            "state": "idle",
            "yield_message": None,
            "task": task,  # Store task description for reference
        }
        child_vfs.write_file(SESSION_FILE, json.dumps(child_session, indent=2))
        child_vfs.commit(f"Initialize child session: {task[:50]}")
        
        return {
            "success": True,
            "branch": branch_name,
            "task": task,
            "message": (
                f"Created child session on branch '{branch_name}'. "
                f"Use resume_session('{branch_name}', 'your instructions') to start it."
            ),
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}