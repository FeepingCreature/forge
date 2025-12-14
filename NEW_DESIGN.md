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
- **Open files = active files in AI context** (unified concept)
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

### Branch Model

**All branches are equal.** There is no special "forge/session" branch type. Every branch works the same way:

- User can edit files freely
- AI Chat is available for assistance
- Save = immediate commit
- AI can be invoked at any time

**Branch identification:** Since we always create a commit when a branch is created, branches can be identified by their initial commit OID. This provides stable identity even if the branch is renamed.

### AI Turn Locking

File tabs become **read-only during an AI turn** (while VFS is AI-controlled). This is not a fundamental limitation - it's to avoid confusing both AI and user with concurrent edits.

**Pre-turn requirement:** All files must be saved before starting an AI turn. If there are unsaved changes, prompt user to save first.

**During AI turn:**
- File tabs show content but are not editable
- User can still send messages to guide AI
- User can observe AI making changes in real-time

**After AI turn:**
- File tabs become editable again
- Changes are committed
- User can continue editing or start another AI turn

### Open Files = Active Files

Converge these concepts: **files open in tabs are the files in AI context**.

- Opening a file tab adds it to context
- Closing a file tab removes it from context
- No separate "active files" management needed
- AI sees exactly what user sees

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

### Phase 3: AI Turn Integration

- [ ] Lock file tabs during AI execution (read-only)
- [ ] Require save before AI turn starts
- [ ] Show visual indicator when AI is working
- [ ] Re-enable editing when AI turn completes

### Phase 4: Polish

- [ ] Branch tab context menu (close, rename, delete, merge)
- [ ] Visual indicators for branch state (ahead/behind main, etc.)
- [ ] Keyboard shortcuts for branch navigation
- [ ] "New branch from here" action

## Open Questions

1. **File tab state persistence:** When switching branch tabs, should open files be remembered per-branch?

2. **Main branch protection:** Should `main` require confirmation before commit? Or is that overkill given easy revert?

## Design Decisions Made

1. **Commit messages:** Auto-generate with cheap LLM. No user prompt needed.

2. **Undo (Ctrl+Z):** Normal editor undo within a file. Undo across commits handled via repository view (v2).

3. **Branch types:** All branches equal. No special "forge/session" naming convention required.

4. **Open files = active files:** Unified concept. Tab open = in context.

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
