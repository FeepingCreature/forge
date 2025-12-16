# Forge Design v2 - Branch-First Architecture

> **Note:** This supersedes `DESIGN.md`. See that file for historical context and some implementation details that remain relevant.

## Core Philosophy

Forge is a git-native IDE where **branches are the fundamental workspace unit**. There is no working directory in the traditional sense. All file access goes through VFS, all saves create commits, and multiple branches can be open simultaneously as tabs.

The key insight: **git models multi-agent collaboration**. Each branch is an isolated workspace. Users and AI agents use the same interface. The traditional "dirty state" concept disappears - if you can see it, it's committed (or about to be).

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
- Refreshes after AI makes changes

**File tabs (within a branch):**
- First tab is always 🤖 AI Chat (not closable)
- Other tabs are files open for viewing/editing
- All file operations go through VFS for that branch

### No Working Directory

The filesystem working directory is irrelevant to Forge's operation. Forge can work on bare repositories.

**When is a filesystem needed?**
- Running shell commands / tests
- External tool execution
- These cases materialize a tempdir from VFS on demand

**What this enables:**
- Multiple branches open simultaneously
- No checkout conflicts
- True isolation between workspaces
- Works on bare repos

### Save = Commit

There is no dirty state. Saving a file creates a commit.

**Commit behavior:**
- Save (Ctrl+S) → immediate commit to current branch
- Commit message: auto-generated (`edit: filename.py`) or multi-file (`edit: 3 files`)
- No staging area exposed to user
- Every save is atomic and reversible

**Why no dirty state?**
- Dirty state is a vestige of single-branch, single-user workflow
- Git already tracks history - let it do its job
- Commits are cheap, rollback is easy
- Aligns with AI workflow (AI commits atomically too)

### Unified Human/AI Interface

Both users and AI agents use the same workspace model:

| Action | Human | AI Agent |
|--------|-------|----------|
| View file | Open in file tab | `update_context` / `grep_open` |
| Edit file | Type + Save | `write_file` / `search_replace` |
| Create file | New file + Save | `write_file` |
| Delete file | Delete action | `delete_file` |
| See history | Git log view | (future: tool) |

**Key unification:** The AI Chat tab is just another "editor" - it edits the conversation and triggers commits that include both conversation state and code changes.

### Branch Model

**All branches are equal.** There is no special "forge/session" branch type. Every branch works the same way:

- User can edit files freely
- AI Chat is available for assistance
- Save = immediate commit
- AI can be invoked at any time

**Session storage:** Each branch has its own `.forge/session.json` file that stores:
- Conversation history
- Active files in context

When you branch, the session file comes with it and diverges naturally - just like any other file. This means:
- Forking a branch forks the conversation
- Branch rename doesn't break anything
- No special branch naming required
- The branch name IS the session identity (no UUIDs)

**Merge handling (TODO):** When merging branches, `.forge/session.json` will conflict since both branches have diverged conversations. The IDE will need to handle this specially - possibly by archiving the merged-in session to `.forge/merged/{branch_name}.json` or allowing user to choose which history to keep.

### AI Turn Locking

File tabs become **read-only during an AI turn** (while VFS is AI-controlled). This is not a fundamental limitation - it's to avoid confusing both AI and user with concurrent edits.

**Pre-turn requirement:** All files must be saved before starting an AI turn. If there are unsaved changes, prompt user to save first (Save/Discard/Cancel dialog).

**During AI turn:**
- File tabs show content but are not editable
- AI Chat tab shows ⏳ indicator
- Status bar shows "🤖 AI working..."
- User can still send messages to guide AI (queued)

**After AI turn:**
- File tabs become editable again
- All open files refresh from VFS (AI may have changed them)
- Changes are committed
- Status bar shows "🤖 AI finished → abc12345"

### Open Files ⊆ Active Files

The relationship is one-way: **opening a file adds it to AI context, but closing does NOT remove it**.

- Opening a file tab adds it to context (via `file_opened` signal)
- **Closing a file tab does NOT remove it from context**
- Context is managed via the file explorer sidebar (click ◯/● to toggle)
- AI can add files to context without opening tabs (via `update_context` or `grep_open`)
- This allows AI to efficiently work with many files without cluttering the UI

**Auto-included files:** `CLAUDE.md` and `AGENTS.md` are automatically added to context at session start if they exist. These contain project-specific AI instructions.

### Prompt Cache Optimization

The prompt is structured as an **append-only stream with deletions** to maximize Anthropic cache reuse:

```
[system prompt] ← stable prefix
[summaries for all files] ← generated once at session start
[file content: oldest-modified first]
[file content: recently-modified last]
[conversation: user message]
[conversation: assistant response]
[conversation: tool calls + results]
...
[latest content] ← cache_control: ephemeral (always at end)
```

**Key optimization:** When a file is modified:
1. Delete its old content block from the stream
2. Append new content at the end with tool_call_id reference
3. Cache is preserved for everything before the old position

This means successive edits to the same file(s) get ~90% cache reuse.

**Implementation:** `PromptManager` class in `src/prompts/manager.py` handles this as a list of `ContentBlock` objects with soft deletion.

## VFS Architecture

### Single Access Layer

ALL file operations go through VFS. There is no direct filesystem I/O for repository content.

```python
class VFS(ABC):
    def read_file(self, path: str) -> str
    def write_file(self, path: str, content: str) -> None
    def delete_file(self, path: str) -> None
    def list_files(self) -> list[str]
    def file_exists(self, path: str) -> bool
```

### VFS Implementations

