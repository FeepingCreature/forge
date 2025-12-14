# Forge TODO - Branch-First Architecture Migration

> Derived from `NEW_DESIGN.md`. This replaces the old `TODO.md` which tracked MVP completion.

## Current State

The MVP is functional:
- ✅ Git-backed AI sessions with atomic commits
- ✅ VFS abstraction (GitCommitVFS, WorkInProgressVFS)
- ✅ Tool system with approval workflow
- ✅ Built-in tools (read_file, write_file, delete_file, search_replace, update_context)
- ✅ Repository summaries with caching
- ✅ LLM integration via OpenRouter

## Goal

Migrate to branch-first architecture where:
- Branches are the fundamental workspace unit (not files/sessions)
- Multiple branches open as tabs simultaneously
- No working directory dependency
- Save = Commit (no dirty state)
- Open files = Active files in AI context
- **Single session file per branch** (`.forge/session.json`) - diverges naturally with branches
- **No session UUIDs** - branch name is the identity

---

## Phase 1: Branch Tabs Infrastructure

### 1.1 Create BranchWorkspace dataclass
- [x] Create `src/ui/branch_workspace.py`
- [x] Define `BranchWorkspace` with: branch_name, vfs, open_files, ai_chat state
- [x] Handle VFS lifecycle per branch

### 1.2 Create BranchTabWidget
- [x] Create `src/ui/branch_tab_widget.py`
- [x] Contains file tabs (QTabWidget) + AI chat as first tab
- [x] Manages open files within a single branch
- [x] Routes file operations through VFS

### 1.3 Refactor MainWindow
- [x] Top-level tabs become branches (not mixed editor/AI)
- [x] Each branch tab contains a `BranchTabWidget`
- [x] "+" button creates new branch (with dialog for name/type)
- [x] Branch tab context menu (close, rename, delete)

### 1.4 Branch switching without checkout
- [x] Switch tabs just changes which VFS is active
- [x] No `git checkout` - working directory untouched
- [x] Each branch has independent VFS instance

---

## Phase 2: Editor VFS Integration ✅ COMPLETE

### 2.1 Wire EditorWidget to VFS ✅
- [x] EditorWidget is a pure view component (get_text/set_text only)
- [x] BranchTabWidget loads files via `workspace.get_file_content()` (VFS)
- [x] Removed direct filesystem I/O from EditorWidget

### 2.2 Track edits in VFS ✅
- [x] BranchTabWidget tracks modified files in `_modified_files` set
- [x] Changes written to VFS via `workspace.set_file_content()`
- [x] Tab shows modified indicator (dot: `📄 filename •`)

### 2.3 Implement Save = Commit ✅
- [x] Ctrl+S wired in MainWindow → `BranchTabWidget.save_current_file()`
- [x] Creates atomic git commit via `workspace.commit()`
- [x] Clear modified indicator after commit
- [x] Status bar shows commit hash

### 2.4 Auto-generate commit messages ✅
- [x] Simple format: `"edit: filename.py"` for single file
- [x] `"edit: N files"` for multiple files
- [x] LLM-based messages available via SessionManager (for AI turns)

### 2.5 Status bar updates ✅
- [x] Show current branch name on tab switch
- [x] Show commit hash after save (`Saved → abc12345`)
- [x] Modified state shown in tab title (not status bar)

---

## Phase 3: AI Turn Integration ✅ COMPLETE

### 3.1 Lock file tabs during AI execution ✅
- [x] Set all file tabs to read-only when AI turn starts via `set_read_only(True)`
- [x] Visual indicator: AI Chat tab shows "🤖 AI Chat ⏳" during processing
- [x] User can still view files, just not edit

### 3.2 Require save before AI turn ✅
- [x] Check for uncommitted changes before AI turn via `unsaved_changes_check` callback
- [x] Prompt user: "Save before AI turn?" with Save/Discard/Cancel options
- [x] Option to save all or cancel

### 3.3 Visual AI working indicator ✅
- [x] AI Chat tab shows ⏳ indicator when AI is working
- [x] Status bar shows "🤖 AI working..."
- [x] Send button already disabled during AI turn (existing code)

### 3.4 Re-enable editing on AI turn complete ✅
- [x] Unlock file tabs via `set_read_only(False)` when AI finishes
- [x] Refresh all open files from VFS via `refresh_all_files()`
- [x] Status bar shows commit hash: "🤖 AI finished → abc12345"

---

## Phase 4: Open Files = Active Files

### 4.1 Unify concepts
- [ ] Opening a file tab adds it to AI context
- [ ] Closing a file tab removes it from context
- [ ] Remove separate "active files" management UI

