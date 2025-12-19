# Forge TODO - Branch-First Architecture

> Derived from `NEW_DESIGN.md`. Tracks implementation status of the branch-first architecture.

## Current State

The branch-first architecture is **largely complete**:
- ✅ Git-backed AI sessions with atomic commits
- ✅ VFS abstraction (GitCommitVFS, WorkInProgressVFS)
- ✅ Tool system with approval workflow
- ✅ Built-in tools (write_file, delete_file, search_replace, update_context, grep_open)
- ✅ Repository summaries with caching
- ✅ LLM integration via OpenRouter with streaming
- ✅ Branch tabs as primary workspace unit
- ✅ File explorer sidebar with context management
- ✅ PromptManager for cache-optimized prompts
- ✅ Auto-include CLAUDE.md/AGENTS.md in context

---

## Phase 1: Branch Tabs Infrastructure ✅ COMPLETE

- [x] Create `src/ui/branch_workspace.py` - BranchWorkspace class
- [x] Create `src/ui/branch_tab_widget.py` - Container for file tabs + AI chat
- [x] Refactor MainWindow for branch-level tabs
- [x] "+" button creates new branch with dialog
- [x] Branch tab context menu (close, fork, delete)
- [x] Branch switching without checkout (VFS-based)

---

## Phase 2: Editor VFS Integration ✅ COMPLETE

- [x] EditorWidget is pure view (get_text/set_text only)
- [x] BranchTabWidget loads files via VFS
- [x] Changes tracked in `_modified_files` set
- [x] Tab shows modified indicator (`📄 filename •`)
- [x] Ctrl+S → `workspace.commit()` → atomic git commit
- [x] Auto-generated commit messages (`edit: filename.py`)
- [x] Status bar shows branch and commit hash

---

## Phase 3: AI Turn Integration ✅ COMPLETE

- [x] Lock file tabs during AI execution (read-only)
- [x] Visual indicator: AI Chat tab shows "🤖 AI Chat ⏳"
- [x] Status bar shows "🤖 AI working..."
- [x] Pre-turn save check with Save/Discard/Cancel dialog
- [x] Re-enable editing when AI turn completes
- [x] Refresh all open files from VFS after AI changes

---

## Phase 4: Context Management ✅ COMPLETE

- [x] File explorer sidebar (`FileExplorerWidget`)
- [x] Explorer reads from VFS (shows branch's git tree)
- [x] Double-click file → opens in file tab
- [x] Context icons: ◯ (none), ◐ (partial), ● (full)
- [x] Click icon to toggle AI context
- [x] Opening file tab adds to context (one-way)
- [x] Closing file tab does NOT remove from context
- [x] AI can add files via `update_context` and `grep_open`
- [x] `grep_open` tool for discovering relevant files
- [x] PromptManager for cache-optimized prompt stream
- [x] Modified files move to end of prompt for cache reuse
- [x] Auto-include CLAUDE.md/AGENTS.md at session start

---

## Phase 5: Polish (In Progress)

### 5.1 Branch tab context menu
- [x] Close branch tab (with unsaved changes check)
- [x] Fork branch (create new branch from current state)
- [x] Delete branch (with confirmation)
- ~~Rename branch~~ → Repository View (Phase 7)
- ~~Merge to...~~ → Repository View (Phase 7)

### 5.2 Visual indicators
- [x] 🤖 icon for branches with session data
- [x] 🌿 icon for branches without session data
- ~~Branch ahead/behind main indicator~~ → Rethink later
- ~~Uncommitted changes indicator on branch tab~~ → Rethink later

### 5.3 Keyboard shortcuts
- [x] Ctrl+S: Save (commit) current file
- [x] Ctrl+Shift+S: Save all open files
- [x] Ctrl+Tab / Ctrl+Shift+Tab: Switch branch tabs
- [x] Ctrl+W: Close current file tab
- [x] Ctrl+Shift+W: Close current branch tab
- [x] Ctrl+N: New branch dialog

### 5.4 Context display
- [x] Show token count per file in tab tooltip
- [x] Show total context tokens in status bar
- [x] Warn if context exceeds model limit (⚠️ icon at >80%)

### 5.5 New branch actions
- [x] New AI Session (creates branch + initial session commit)
- [x] New Branch (simple branch creation)
- [ ] Right-click on commit → "New branch from here"

---

## Phase 6: Session Management Improvements

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
- [ ] Restore open file tabs when reopening a branch
- [ ] File tab changes are FOLLOW_UP commits (auto-amend)

---

## Phase 7: Repository View (v2 - Future)

### 7.1 Visual commit/branch overview
- [ ] Repository View as default top-level tab (before branch tabs)
- [ ] Zoomable whole-repo graph
- [ ] Show all commits as graph
- [ ] Show all branches
- [ ] Abandoned commits visible (toggleable)

### 7.2 Visual git operations
- [ ] Drag commit onto branch → merge
- [ ] Drag commit to reorder → rebase
- [ ] Right-click commit → cherry-pick

### 7.3 Branch operations (moved from branch tab context menu)
- [ ] Rename branch (right-click branch label)
- [ ] Merge branch (drag onto target)
- [ ] Delete branch (right-click branch label)

### 7.4 Smart merge indicators
- [ ] Green: can merge cleanly
- [ ] Yellow: has conflicts
- [ ] Preview merge result before executing

---

## Known Issues / Tech Debt

- [ ] Error handling uses too many try/except blocks (violates "no fallbacks" philosophy)
- [ ] Some type hints use `Any` instead of proper types
- [x] Context token counting implemented and displayed in UI

---

## Open Questions

1. **File tab persistence across restarts:** Remember open files per-branch when app restarts? Currently not implemented - only AI context is restored.

---

## Implementation Notes

### Key Files

| File | Purpose |
|------|---------|
| `src/ui/main_window.py` | Branch tabs, menu bar, branch management |
| `src/ui/branch_tab_widget.py` | File tabs + AI chat container, file operations |
| `src/ui/branch_workspace.py` | Per-branch state, VFS access |
| `src/ui/file_explorer_widget.py` | VFS-based file tree with context icons |
| `src/ui/ai_chat_widget.py` | AI chat, streaming, tool execution |
| `src/session/manager.py` | Session lifecycle, prompt building, commits |
| `src/prompts/manager.py` | Cache-optimized prompt construction |
| `src/vfs/work_in_progress.py` | Writable VFS layer for branches |
| `src/tools/manager.py` | Tool discovery, approval, execution |

### Architecture Invariants

1. **All file access through VFS** - No direct filesystem I/O for repo content
2. **Save = Commit** - No dirty state concept
3. **Branch = Workspace** - Each branch tab is fully isolated
4. **One session file per branch** - `.forge/session.json` diverges with branches
5. **Open files ⊆ Active files** - One-way relationship, managed via file explorer
