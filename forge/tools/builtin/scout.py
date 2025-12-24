"""
Scout tool - query a smaller/cheaper model with many files.

Use this to examine more files than would fit in your context window.
The scout model (Haiku) can answer questions about the files or tell you
which ones are relevant to load into your own context.
"""

import fnmatch
from typing import TYPE_CHECKING, Any

from forge.config.settings import Settings
from forge.llm.client import LLMClient

if TYPE_CHECKING:
    from forge.vfs.base import VFS


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "function": {
            "name": "scout",
            "description": """Send many files to a smaller/cheaper model (Haiku) to answer a question or identify relevant files.

Use this when you need to examine more files than would be practical to load into your own context.
The scout model can:
- Answer questions about patterns across many files
- Identify which files are relevant for a task
- Summarize how something works across the codebase

Note: The scout model has no memory between calls and cannot make tool calls.
It only sees the files you explicitly pass to it.

Example uses:
- "Which of these files handle authentication?"
- "What error handling patterns are used across these files?"
- "Find the files that define or use the User class"
""",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern to match files (e.g., 'src/**/*.py' for all Python files under src/). Use **/ for recursive matching.",
                    },
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Explicit list of file paths (alternative to pattern)",
                    },
                    "question": {
                        "type": "string",
                        "description": "The question to ask about these files",
                    },
                },
                "required": ["question"],
            },
        },
    }


def execute(vfs: "VFS", args: dict[str, Any]) -> dict[str, Any]:
    """Query the scout model with files and a question"""
    pattern = args.get("pattern", "")
    files = args.get("files", [])
    question = args.get("question", "")

    if not question:
        return {"success": False, "error": "No question specified"}

    # Resolve files from pattern if provided
    if pattern:
        all_files = vfs.list_files()
        # fnmatch doesn't handle ** well, so we do it manually
        if "**" in pattern:
            # Convert ** glob to work with fnmatch
            # e.g., "src/**/*.py" should match "src/foo/bar/baz.py"
            parts = pattern.split("**/")
            prefix = parts[0]  # e.g., "src/"
            suffix = parts[1] if len(parts) > 1 else "*"  # e.g., "*.py"
            for f in all_files:
                if f.startswith(prefix) and fnmatch.fnmatch(f, f"*{suffix}"):
                    files.append(f)
        else:
            files = [f for f in all_files if fnmatch.fnmatch(f, pattern)]

    if not files:
        return {"success": False, "error": "No files specified or matched pattern"}

    # Read all the files
    file_contents: list[tuple[str, str]] = []
    errors: list[str] = []

    for filepath in files:
        try:
            content = vfs.read_file(filepath)
            file_contents.append((filepath, content))
        except FileNotFoundError:
            errors.append(f"File not found: {filepath}")
        except UnicodeDecodeError:
            errors.append(f"Binary file skipped: {filepath}")

    if not file_contents:
        return {
            "success": False,
            "error": "Could not read any files",
            "details": errors,
        }

    # Build the prompt with all file contents
    file_sections = []
    for filepath, content in file_contents:
        file_sections.append(f"=== {filepath} ===\n{content}")

    files_text = "\n\n".join(file_sections)

    prompt = f"""You are a code analysis assistant. You have been given the contents of several files and a question about them.

Answer the question based on the file contents. Be specific - reference file names and line numbers when relevant.
If asked to identify relevant files, list them clearly.

FILES:
{files_text}

QUESTION: {question}"""

    # Get settings and create client
    settings = Settings()
    api_key = settings.get_api_key()
    model = settings.get_summarization_model()

    if not api_key:
        return {"success": False, "error": "No API key configured"}

    client = LLMClient(api_key, model)

    # Call the model
    messages = [{"role": "user", "content": prompt}]

    response = client.chat(messages)

    # Extract the response
    choices = response.get("choices", [])
    if not choices:
        return {"success": False, "error": "No response from model"}

    answer = choices[0].get("message", {}).get("content", "")

    result: dict[str, Any] = {
        "success": True,
        "answer": answer,
        "files_examined": len(file_contents),
        "model": model,
    }

    if errors:
        result["file_errors"] = errors

    return result
