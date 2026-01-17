"""
resume_session tool - Send a message to a child session and start/resume it.

This tool appends a message to a child session's conversation and kicks off
its execution. The child runs asynchronously - use wait_session to check
for completion or questions.
"""

import json
from typing import Any

from forge.constants import SESSION_FILE


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM."""
    return {
        "name": "resume_session",
        "description": (
            "Send a message to a child session and start/resume its execution. "
            "The child runs asynchronously. Use wait_session to check for "
            "completion or questions from the child."
        ),
        "input_schema": {
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
    }


def execute(vfs: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Send message to child session and start/resume it."""
    branch = args.get("branch", "")
    message = args.get("message", "")
    
    if not branch:
        return {"success": False, "error": "Branch name is required"}
    if not message:
        return {"success": False, "error": "Message is required"}
    
    repo = vfs.repo
    
    # Check if branch exists
    if branch not in repo.repo.branches:
        return {"success": False, "error": f"Branch '{branch}' does not exist"}
    
    try:
        # Read child's session
        from forge.vfs.work_in_progress import WorkInProgressVFS
        
        child_vfs = WorkInProgressVFS(repo, branch)
        
        try:
            session_content = child_vfs.read_file(SESSION_FILE)
            session_data = json.loads(session_content)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"success": False, "error": f"No valid session found on branch '{branch}'"}
        
        # Check if this is actually a child of current session
        parent_branch = vfs.branch_name
        if session_data.get("parent_session") != parent_branch:
            return {
                "success": False,
                "error": f"Branch '{branch}' is not a child of current session",
            }
        
        # Check current state
        current_state = session_data.get("state", "idle")
        if current_state == "running":
            return {
                "success": False,
                "error": f"Child session is already running",
            }
        
        # Append message to child's conversation
        messages = session_data.get("messages", [])
        messages.append({"role": "user", "content": message})
        session_data["messages"] = messages
        session_data["state"] = "running"
        session_data["yield_message"] = None
        
        # Write updated session
        child_vfs.write_file(SESSION_FILE, json.dumps(session_data, indent=2))
        child_vfs.commit(f"Resume session with message from parent")
        
        # TODO: Actually start the child's SessionRunner
        # For now, we just update the state - the session registry will
        # pick this up and start the runner when it sees state="running"
        
        # Signal that this needs to trigger a session start
        return {
            "success": True,
            "branch": branch,
            "state": "running",
            "message": f"Resumed child session '{branch}'. Use wait_session to check for completion.",
            # Flags for SessionRunner to pick up
            "_start_session": branch,
            "_start_message": message,
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}