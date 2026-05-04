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
- [ ] Ctrl+G: Go to line
- [ ] Ctrl+F: Find in file
- [x] Ctrl+Shift+F: Find in project (global search)
- [ ] Search in webview (chat history)
- [ ] Ctrl+Return in search to ask model "find code that does X" (AI-assisted search)
- [ ] Search + AI hybrid: normal search, then "explain these results"

### Startup & Bundling
- [ ] JS files for webview should be bundled in app (no HTTP requests on startup)

### File Explorer
- [ ] Global search results shown as icons/markers in explorer view
- [ ] Explorer could become generic "tool view" with tabs (files, search results, etc.)

### UI Layout & Theming
- [ ] Make UI panels arrangeable/dockable
- [ ] Configurable syntax highlighting/theming (how deep?)
- [ ] Performance audit at some point

### Code Completion
- [ ] Improve ghost text rendering (currently uses tooltip)

### AI Turn Interaction
- [ ] Pause button (only if OpenRouter supports pause/resume streaming - probably not)

### Tool Rendering
- [ ] User-defined tools need a hook for custom pretty rendering

### Session Forking UX
- [ ] "Fork conversation here" button in chat history
- [ ] Creates new branch from that commit
- [ ] Try two approaches in parallel

### Project Configuration
- [ ] `.forge/config.toml` for project-specific settings
- [ ] Model preferences per-project
- [ ] Custom test/build commands (read-only to AI - cannot modify autonomously)
- [ ] Note: Config affects AI capabilities, so AI must not be able to write it

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
- [ ] Show dangling/recent commits not on any branch (reflog-based?)

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
