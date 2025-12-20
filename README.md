# Forge - AI Agent IDE

**WARNING**: Forge is alpha code at best!

Forge is a Qt-based coding assistant backed by git. AI sessions are independent branches. Each turn of AI activity
is one commit. Checking out a commit includes the conversation that led to it. Agents can work on multiple
branches simultaneously.

## Features

- **Git-native**: AI works through a virtual filesystem that reads/writes git objects directly
- **Atomic commits**: Each AI turn = one commit. Clean history, easy rollback
- **Branch tabs**: Work on multiple branches simultaneously without checkout conflicts
- **Tool-based editing**: No command-line calls, only tools. File editing tools are predefined.
- **Custom tools**: Add Python scripts to `./tools/` for project-specific automation
- **Session persistence**: Conversations are stored in `.forge/session.json` within each branch

## Installation

Requires Python 3.10+ and libgit2.

```bash
# Install libgit2 (required for pygit2)
# macOS
brew install libgit2

# Ubuntu/Debian
sudo apt-get install libgit2-dev

# Then install Forge
pip install -e .
```

Set your [OpenRouter](https://openrouter.ai/) API key via Settings (gear icon) or environment:

```bash
export OPENROUTER_API_KEY=sk-or-...
```

## Usage

```bash
# Run from any git repository
cd your-project
forge
```

## How It Works

1. **Open a branch** — Each branch tab is an isolated workspace with its own VFS and AI session
2. **Chat with the AI** — Describe what you want to build or change
3. **AI uses tools** — `write_file`, `search_replace`, `grep_open`, etc. operate on the VFS
4. **Changes commit atomically** — When the AI turn ends, all changes become one git commit
5. **Review and merge** — Use normal git workflow to review diffs, merge branches

### What the AI Sees

- **File summaries**: ~50 token summary of every file (generated at session start)
- **Active files**: Full content of files explicitly loaded into context
- **Conversation history**: The full chat for this branch's session

### Built-in Tools

| Tool | Purpose |
|------|---------|
| `write_file` | Create or overwrite a file |
| `search_replace` | Make targeted edits to existing files |
| `delete_file` | Remove a file |
| `rename_file` | Move or rename a file |
| `update_context` | Add/remove files from AI's active context |
| `grep_open` | Search files by regex, add matches to context |
| `get_lines` | View lines around a specific line number |
| `set_license` | Add a LICENSE file |
| `check` | Run `make check` (format + typecheck + lint) |

### Custom Tools

Add Python scripts to `./tools/` in your repository:

```python
# tools/my_tool.py
def get_schema() -> dict:
    """JSON schema for the LLM"""
    return {
        "name": "my_tool",
        "description": "Does something useful",
        "parameters": {...}
    }

def execute(vfs, args: dict) -> dict:
    """Run the tool using the VFS"""
    content = vfs.read_file(args["path"])
    # ... do something ...
    vfs.write_file(args["path"], new_content)
    return {"success": True}
```

As the AI can also add tools, custom tools require one-time approval before use (security measure).

### Why pygit2?

Forge uses pygit2 (libgit2 bindings) to manipulate git objects directly:
- Create commits without touching the working directory
- Read files from any branch/commit without checkout
- Multiple branches open simultaneously
- Build trees in memory before committing

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run checks (format + typecheck + lint)
make check

# Individual checks
make format     # Auto-format with ruff
make typecheck  # Type check with mypy
make lint       # Lint with ruff
```

## License

GPL-3.0 — see [LICENSE](LICENSE).
