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

---

## Phase 1: Branch Tabs Infrastructure

### 1.1 Create BranchWorkspace dataclass
- [ ] Create `src/ui/branch_workspace.py`
- [ ] Define `BranchWorkspace` with: branch_name, vfs, open_files, ai_chat state
- [ ] Handle VFS lifecycle per branch

### 1.2 Create BranchTabWidget
- [ ] Create `src/ui/branch_tab_widget.py`
- [ ] Contains file tabs (QTabWidget) + AI chat as first tab
- [ ] Manages open files within a single branch
- [ ] Routes file operations through VFS

### 1.3 Refactor MainWindow
- [ ] Top-level tabs become branches (not mixed editor/AI)
- [ ] Each branch tab contains a `BranchTabWidget`
- [ ] "+" button creates new branch (with dialog for name/type)
- [ ] Branch tab context menu (close, rename, delete)

### 1.4 Branch switching without checkout
- [ ] Switch tabs just changes which VFS is active
- [ ] No `git checkout` - working directory untouched
- [ ] Each branch has independent VFS instance

---

## Phase 2: Editor VFS Integration

### 2.1 Wire EditorWidget to VFS
- [ ] Add VFS parameter to EditorWidget constructor
- [ ] `load_file()` uses `vfs.read_file()` instead of filesystem
- [ ] Remove direct filesystem I/O from editor

### 2.2 Track edits in VFS
- [ ] On text change, mark file as modified in VFS
- [ ] Changes accumulate in `WorkInProgressVFS.pending_changes`
- [ ] Tab shows modified indicator (dot or asterisk)

### 2.3 Implement Save = Commit
- [ ] Ctrl+S calls `vfs.commit()` 
- [ ] Creates atomic git commit on branch
- [ ] Clear modified indicator after commit
- [ ] Show commit hash briefly in status bar

### 2.4 Auto-generate commit messages
- [ ] Simple format initially: `"edit: filename.py"`
- [ ] Later: Use cheap LLM for better messages
- [ ] Reuse existing `SessionManager.generate_commit_message()`

### 2.5 Status bar updates
- [ ] Show current branch name
- [ ] Show commit hash after save
- [ ] Show "modified" state if uncommitted changes

---

## Phase 3: AI Turn Integration

### 3.1 Lock file tabs during AI execution
- [ ] Set all file tabs to read-only when AI turn starts
- [ ] Visual indicator (grayed out, overlay, or border)
- [ ] User can still view files, just not edit

### 3.2 Require save before AI turn
- [ ] Check for uncommitted changes before AI turn
- [ ] Prompt user: "Save changes before AI turn?"
- [ ] Option to save all or cancel

### 3.3 Visual AI working indicator
- [ ] Show spinner or progress in AI chat tab
- [ ] Status bar shows "AI working..."
- [ ] Disable send button during AI turn

### 3.4 Re-enable editing on AI turn complete
- [ ] Unlock file tabs when AI finishes
- [ ] Refresh file contents from VFS (AI may have changed them)
- [ ] Show commit hash for AI's commit

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

## Open Questions

1. **Session branch naming**: Keep `forge/session/{uuid}` or allow custom names from start?
2. **Main branch protection**: Require confirmation before committing to main?
3. **File tab persistence**: Remember open files per-branch across restarts?
4. **Conflict during AI turn**: What if user's main branch changes while AI works on session branch?
