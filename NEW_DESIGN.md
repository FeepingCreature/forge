# Forge Design v2 - Branch-First Architecture

## Core Philosophy

Forge is a git-native IDE where **branches are the fundamental workspace unit**. There is no working directory in the traditional sense. All file access goes through VFS, all saves create commits, and multiple branches can be open simultaneously as tabs.

The key insight: **git models multi-agent collaboration**. Each branch is an isolated workspace. Users and AI agents use the same interface. The traditional "dirty state" concept disappears - if you can see it, it's committed (or about to be).

## Architecture Overview

### UI Hierarchy

```
┌─ Branch Tabs (top level) ─────────────────────────────┐
│ [main] [forge/session/abc123] [feature/xyz] [+]       │
├───────────────────────────────────────────────────────┤
│ ┌─ File Tabs (within branch) ──────────────────────┐  │
│ │ [🤖 AI Chat] [README.md] [src/main.py]           │  │
│ ├──────────────────────────────────────────────────┤  │
│ │                                                  │  │
│ │  (content area - chat or editor)                 │  │
│ │                                                  │  │
│ └──────────────────────────────────────────────────┘  │
├───────────────────────────────────────────────────────┤
│ Status: branch info, commit hash, etc                 │
└───────────────────────────────────────────────────────┘
```

**Branch tabs:** Each tab is a complete, isolated view of a branch. Switching tabs does NOT checkout - it just changes which VFS you're viewing.

**File tabs (within a branch):**
- First tab is always 🤖 AI Chat (can be minimized/hidden but always present)
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
- Commit message: auto-generated (`edit: filename.py`) or use cheap LLM
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
| View file | Open in file tab | `read_file` tool |
| Edit file | Type + Save | `write_file` / `search_replace` |
| Create file | New file + Save | `write_file` |
| Delete file | Delete action | `delete_file` |
| See history | Git log view | (future: tool) |

**Key unification:** The AI Chat tab is just another "editor" - it edits the conversation and triggers commits that include both conversation state and code changes.

### Branch Modes

A branch can be in one of two modes:

**1. Manual Mode (default for `main`, user-created branches)**
- User can edit files freely
- AI Chat is available for assistance
- AI suggestions require user action to apply
- Save = immediate commit

**2. AI Session Mode (branches starting with `forge/session/`)**
- AI is actively working
- File tabs are **read-only** (locked)
- User observes AI making changes
- User can:
  - Send messages to guide AI
  - **Fork** the branch to get an editable copy
  - Wait for AI to finish

**Forking during AI session:**
- User clicks "Fork" button
- New branch created from current commit
- User gets editable Manual Mode workspace
- Original AI session continues independently

### AI Sessions as Internal PRs

AI sessions naturally model pull requests:

1. User creates AI session → new branch from `main`
2. AI works on branch, making commits
3. When complete, user reviews changes
4. User merges to `main` (or rebases, cherry-picks, etc.)

**Future enhancement:** Explicit PR view showing diff between session branch and main, with merge/rebase controls.

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

**BranchVFS** (new - replaces WorkInProgressVFS for user editing):
- Wraps a branch reference
- `read_file`: reads from branch HEAD
- `write_file`: creates commit on branch
- Used for Manual Mode editing

**WorkInProgressVFS** (existing - for AI sessions):
- Accumulates changes in memory during AI turn
- Commits atomically at end of turn
- Used for AI Session Mode

**GitCommitVFS** (existing):
- Read-only view of specific commit
- Used for history viewing, diffs

### Commit Flow

**Manual edit save:**
```
User types in editor
    ↓
User presses Ctrl+S
    ↓
BranchVFS.write_file(path, content)
    ↓
Create blob → Update tree → Create commit → Update branch ref
    ↓
Editor shows saved state (commit hash in status bar)
```

**AI turn:**
```
AI uses tools (read_file, write_file, etc.)
    ↓
Changes accumulate in WorkInProgressVFS
    ↓
AI turn ends
    ↓
vfs.commit() creates atomic commit
    ↓
Branch ref updated, file tabs refresh
```

## Data Model

### Branch State

Each open branch tab maintains:

```python
@dataclass
class BranchWorkspace:
    branch_name: str
    vfs: VFS  # BranchVFS or WorkInProgressVFS depending on mode
    mode: Literal["manual", "ai_session"]
    open_files: list[str]  # Paths of open file tabs
    ai_chat: AIChatState | None  # Conversation state
```

### Session Persistence

Session state stored in `.forge/sessions/{session_id}.json` within the branch:
- Conversation history
- Active files in context
- Tool call history
- Session metadata

On branch tab open:
1. Check if `forge/session/*` branch → AI Session Mode
2. Load session state from `.forge/sessions/*.json`
3. Initialize appropriate VFS
4. Restore open file tabs

## User Workflows

### Simple Editing (like a normal editor)

1. Open Forge in a repo
2. `main` branch tab opens by default
3. Open files, edit, Ctrl+S saves (commits)
4. Use AI Chat tab if you want AI help

### AI-Assisted Development

1. Click [+] → "New AI Session"
2. New branch `forge/session/{uuid}` created
3. Branch tab opens in AI Session Mode
4. Chat with AI, watch it make changes
5. When done, merge to main

### Concurrent Workflows

1. Have `main` open for quick fixes
2. Have `forge/session/feature-a` for AI working on feature A
3. Have `forge/session/refactor` for AI doing refactoring
4. Switch between tabs freely
5. Each is fully isolated

### Forking an AI Session

1. AI is working on `forge/session/abc`
2. You spot something you want to fix manually
3. Click "Fork" → creates `forge/session/abc-fork-1`
4. New tab opens in Manual Mode
5. Make your edits, save (commits)
6. Can merge changes back if needed

## Implementation Plan

### Phase 1: Branch Tabs Infrastructure

- [ ] Create `BranchWorkspace` class to manage per-branch state
- [ ] Refactor `MainWindow` to use branch-level tabs
- [ ] Create `BranchTabWidget` containing file tabs + AI chat
- [ ] Implement branch tab switching (no checkout)

### Phase 2: BranchVFS for Manual Editing

- [ ] Create `BranchVFS` that commits on write
- [ ] Wire editor saves through VFS
- [ ] Auto-generate commit messages
- [ ] Show commit hash in status bar

### Phase 3: Mode Switching

- [ ] Detect AI session branches → AI Session Mode
- [ ] Lock file tabs during AI execution
- [ ] Implement Fork button/action
- [ ] Handle mode transitions cleanly

### Phase 4: Polish

- [ ] Branch tab context menu (close, rename, delete, merge)
- [ ] Visual indicators for branch state (ahead/behind main, etc.)
- [ ] Keyboard shortcuts for branch navigation
- [ ] "New branch from here" action

## Open Questions

1. **Commit message UX:** Auto-generate silently, or mini-prompt, or configurable?

2. **Undo across commits:** Ctrl+Z within a file should work normally, but what about undoing a save? Expose git reset somehow?

3. **File tab state persistence:** When switching branch tabs, should open files be remembered per-branch?

4. **Main branch protection:** Should `main` require confirmation before commit? Or is that overkill given easy revert?

5. **Merge/rebase UI:** How much git workflow UI to build vs. expecting users to use git CLI?

## Migration from v1

The current codebase has:
- Filesystem-based editor (needs VFS)
- AI sessions already use VFS (good!)
- Mixed tab model (editor tabs + AI tabs at same level)

Migration path:
1. Add branch tab layer above current tabs
2. Wire editor through VFS
3. Unify session loading to be branch-based
4. Remove filesystem assumptions
