# Forge TODO

## User Tasks

*Tasks the AI has asked the user to verify. Check off when done, or delete if no longer relevant.*

- [ ] Test middle-mouse zoom in Git view - does it still scroll unexpectedly?

---

## Current State

The branch-first architecture is **largely complete**:
- ✅ Git-backed AI sessions with atomic commits
- ✅ VFS abstraction (GitCommitVFS, WorkInProgressVFS)
- ✅ Tool system with approval workflow
- ✅ Built-in tools (write_file, delete_file, search_replace, update_context, grep_open, get_lines, rename_file)
- ✅ Repository summaries with caching
- ✅ LLM integration via OpenRouter with streaming
- ✅ Branch tabs as primary workspace unit
- ✅ File explorer sidebar with context management
- ✅ PromptManager for cache-optimized prompts
- ✅ Auto-include CLAUDE.md/AGENTS.md in context
- ✅ Editor VFS integration (Save = Commit)
- ✅ AI turn locking with pre-turn save check
- ✅ Token counting and context stats in UI

**Forge can develop itself!** The git-first workflow is complete.

---

## Phase 1: Polish (In Progress)

### Keyboard Shortcuts
- [x] Ctrl+S: Save (commit) current file
- [x] Ctrl+Shift+S: Save all open files
- [x] Ctrl+Tab / Ctrl+Shift+Tab: Switch branch tabs
- [x] Ctrl+W: Close current file tab
- [x] Ctrl+Shift+W: Close current branch tab
- [x] Ctrl+N: New branch dialog
- [ ] Ctrl+O: Open file dialog
- [ ] Ctrl+G: Go to line

### Branch Tab Context Menu
- [x] Close branch tab (with unsaved changes check)
- [x] Fork branch (create new branch from current state)
- [x] Delete branch (with confirmation)

### Context Display
- [x] Show token count per file in tab tooltip
- [x] Show total context tokens in status bar
- [x] Warn if context exceeds model limit (⚠️ icon at >80%)

---

## Phase 2: Session Management Improvements

### Merge Conflict Handling
- [ ] On merge, archive source branch session to `.forge/merged/{branch_name}.json`
- [ ] Keep current branch's `.forge/session.json` as-is
- [ ] No conflict resolution needed - merge means work is done

### Working Directory Protection
- [x] Check if target branch is the currently checked-out branch on AI turn start
- [x] If checked out, ensure working directory is clean (no uncommitted changes)
- [x] Warn user if workdir has changes - they'd be overwritten by AI commits
- [x] After commit to checked-out branch, sync working directory to new HEAD
- [ ] Optional: soft warning when starting AI work on main/master (suggest creating branch)

### File Tab Persistence
- [ ] Track open files in XDG user config dir (NOT session data - this is user state)
- [ ] Restore open file tabs when reopening a branch
- [ ] Keyed by repo path + branch name

---

## Phase 3: Repository View (v2 - Future)

### Visual Commit/Branch Overview
- [ ] Repository View as default top-level tab (before branch tabs)
- [ ] Zoomable whole-repo graph
- [ ] Show all commits as graph
- [ ] Show all branches
- [ ] Abandoned commits visible (toggleable)

### Visual Git Operations
- [ ] Drag commit onto branch → merge
- [ ] Drag commit to reorder → rebase
- [ ] Right-click commit → cherry-pick

### Branch Operations (in repo view)
- [ ] Rename branch (right-click branch label)
- [ ] Merge branch (drag onto target)
- [ ] Delete branch (right-click branch label)

### Smart Merge Indicators
- [ ] Green: can merge cleanly
- [ ] Yellow: has conflicts
- [ ] Preview merge result before executing

---

## Tech Debt

- [ ] Error handling uses too many try/except blocks (violates "no fallbacks" philosophy)
- [ ] Some type hints use `Any` instead of proper types

---

## Nice to Have (Post-MVP)

- [ ] Multiple cursors
- [ ] Minimap
- [ ] Integrated terminal
- [ ] Debugger integration
- [ ] Git blame view
- [ ] Inline git diff
- [ ] Code folding
- [ ] Autocomplete
- [ ] Vim/Emacs keybindings

---

## Open Questions

1. **File tab persistence across restarts:** Remember open files per-branch when app restarts? Currently only AI context is restored, not open tabs.
