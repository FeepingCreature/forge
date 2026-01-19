"""
Get skill documentation - returns detailed instructions for specific tasks.

This tool provides documentation for rarely-used but complex tasks,
keeping this information out of the main system prompt.

Skills are discovered from tools that export a get_skill() function,
plus some built-in skills defined here.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.tools.context import ToolContext


# Built-in skills that aren't associated with a specific tool
BUILTIN_SKILLS: dict[str, str] = {
    "create_tool": """\
# Creating a Custom Tool

Custom tools live in the `/tools` directory at the repository root.
Each tool is a single Python file that exports `get_schema()` and `execute()`.

## File Structure

Create a file at `tools/<tool_name>.py`:

```python
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.tools.context import ToolContext


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


def execute(ctx: "ToolContext", args: dict[str, Any]) -> dict[str, Any]:
    \"\"\"Execute the tool\"\"\"
    param1 = args.get("param1", "")
    param2 = args.get("param2", False)

    # Do work here...

    return {
        "success": True,
        "result": "whatever you want to return",
    }
```

## ToolContext API

The `ctx` parameter is a `ToolContext` that provides access to files and more:

```python
# File operations (convenience methods)
content = ctx.read_file("path/to/file.py")   # Returns str, raises FileNotFoundError
ctx.write_file("path/to/file.py", "content") # Accumulates in pending changes
exists = ctx.file_exists("path/to/file.py")  # Returns bool
files = ctx.list_files()                      # List all text files

# Access VFS directly for more operations
ctx.vfs.delete_file("path/to/file.py")       # Mark for deletion
pending = ctx.vfs.get_pending_changes()      # dict[path, content]
deleted = ctx.vfs.get_deleted_files()        # set of deleted paths

# Branch info
current = ctx.branch_name                    # Current branch name

# Cross-branch operations
other_vfs = ctx.get_branch_vfs("other-branch")
other_content = other_vfs.read_file("file.py")

# Git repository access (for advanced operations)
ctx.repo  # ForgeRepository instance
```

### Materializing to Disk

For tools that need to run external commands:

```python
tmpdir = ctx.vfs.materialize_to_tempdir()  # Returns Path to temp directory
# tmpdir contains the full repo state with pending changes applied
# Remember to clean up:
import shutil
shutil.rmtree(tmpdir, ignore_errors=True)
```

## Calling the LLM (Scout Model)

Custom tools can call the summarization/scout model for analysis tasks:

```python
from typing import TYPE_CHECKING, Any

from forge.config.settings import Settings
from forge.llm.client import LLMClient

if TYPE_CHECKING:
    from forge.tools.context import ToolContext


def execute(ctx: "ToolContext", args: dict[str, Any]) -> dict[str, Any]:
    # Get API key and summarization model from settings
    settings = Settings()
    api_key = settings.get_api_key()
    model = settings.get_summarization_model()

    if not api_key:
        return {"success": False, "error": "No API key configured"}

    # Create client and make request
    client = LLMClient(api_key, model)
    messages = [{"role": "user", "content": "Your prompt here"}]
    response = client.chat(messages)

    # Extract response
    choices = response.get("choices", [])
    if not choices:
        return {"success": False, "error": "No response from model"}

    answer = choices[0].get("message", {}).get("content", "")
    return {"success": True, "answer": answer}
```

## Exporting a Skill

Tools can provide detailed documentation via the `get_skill` tool by exporting
a `get_skill()` function. This is useful for complex tools that need more
explanation than fits in a schema description.

```python
def get_skill() -> tuple[str, str]:
    \"\"\"Return (skill_name, documentation) for this tool.\"\"\"
    return ("my_skill", \"\"\"\\
# My Skill Documentation

Detailed instructions for using this tool...

## Examples

```python
# Example usage
```
\"\"\")
```

The skill name doesn't have to match the tool name. Users can then call
`get_skill("my_skill")` to retrieve this documentation.

## Tool Approval

Custom tools require user approval before they can be executed. When you create
a new tool, the user will be prompted to review and approve it. The tool's hash
is stored in `.forge/approved_tools.json` so modifications require re-approval.

## Example: A Tool That Runs Commands

```python
import shutil
import subprocess
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.tools.context import ToolContext


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


def execute(ctx: "ToolContext", args: dict[str, Any]) -> dict[str, Any]:
    target = args.get("target", "")

    # Materialize VFS to run commands
    tmpdir = ctx.vfs.materialize_to_tempdir()

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
        shutil.rmtree(tmpdir, ignore_errors=True)
```
""",
}


def _get_all_skills(ctx: "ToolContext | None") -> dict[str, str]:
    """Get all available skills from tools and built-ins."""
    skills = BUILTIN_SKILLS.copy()

    # If we have a context with session_manager, get tool-defined skills
    if ctx and ctx.session_manager:
        tool_skills = ctx.session_manager.tool_manager.get_skills()
        skills.update(tool_skills)

    return skills


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM.

    Note: The enum is populated dynamically at runtime via execute(),
    since we can't know all skills until tools are discovered.
    """
    # List known built-in skills for the schema
    # Tool-defined skills are discovered at runtime
    return {
        "type": "function",
        "function": {
            "name": "get_skill",
            "description": (
                "Get detailed documentation for a specific skill. "
                "Use this when you need instructions for complex, rarely-used tasks. "
                f"Available skills: {', '.join(BUILTIN_SKILLS.keys())}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {
                        "type": "string",
                        "description": "The skill to get documentation for",
                    },
                },
                "required": ["skill"],
            },
        },
    }


def execute(ctx: "ToolContext", args: dict[str, Any]) -> dict[str, Any]:
    """Return skill documentation"""
    skill = args.get("skill", "")

    # Get all skills (built-in + tool-defined)
    all_skills = _get_all_skills(ctx)

    if not skill:
        return {
            "success": False,
            "error": "No skill specified",
            "available_skills": list(all_skills.keys()),
        }

    if skill not in all_skills:
        return {
            "success": False,
            "error": f"Unknown skill: {skill}",
            "available_skills": list(all_skills.keys()),
        }

    return {
        "success": True,
        "skill": skill,
        "documentation": all_skills[skill],
    }
