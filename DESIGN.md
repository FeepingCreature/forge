# Forge Design Specification

## Core Philosophy

Forge is a git-native IDE where **branches are the fundamental workspace unit**. There is no working directory in the traditional sense. All file access goes through VFS, all saves create commits, and multiple branches can be open simultaneously as tabs.

Key insights:
- **Git models multi-agent collaboration** - Each branch is an isolated workspace. Users and AI agents use the same interface.
- **AI time travel** - Checkout any commit to see the exact code state AND the AI conversation that produced it.
- **No dirty state** - If you can see it, it's committed (or about to be). The traditional "dirty state" concept disappears.

## Key Principles

1. **Git is More Fundamental Than Filesystem**: The git repository is the primary reality. The filesystem is just a view. AI sessions work entirely within git.

2. **Tool-Based AI**: LLMs interact through approved, sandboxed tools that operate on VFS, not filesystem. Tools are reviewed once when created/modified, then run autonomously.

3. **Session Persistence in Git**: AI conversations are committed alongside code changes. Every commit contains both the code diff AND the session state.

4. **Concurrent Sessions via Branches**: Multiple AI tasks run on separate git branches. They don't interfere. Cross-session collaboration happens through git merges.

5. **Save = Commit**: No staging area, no dirty state. Saving creates an atomic commit. Commits are cheap, rollback is easy.

---

## Architecture Overview

### UI Hierarchy

```
┌─ Branch Tabs (top level) ─────────────────────────────┐
│ [🌿 main] [🤖 ai/feature] [🌿 feature/xyz] [+]        │
├───────────────────────────────────────────────────────┤
│ ┌─────────┬─ File Tabs (within branch) ────────────┐  │
│ │ File    │ [🤖 AI Chat] [README.md] [src/main.py] │  │
│ │ Explorer├────────────────────────────────────────┤  │
│ │         │                                        │  │
│ │ 📁 src  │  (content area - chat or editor)       │  │
│ │  📄 a.py│                                        │  │
│ │  ● b.py │  (● = in AI context)                   │  │
│ │ 📄 README│                                       │  │
│ └─────────┴────────────────────────────────────────┘  │
├───────────────────────────────────────────────────────┤
│ Status: Branch: main | Saved → abc1234               │
└───────────────────────────────────────────────────────┘
```

**Branch tabs:** Each tab is a complete, isolated view of a branch. Switching tabs does NOT checkout - it just changes which VFS you're viewing. Branches with session data show 🤖, others show 🌿.

**File explorer (left sidebar):**
- Shows files from VFS (git tree), not filesystem
- Context icons: ◯ (not in context), ◐ (partial), ● (full)
- Double-click to open file, click icon to toggle AI context

**File tabs (within a branch):**
- First tab is always 🤖 AI Chat (not closable)
- Other tabs are files open for viewing/editing
- All file operations go through VFS

---

## VFS Architecture

### The Work-in-Progress Problem

During an AI turn with multiple tool calls:
- Tool 1 modifies `file.py`
- Tool 2 needs to see Tool 1's changes
- But we haven't committed yet

We're working with **"commit + patch"**, not a pure git state.

### VFS Interface

```python
class VFS(ABC):
    def read_file(self, path: str) -> str
    def write_file(self, path: str, content: str) -> None
    def delete_file(self, path: str) -> None
    def list_files(self) -> list[str]
    def file_exists(self, path: str) -> bool
```

### Implementations

**GitCommitVFS** - Read-only view of a commit:
- Reads files from git tree objects
- Immutable - write operations raise error
- Used for historical commits, diffs

**WorkInProgressVFS** - Writable layer:
- Wraps a base commit, accumulates changes in memory
- `read_file()` checks pending_changes first, falls back to base
- `write_file()` updates pending_changes
- `commit()` creates atomic git commit
- `materialize_to_tempdir()` for running tests/commands

### Single Access Layer

ALL file operations go through VFS. No direct filesystem I/O for repository content.

**When is a filesystem needed?**
- Running shell commands / tests
- External tool execution
- These materialize a tempdir from VFS on demand

---

## Tool System

Tools are Python modules in `forge/tools/builtin/` (built-in) or `./tools/` (user-created):

```python
# Example: tools/search_replace.py
def get_schema() -> dict:
    """Return JSON schema for LLM"""
    return {...}

def execute(vfs: VFS, args: dict) -> dict:
    """Perform operation using VFS"""
    content = vfs.read_file(args['filepath'])
    new_content = content.replace(args['search'], args['replace'], 1)
    vfs.write_file(args['filepath'], new_content)
    return {'success': True}
```

### Built-in Tools (always available, no approval needed)

| Tool | Purpose |
|------|---------|
| `write_file` | Write complete file to VFS |
| `delete_file` | Delete file from VFS |
| `search_replace` | Make SEARCH/REPLACE edits |
| `update_context` | Add/remove files from AI context |
| `grep_open` | Search files and add matches to context |
| `get_lines` | Get lines around a specific line number |
| `rename_file` | Rename/move a file |