**WorkInProgressVFS** (used for both manual editing and AI sessions):
- Wraps a branch reference
- `read_file`: reads from branch HEAD + pending changes
- `write_file`: accumulates changes in memory
- `delete_file`: marks file for deletion
- `commit()`: creates atomic commit on branch
- For manual editing: user edits accumulate, Save triggers `commit()`
- For AI sessions: AI edits accumulate, end of turn triggers `commit()`

**GitCommitVFS** (existing):
- Read-only view of specific commit
- Used for history viewing, diffs
- Base layer for WorkInProgressVFS

### Commit Flow

**Manual edit save:**
```
User types in editor
    ↓
Changes tracked in WorkInProgressVFS (in memory)
    ↓
User presses Ctrl+S
    ↓
BranchTabWidget.save_file() → workspace.commit()
    ↓
Create blob → Update tree → Create commit → Update branch ref
    ↓
Status bar shows "Saved → abc12345"
```

**AI turn:**
```
AI uses tools (write_file, search_replace, etc.)
    ↓
Changes accumulate in WorkInProgressVFS
    ↓
AI turn ends (final response with no tool calls)
    ↓
SessionManager.commit_ai_turn() creates atomic commit
    ↓
Branch ref updated, file tabs refresh via refresh_all_files()
```

## Data Model

### Branch State

Each open branch tab maintains:

```python
class BranchWorkspace:
    branch_name: str
    _vfs: WorkInProgressVFS  # Created lazily
    open_files: list[str]  # Paths of open file tabs
    active_tab_index: int  # Currently focused tab
    
    @property
    def vfs(self) -> WorkInProgressVFS  # Single source of truth
    
    def get_file_content(self, filepath: str) -> str
    def set_file_content(self, filepath: str, content: str) -> None
    def commit(self, message: str) -> str  # Returns commit OID
```

### Session Persistence

Session state stored in `.forge/session.json` within the branch:
- `messages`: Conversation history (user, assistant, tool messages)
- `active_files`: List of files in AI context

On branch tab open:
1. Initialize VFS for the branch
2. Check if `.forge/session.json` exists → load session data
3. If missing → start fresh (file created on first AI turn)
4. Restore active files to AI context (but don't force-open tabs)

## User Workflows

### Simple Editing (like a normal editor)

1. Open Forge in a repo
2. Current branch tab opens by default
3. Use file explorer to open files
4. Edit, Ctrl+S saves (commits)
5. Use AI Chat tab if you want AI help

### AI-Assisted Development

1. Click [+] → "New AI Session"
2. Enter branch name (e.g., `ai/feature-x`)
3. Branch created with initial session commit
4. Chat with AI, watch it make changes
5. When done, merge to main

### Concurrent Workflows

1. Have `main` open for quick fixes
2. Have `ai/feature-a` for AI working on feature A
3. Have `refactor/cleanup` for manual refactoring
4. Switch between tabs freely
5. Each is fully isolated

### Forking a Branch

1. Right-click branch tab → "Fork branch..."
2. Enter new branch name
3. New branch created from current HEAD
4. New tab opens (inherits conversation history!)
5. Make edits independently
6. Can merge changes back if needed

## Implementation Status

### ✅ Complete

- Branch tabs infrastructure (BranchWorkspace, BranchTabWidget)
- Editor VFS integration (no filesystem I/O)
- Save = Commit workflow
- AI turn locking (read-only during AI work)
- Pre-turn save requirement with dialog
- File explorer sidebar with VFS integration
- Context management (open files ⊆ active files)
- PromptManager for cache optimization
- Auto-include CLAUDE.md/AGENTS.md

### 🔧 TODO

- Branch tab context menu enhancements (rename, merge dialog)
- Visual indicators (ahead/behind main)
- Keyboard shortcuts for branch navigation
- Token count display in UI
- Merge conflict handling for session files
- Main branch AI session confirmation
- File tab persistence across restarts
- Repository view (v2 - visual git operations)

## Design Decisions

1. **All branches equal:** No special `forge/session/` prefix. Any branch can have AI chat. Branches with `.forge/session.json` show 🤖 icon.

2. **One-way context:** Opening adds to context, closing doesn't remove. Context managed via file explorer icons.

3. **Concurrent editing:** User can freely edit their branch while AI works on a session branch. That's the whole point of branch isolation.

4. **Session merge strategy:** Archive session on merge. When a branch is merged, its `.forge/session.json` is archived to `.forge/merged/{branch_name}.json`. Merge means the work is done.

5. **Commit messages:** Auto-generated. `"edit: filename.py"` for single file, `"edit: N files"` for multiple. AI turns use LLM-generated messages.

6. **Undo (Ctrl+Z):** Normal editor undo within a file. Undo across commits handled via repository view (v2).

## Open Questions

1. **File tab persistence across restarts:** Remember open files per-branch when app restarts? Currently not implemented.

## v2: Repository View

A dedicated repository view is central to the git-first vision. Accessed via a prominent button (top-left), it switches the entire window to show:

### Visual Commit/Branch Overview
- Every commit visible
- Every branch visible
- Abandoned commits shown as pseudo-branches (toggleable) - enables undo even for "lost" work

### Visual Git Operations
- **Merge:** Drag a commit onto target branch head
- **Rebase:** Detach and reattach commit chains visually
- **Cherry-pick:** Drag individual commits between branches

### Smart Merge Indicators
Merging is expensive, but *checking if merge is clean* is cheap. The UI can show:
- Green indicator: commit can be cleanly rebased/merged
- Yellow indicator: merge possible but has conflicts
- Visual preview of what merge would look like

This makes git operations discoverable and safe - users can see consequences before acting.
