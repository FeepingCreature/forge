"""
Get skill documentation - returns detailed instructions for specific tasks.

This tool provides documentation for rarely-used but complex tasks,
keeping this information out of the main system prompt.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.vfs.base import VFS


SKILLS: dict[str, str] = {
    "create_tool": """\
# Creating a Custom Tool

Custom tools live in the `/tools` directory at the repository root.
Each tool is a single Python file that exports two functions: `get_schema()` and `execute()`.

## File Structure

Create a file at `tools/<tool_name>.py`:

```python
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.vfs.work_in_progress import WorkInProgressVFS


def get_schema() -> dict[str, Any]:
    \"\"\"Return tool schema for LLM\"\"\"
    return {
        "type": "function",
        "function": {
            "name": "my_tool",  # Must match filename without .py
            "description": "What this tool does - be descriptive!",
            "parameters": {
                "type": "object",
                "properties": {
                    "param1": {
                        "type": "string",
                        "description": "Description of param1",
                    },
                    "param2": {
                        "type": "boolean",
                        "default": False,
                        "description": "Optional param with default",
                    },
                },
                "required": ["param1"],  # List required params
            },
        },
    }


def execute(vfs: "WorkInProgressVFS", args: dict[str, Any]) -> dict[str, Any]:
    \"\"\"Execute the tool\"\"\"
    param1 = args.get("param1", "")
    param2 = args.get("param2", False)

    # Do work here...

    return {
        "success": True,
        "result": "whatever you want to return",
    }
```

## VFS API

The `vfs` parameter is a `WorkInProgressVFS` that provides access to files:

### Reading Files
```python
content = vfs.read_file("path/to/file.py")  # Returns str, raises FileNotFoundError if missing
exists = vfs.file_exists("path/to/file.py")  # Returns bool
```

### Writing Files
```python
vfs.write_file("path/to/file.py", "new content")  # Accumulates in pending changes
```

### Listing Files
```python
files = vfs.list_files()  # Returns list of all file paths
```

### Deleting Files
```python
vfs.delete_file("path/to/file.py")  # Marks for deletion
```

### Getting Changes
```python
pending = vfs.get_pending_changes()  # Returns dict[path, content]
deleted = vfs.get_deleted_files()  # Returns set of deleted paths
```

### Materializing to Disk
For tools that need to run external commands:
```python
tmpdir = vfs.materialize_to_tempdir()  # Returns Path to temp directory
# tmpdir contains the full repo state with pending changes applied
# Remember to clean up:
import shutil
shutil.rmtree(tmpdir, ignore_errors=True)
```

## Tool Approval

Custom tools require user approval before they can be executed. When you create
a new tool, the user will be prompted to review and approve it. The tool's hash
is stored in `.forge/approved_tools.json` so modifications require re-approval.

## Example: A Tool That Runs Commands

```python
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.vfs.work_in_progress import WorkInProgressVFS


def get_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "run_make",
            "description": "Run a make target",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Make target to run",
                    },
                },
                "required": ["target"],
            },
        },
    }


def execute(vfs: "WorkInProgressVFS", args: dict[str, Any]) -> dict[str, Any]:
    target = args.get("target", "")

    # Materialize VFS to run commands
    tmpdir = vfs.materialize_to_tempdir()

    try:
        result = subprocess.run(
            ["make", target],
            cwd=tmpdir,
            capture_output=True,
            text=True,
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
```
""",
}


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    skill_names = list(SKILLS.keys())
    return {
        "type": "function",
        "function": {
            "name": "get_skill",
            "description": (
                "Get detailed documentation for a specific skill. "
                "Use this when you need instructions for complex, rarely-used tasks. "
                f"Available skills: {', '.join(skill_names)}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {
                        "type": "string",
                        "enum": skill_names,
                        "description": "The skill to get documentation for",
                    },
                },
                "required": ["skill"],
            },
        },
    }


def execute(vfs: "VFS", args: dict[str, Any]) -> dict[str, Any]:
    """Return skill documentation"""
    skill = args.get("skill", "")

    if not skill:
        return {
            "success": False,
            "error": "No skill specified",
            "available_skills": list(SKILLS.keys()),
        }

    if skill not in SKILLS:
        return {
            "success": False,
            "error": f"Unknown skill: {skill}",
            "available_skills": list(SKILLS.keys()),
        }

    return {
        "success": True,
        "skill": skill,
        "documentation": SKILLS[skill],
    }
