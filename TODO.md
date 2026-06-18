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

**Forge can develop itself!** The git-first workflow is complete. _(edit-tool render test — will be reverted)_

---

## Long-Term / Speculative

- [ ] AI can kick off autonomous work on separate branches via tool call
- [ ] Integrate AI with global search for AI-assisted code discovery

---

## High Priority: Scoped Capability Tools

The tool system is Forge's security model. NO arbitrary command execution.
Instead, each capability is a reviewed tool that runs autonomously once approved.
See DESIGN.md "Tool System: Security Through Capability Design".

### Test Runner Tool
- [x] `run_tests` tool - Run project's test suite (like `check` but for tests)
- [x] Discovers test command: `make test`, `pytest`, `npm test`, etc.
- [x] Parse output to show pass/fail summary
- [x] On failure, show relevant traceback
- [x] AI can iterate until tests pass
- [x] Built-in tool (no approval needed)

### Build Tool
- [ ] `build` tool - Run project's build command
- [ ] Discovers build command from Makefile, package.json, etc.
- [ ] Returns build output/errors
- [ ] Built-in, safe (read-only on source, writes to tempdir)

---

## Medium Priority: Developer Experience

### Quick Actions
- [x] Ctrl+E: Quick open (fuzzy file search)
- [x] Ctrl+Shift+P: Command palette
- [x] ActionRegistry for centralized keybinding management
- [x] Ctrl+F: Find in file (EditorWidget.SearchBar)
- [x] Ctrl+Shift+F: Find in project (global search)
- [x] Search in webview chat history (AIChatWidget._show_search)
- [ ] Ctrl+G: Go to line (EditorWidget.go_to_line exists, just needs shortcut wired)
- [ ] Ctrl+Return in search to ask model "find code that does X" (AI-assisted search)
- [ ] Search + AI hybrid: normal search, then "explain these results"

### Startup & Bundling
- [x] JS files for webview bundled in app (js_cache.JS_CACHE_DIR / get_script_src)

### File Explorer
- [ ] Global search results shown as icons/markers in explorer view
- [ ] Explorer could become generic "tool view" with tabs (files, search results, etc.)

### UI Layout & Theming
- [ ] Make UI panels arrangeable/dockable
- [ ] Configurable syntax highlighting/theming (PythonHighlighter exists but colors are hardcoded)
- [ ] Performance audit at some point

### Code Completion
- [ ] Improve ghost text rendering (currently QToolTip; want true in-editor inline)

### AI Turn Interaction
- [ ] Pause button (only if OpenRouter supports pause/resume streaming - probably not)

### Tool Rendering
- [ ] User-defined tools need a hook for custom pretty rendering

### Session Forking UX
- [x] "Fork conversation here" button in chat history (fork_requested signal)
- [x] Creates new branch from that commit (MainWindow._fork_from_turn)
- [ ] Try two approaches in parallel (workflow polish on top of the above)

### Project Configuration
- [ ] `.forge/config.toml` for project-specific settings (only `.forge/config.json` for tool approval today)
- [ ] Model preferences per-project
- [ ] Custom test/build commands (run_tests auto-discovers but isn't user-configurable)
- [ ] Note: Config affects AI capabilities, so AI must not be able to write it

---

## Lower Priority: Polish

### User Task Tracking
- [ ] AI can add tasks for user via `add_user_task` tool
- [ ] Tasks shown in UI, user can check off
- [ ] Prevents "stale instruction" confusion

### File Tab Persistence
- [x] Remember open files per-branch across restarts (BranchTabWidget save/restore_open_files)
- [ ] Verify storage location is XDG config (not session data)

### Editor Improvements
- [ ] Ctrl+O: Open file dialog (Ctrl+E quick open exists; question is whether a native dialog is wanted too)
- [ ] Find/replace in file (find exists, replace UI does not)
- [ ] Go to definition (for Python)
- [ ] Show references

---

## Git Graph Improvements

### Search & Navigation
- [ ] Search box (filter by message, author, SHA)
- [ ] Jump to commit by partial SHA
- [ ] Show dangling/recent commits not on any branch (reflog-based?)

### Drag-and-Drop Operations
- [x] Drag commits for merge (MergeDragSpline + drag handlers in git_graph/scene.py)
- [x] Visual feedback during drag (merge check icons, panel graying)
- [ ] Drag commits for rebase

### Git Operations
- [x] Squash commit into parent (squash_requested signal in git_graph/panel.py)
- [ ] Undo support (reflog-based) — GitActionLog exists but no reflog integration yet

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
- [x] Consolidate similar tool code (grep_open/grep_context now share grep_utils.get_files_to_search)

---

## Nice to Have (Post-MVP)

- [ ] Integrated terminal
- [ ] Debugger integration
- [ ] Git blame view
- [ ] Inline git diff
- [ ] Code folding
- [ ] Autocomplete / LSP
- [ ] Vim keybindings