### 4.2 Context display
- [ ] Show token count per open file in tab tooltip
- [ ] Show total context tokens in status bar
- [ ] Warn if context exceeds model limit

### 4.3 AI can request file opens
- [ ] AI suggests files to open (via tool or message)
- [ ] UI shows suggestion, user can accept/decline
- [ ] Accepted files open as new tabs

---

## Phase 5: Polish

### 5.1 Branch tab context menu
- [ ] Close branch tab (with confirmation if changes)
- [ ] Rename branch
- [ ] Delete branch (with strong confirmation)
- [ ] Merge to... (opens merge dialog)
- [ ] Fork branch (create new branch from current state)

### 5.2 Visual indicators
- [ ] Branch ahead/behind main indicator
- [ ] Uncommitted changes indicator on branch tab
- [ ] AI session branches have distinct icon (🤖)

### 5.3 Keyboard shortcuts
- [ ] Ctrl+Tab / Ctrl+Shift+Tab: Switch branch tabs
- [ ] Ctrl+W: Close current file tab (not branch)
- [ ] Ctrl+Shift+W: Close current branch tab
- [ ] Ctrl+N: New branch dialog
- [ ] Ctrl+S: Save (commit) current file
- [ ] Ctrl+Shift+S: Save all open files

### 5.4 New branch from here
- [ ] Right-click on commit → "New branch from here"
- [ ] Fork current branch at HEAD
- [ ] Dialog for branch name

---

## Phase 6: Repository View (v2)

### 6.1 Visual commit/branch overview
- [ ] Dedicated repository view (button in top-left)
- [ ] Show all commits as graph
- [ ] Show all branches
- [ ] Abandoned commits visible (toggleable)

### 6.2 Visual git operations
- [ ] Drag commit onto branch → merge
- [ ] Drag commit to reorder → rebase
- [ ] Right-click commit → cherry-pick

### 6.3 Smart merge indicators
- [ ] Green: can merge cleanly
- [ ] Yellow: has conflicts
- [ ] Preview merge result before executing

---

## Migration Notes

### Files to modify:
- `src/ui/main_window.py` - Major refactor for branch tabs
- `src/ui/editor_widget.py` - Wire to VFS
- `src/ui/ai_chat_widget.py` - Integrate into BranchTabWidget

### Files to create:
- `src/ui/branch_workspace.py` - BranchWorkspace dataclass
- `src/ui/branch_tab_widget.py` - Container for file tabs + AI chat

### Files that can stay mostly unchanged:
- `src/vfs/*` - VFS abstraction already suitable
- `src/git_backend/*` - Git operations already suitable
- `src/tools/*` - Tool system works with VFS
- `src/session/manager.py` - May need minor adjustments

### Breaking changes:
- Tab model completely changes (branch tabs > file tabs)
- Editor no longer reads from filesystem directly
- "Dirty state" concept disappears (save = commit)

---

## Refactoring: Session Model Simplification

### Remove session UUIDs
- [x] Update `BranchWorkspace` to remove `session_id` field
- [x] Add `load_session_data()` and `save_session_data()` to BranchWorkspace
- [x] Replace `.forge/sessions/{uuid}.json` with `.forge/session.json`
- [x] Remove `session_id` parameter from `AIChatWidget`
- [x] Remove `session_id` from `SessionManager`
- [x] Use `branch_name` as the sole identifier

### Remove special branch prefix
- [x] Remove `is_session_branch` checks - replaced with `has_session`
- [x] Remove `forge/session/` prefix requirement from MainWindow
- [x] Update `_open_branch()` to not check for prefix
- [x] Update display names to just use branch name

### Session file handling
- [x] Add methods to load/save session from `.forge/session.json` in branch VFS
- [x] Create session file on first AI turn (via SessionManager.commit_ai_turn)
- [x] Session file diverges naturally when branching

### Merge conflict handling
- [ ] On merge, archive source branch session to `.forge/merged/{branch_name}.json`
- [ ] Keep current branch's `.forge/session.json` as-is
- [ ] No conflict resolution needed - merge means work is done

### Main branch protection
- [ ] Detect when AI turn is starting on main/master branch
- [ ] Show confirmation dialog: "Start AI session on main branch?"
- [ ] Suggest creating a new branch instead
- [ ] Allow user to proceed if they really want to

### File tab persistence
- [ ] Track open files in session data
- [ ] File tab changes are FOLLOW_UP commits (auto-amend)
- [ ] Restore open files when reopening a branch

---

## Open Questions

1. **File tab persistence across restarts:** Remember open files per-branch when app restarts?
