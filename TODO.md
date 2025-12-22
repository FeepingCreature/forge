# Forge TODO

## Current State

The branch-first architecture is **complete**:
- ✅ Git-backed AI sessions with atomic commits
- ✅ VFS abstraction (GitCommitVFS, WorkInProgressVFS)
- ✅ Tool system with approval workflow
- ✅ Built-in tools (write_file, delete_file, search_replace, update_context, grep_open, grep_context, get_lines, rename_file, undo_edit, compact, set_license)
- ✅ Repository summaries with caching
- ✅ LLM integration via OpenRouter with streaming
- ✅ Branch tabs as primary workspace unit
- ✅ File explorer sidebar with context management
- ✅ PromptManager for cache-optimized prompts
- ✅ Auto-include CLAUDE.md/AGENTS.md in context
- ✅ Editor VFS integration (Save = Commit)
- ✅ AI turn locking with pre-turn save check
- ✅ Token counting and context stats in UI
- ✅ Keyboard shortcuts (save, switch tabs, close tabs, new branch)
- ✅ Branch tab context menu (close, fork, delete)
- ✅ `check` tool (format + typecheck + lint)
- ✅ Context compaction with nudge at threshold

**Forge can develop itself!** The git-first workflow is complete.

---

## High Priority: Code Execution

These are critical for AI to verify its work.

### Run Commands Tool
- [ ] `run_command` tool - Execute shell commands in materialized VFS
- [ ] Sandbox: run in temp dir, timeout, resource limits
- [ ] Return stdout/stderr/exit code to AI
- [ ] AI can run tests, build, execute scripts

### Test Integration
- [ ] `run_tests` tool - Run project tests (pytest, make test, etc.)
- [ ] Parse test output to show pass/fail summary
- [ ] On failure, show relevant traceback
- [ ] AI can iterate until tests pass

### REPL/Eval Tool
- [ ] `eval` tool - Evaluate a Python expression/snippet
- [ ] Useful for AI to test small code fragments
- [ ] Returns result or exception

---

## Medium Priority: Developer Experience

### Quick Actions
- [ ] Ctrl+P: Quick open (fuzzy file search) - we have the widget, need shortcut
- [ ] Ctrl+Shift+P: Command palette
- [ ] Ctrl+G: Go to line
- [ ] Ctrl+F: Find in file
- [ ] Ctrl+Shift+F: Find in project

### Diff Review Before Accept
- [ ] After AI turn, show diff of all changes before committing
- [ ] User can accept, reject, or edit individual changes
- [ ] "Accept all" for quick workflow

### Session Forking UX
- [ ] "Fork conversation here" button in chat history
- [ ] Creates new branch from that commit
- [ ] Try two approaches in parallel

### Project Configuration
- [ ] `.forge/config.toml` for project-specific settings
- [ ] Define build/test/run commands
- [ ] Custom tool paths
- [ ] Model preferences per-project

---

## Lower Priority: Polish

### User Task Tracking
- [ ] AI can add tasks for user via `add_user_task` tool
- [ ] Tasks shown in UI, user can check off
- [ ] Prevents "stale instruction" confusion

### File Tab Persistence
- [ ] Remember open files per-branch across restarts
- [ ] Store in XDG config (not session data)

### Editor Improvements
- [ ] Ctrl+O: Open file dialog
- [ ] Find/replace in file
- [ ] Go to definition (for Python)
- [ ] Show references

---

## Git Graph Improvements

### Search & Navigation
- [ ] Search box (filter by message, author, SHA)
- [ ] Jump to commit by partial SHA

### Drag-and-Drop Operations
- [ ] Drag commits for merge/rebase
- [ ] Visual feedback during drag

### Git Operations
- [ ] Squash commit into parent
- [ ] Undo support (reflog-based)

---

## Future: Repository View (v2)

### Visual Commit/Branch Overview
- [ ] Repository View as default tab
- [ ] Zoomable whole-repo graph
- [ ] Abandoned commits visible (toggleable)

### Visual Git Operations
- [ ] Drag commit onto branch → merge
- [ ] Drag to reorder → rebase
- [ ] Cherry-pick via drag

### Smart Merge Indicators
- [ ] Green: clean merge
- [ ] Yellow: conflicts
- [ ] Preview before executing

---

## Tech Debt

- [ ] Too many try/except blocks (violates "no fallbacks")
- [ ] Some `Any` type hints should be proper types
- [ ] Consolidate similar tool code (grep_open/grep_context share logic)

---

## Nice to Have (Post-MVP)

- [ ] Integrated terminal
- [ ] Debugger integration
- [ ] Git blame view
- [ ] Inline git diff
- [ ] Code folding
- [ ] Autocomplete / LSP
- [ ] Vim keybindings