### Tool Approval

User-created tools require approval before first use:
- New/modified tools trigger approval UI
- Approval state tracked in `.forge/approved_tools.json`
- Tool file hashes detect modifications
- Once approved, tools run autonomously

---

## Context Model

The AI always receives:
1. **Summaries of all files** - cheap, always included (~50 tokens/file)
2. **Full content of active files** - files explicitly loaded into context

### Prompt Cache Optimization

Prompts are structured as an append-only stream to maximize Anthropic cache reuse:

```
[system prompt]              ← stable prefix
[summaries for all files]    ← generated once
[file content: oldest first]
[file content: newest last]  
[conversation history]
[latest content]             ← cache_control: ephemeral
```

When a file is modified:
1. Delete its old content block
2. Append new content at end
3. Cache preserved for everything before

**Auto-included files:** `CLAUDE.md` and `AGENTS.md` are automatically added to context at session start.

---

## Session Management

### Branch = Workspace

All branches are equal. Every branch:
- Can have AI Chat for assistance
- Uses Save = Commit workflow
- Has its own `.forge/session.json`

When you branch, the session file diverges naturally - forking a branch forks the conversation.

### Session Persistence

Stored in `.forge/session.json` within each branch:
- `messages`: Conversation history
- `active_files`: Files in AI context

### AI Turn Locking

File tabs become **read-only during AI turns**:
- Pre-turn: All files must be saved (Save/Discard/Cancel dialog)
- During: Visual indicators (⏳, status bar)
- After: Tabs refresh from VFS, editing re-enabled

### One Commit Per Turn

Each AI interaction produces exactly one commit:
- Keeps costs down (AI plans all changes upfront)
- Creates clean, atomic history
- Makes rollback trivial

---

## Commit Workflow

### Manual Edit Save
```
User types in editor
    ↓
Changes tracked in WorkInProgressVFS
    ↓
User presses Ctrl+S
    ↓
workspace.commit() → atomic git commit
    ↓
Status: "Saved → abc12345"
```

### AI Turn
```
AI uses tools (write_file, search_replace, etc.)
    ↓
Changes accumulate in WorkInProgressVFS
    ↓
AI turn ends
    ↓
SessionManager.commit_ai_turn() → atomic commit
    ↓
File tabs refresh
```

**Commit messages:** Auto-generated for edits (`"edit: filename.py"`), LLM-generated for AI turns.

---

## File Structure

```
forge/
├── main.py                 # Entry point
├── forge/
│   ├── ui/
│   │   ├── main_window.py      # Branch tabs, menu bar
│   │   ├── branch_tab_widget.py # File tabs + AI chat container
│   │   ├── branch_workspace.py  # Per-branch state, VFS access
│   │   ├── file_explorer_widget.py # VFS-based file tree
│   │   ├── editor_widget.py    # Code editor
│   │   └── ai_chat_widget.py   # AI chat interface
│   ├── git_backend/
│   │   └── repository.py       # Git operations via pygit2
│   ├── llm/
│   │   └── client.py           # OpenRouter API client
│   ├── session/
│   │   └── manager.py          # Session lifecycle, commits
│   ├── prompts/
│   │   └── manager.py          # Cache-optimized prompt construction
│   ├── tools/
│   │   ├── manager.py          # Tool discovery, approval, execution
│   │   └── builtin/            # Built-in tools
│   └── vfs/
│       ├── base.py             # VFS interface
│       ├── git_commit.py       # Read-only VFS
│       └── work_in_progress.py # Writable VFS layer
├── tools/                  # User-created tools (repo-specific)
└── .forge/                 # Forge metadata (tracked in git)
    ├── approved_tools.json # Tool approval tracking
    └── session.json        # Session state (per branch)
```

---

## Technical Stack

- **UI**: PySide6 (Qt for Python)
- **Git**: pygit2 (libgit2 bindings) - creates commits without touching working directory
- **LLM**: OpenRouter API (supports multiple models)
- **VFS**: Custom abstraction for "commit + work in progress" state
- **Language**: Python 3.10+

### Why pygit2?

pygit2 allows us to:
- Read/write git objects directly
- Create commits without checking out files
- Build trees in memory
- Work with multiple branches simultaneously
- Never touch the working directory

---

## Future: Repository View (v2)

A dedicated repository view as the default top-level tab:

### Visual Commit/Branch Overview
- Zoomable whole-repo graph
- Every commit and branch visible
- Abandoned commits shown as pseudo-branches (toggleable)

### Visual Git Operations
- **Merge:** Drag commit onto target branch
- **Rebase:** Detach and reattach commit chains
- **Cherry-pick:** Drag individual commits

### Smart Merge Indicators
- Green: can merge cleanly
- Yellow: has conflicts
- Preview merge result before executing
