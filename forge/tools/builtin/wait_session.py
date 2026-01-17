"""
wait_session tool - Wait for one of the specified child sessions to complete or ask a question.

This tool checks the state of child sessions. If any has completed or is waiting
for input, it returns that information. If all are still running, the current
session yields and waits.
"""

import json
from typing import Any

from forge.constants import SESSION_FILE
from forge.tools.side_effects import SideEffect


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


def execute(vfs: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Check child sessions and wait if needed."""
    branches = args.get("branches", [])
    
    if not branches:
        return {"success": False, "error": "At least one branch is required"}
    
    repo = vfs._repo
    parent_branch = vfs._branch_name
    
    # Check each child's state
    ready_children = []
    running_children = []
    
    for branch in branches:
        if branch not in repo.repo.branches:
            return {"success": False, "error": f"Branch '{branch}' does not exist"}
        
        try:
            from forge.vfs.work_in_progress import WorkInProgressVFS
            
            child_vfs = WorkInProgressVFS(repo, branch)
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
                ready_children.append({
                    "branch": branch,
                    "state": state,
                    "message": yield_message,
                    "task": session_data.get("task", ""),
                })
            elif state == "running":
                running_children.append(branch)
            elif state == "idle":
                # Child hasn't been started yet
                return {
                    "success": False,
                    "error": f"Child session '{branch}' hasn't been started. Use resume_session first.",
                }
            elif state == "error":
                ready_children.append({
                    "branch": branch,
                    "state": "error",
                    "message": yield_message or "Unknown error",
                    "task": session_data.get("task", ""),
                })
                
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
            "_yield_reason": "waiting_children",
            "side_effects": [SideEffect.MID_TURN_COMMIT],  # Force commit before yield
        }
    
    # No children found (shouldn't happen given earlier checks)
    return {"success": False, "error": "No valid child sessions found"}