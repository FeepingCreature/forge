# Forge MVP TODO

## Goal
Get Forge to the point where it can develop itself - a working AI-assisted IDE with tool support and git integration.

## Phase 0: Critical Design Issues ⚠️ FIX FIRST

These issues violate core design principles and must be fixed before the project can work as intended.

### 0. VFS Abstraction for Work-in-Progress State ✅ FIXED
**Problem**: Tools need to see "commit + accumulated changes" but we're trying to work with pure git commits.
**Impact**: Can't properly handle multiple tool calls in one AI turn - each tool needs to see previous tools' changes.
**Solution**: 
- ✅ Create VFS abstraction: `GitCommitVFS` (read-only) and `WorkInProgressVFS` (writable)
- ✅ WorkInProgressVFS wraps a commit and accumulates changes in memory
- ✅ Tools receive VFS instance, use `vfs.read_file()` and `vfs.write_file()`
- ✅ After AI turn: `vfs.commit()` creates atomic git commit
- ✅ Refactor tools from subprocess scripts to Python modules loaded via importlib
**Files created**:
- ✅ `src/vfs/__init__.py`
- ✅ `src/vfs/base.py` - Abstract VFS interface
- ✅ `src/vfs/git_commit.py` - GitCommitVFS implementation
- ✅ `src/vfs/work_in_progress.py` - WorkInProgressVFS implementation
**Files modified**:
- ✅ `src/tools/manager.py` - Use VFS, load tools via importlib
- ✅ `tools/search_replace.py` - Convert to Python module with VFS

### 1. Session Persistence Violates Git-First Principle ✅ FIXED
**Problem**: Sessions were saved to filesystem in `MainWindow._save_session()`, not committed to git.
**Impact**: Broke "AI time travel" - couldn't checkout old commits and see session state.
**Fix**: 
- ✅ Removed filesystem session save/load operations
- ✅ Sessions only saved during `SessionManager.commit_ai_turn()`
- ✅ Load sessions by reading `.forge/sessions/*.json` from git tree
- ✅ Deleted `AIChatWidget.save_session()` and `load_session()` filesystem operations

### 2. Commit Timing is Wrong ✅ FIXED
**Problem**: Commits happened after each tool call in `_execute_tool_calls()`, not once per AI turn.
**Design says**: "One Commit Per Cycle" - each AI interaction produces exactly one commit.
**Impact**: Created multiple commits per turn, wasted tokens, broke atomic commit model.
**Fix**: ✅ Only commit after AI's final response, not during tool execution loop.

### 3. Repository Summaries Not Implemented ✅ FIXED
**Problem**: `SessionManager.generate_repo_summaries()` used placeholder text, didn't call LLM.
**Impact**: Context sent to LLM was useless. The "cheap summaries + selective full files" strategy didn't work.
**Fix**: ✅ Now calls cheap LLM (Haiku) to generate file summaries with caching.

### 4. Tool Approval Workflow ✅ FIXED
**Problem**: Tools execute without user review.
**Design says**: "Tools are reviewed once at creation/modification time."
**Impact**: Malicious or buggy tools could run without user knowledge.
**Fix**:
- ✅ Add `approved_tools.json` in `.forge/` (tracked in git)
- ✅ Show approval UI inline in chat for new/modified tools
- ✅ Check approval before execution
- ✅ Track tool file hashes to detect modifications
**Status**: Complete! Tools now require approval before first use.

### 5. Context Building Has Timing Issues ⚠️ NEEDS IMPROVEMENT
**Problem**: Context is inserted as system message before every user message, duplicating context on every turn.
**Impact**: Wastes tokens, sends redundant data.
**Fix**: 
- Build context once at session start
- Update only when files change
- Send as initial system message, not inserted mid-conversation
**Current Status**: Context is built and sent, but timing could be optimized.

### 6. Active Files Management Missing UI ⚠️ HIGH PRIORITY
**Problem**: `SessionManager` tracks `active_files`, but no UI to add/remove files.
**Impact**: Users can't control what's in context. The "expand/collapse files" feature doesn't exist.
**Fix**: Add UI controls (buttons, file tree, etc.) to manage active files.
**Status**: Backend exists, UI missing. This is blocking effective use of the context system.

### 7. Error Handling Violates "No Fallbacks" Philosophy ⚠️ MEDIUM PRIORITY
**Problem**: Many try/except blocks with silent failures (e.g., `print(f"Error: {e}")` and continue).
**CLAUDE.md says**: "No fallbacks! No try/except... Errors and backtraces are holy."
**Fix**: Let errors propagate or show them to user. Don't silently continue.
**Status**: Can be addressed incrementally as we touch each module.

### 8. Type Hints Use `Any` Too Much ⚠️ LOW PRIORITY
**Problem**: `settings: Any`, `repo: Any`, `session_widget: Any` throughout codebase.
**Impact**: Loses type safety benefits.
**Fix**: Use proper types (`Settings`, `ForgeRepository`, `AIChatWidget`).
**Status**: Can be addressed incrementally. Run `make typecheck` to find issues.

### 9. Sessions Directory Creation is Inconsistent ✅ FIXED
**Problem**: Falls back to `.forge/sessions` in current directory if not in git repo.
**Issue**: Whole app is supposed to require git.
**Fix**: Either require git repo or clarify fallback behavior in design.
**Status**: App now requires git repo (see main.py error handling).

### 10. Session Loading Happens from Filesystem, Not Git ✅ FIXED
**Problem**: `MainWindow._load_existing_sessions()` read from filesystem.
**Should**: Load sessions from current git branch's `.forge/sessions/` directory.
**Fix**: ✅ Read session files from git tree, not filesystem.

## Phase 1: Core Functionality (MVP)

### 1. LLM Integration ⚠️ CRITICAL
- [x] Add API key configuration (env var or config file)
- [x] Wire up LLMClient to AIChatWidget
- [x] Implement actual message sending (not echo)
- [x] Handle streaming responses
- [x] Display assistant responses in chat
- [x] Error handling for API failures

### 2. Tool System ✅ COMPLETE
- [x] Wire up ToolManager to AI sessions
- [x] Discover tools on session start
- [x] Send tool schemas to LLM
- [x] Handle tool calls from LLM responses
- [x] Execute tools and return results
- [x] Display tool execution in chat UI
- [x] **Add tool approval workflow** (new/modified tools need user OK before first use)
  - Tools marked as approved/unapproved with file hash tracking
  - Inline approval UI in chat with review of tool code
  - Approval state tracked in `.forge/approved_tools.json` (git-committed)
  - Execution blocked for unapproved tools
- [x] Track approved vs unapproved tools
- [x] UI to review and approve pending tools

### 3. File Management
- [ ] Save file functionality (Ctrl+S)
- [ ] Track dirty/clean state in editor tabs
- [ ] Show unsaved indicator in tab
- [ ] Prompt before closing unsaved files
- [ ] Auto-save option
- [ ] File tree/explorer sidebar (optional for MVP)

### 4. Git Integration
- [x] Implement commit_changes() in ForgeRepository
- [x] Create commits directly without touching working dir
- [x] Stage session state files with code changes
- [x] Generate commit messages with smaller LLM
- [ ] **Fix commit timing** - Currently commits after each tool call, should be once per AI turn
- [ ] Show current branch in status bar
- [ ] Basic branch switching UI
- [ ] Commit history viewer
- [ ] **Session state in git** - Currently saves to filesystem, should commit to `.forge/sessions/`

### 5. Editor Enhancements
- [ ] Syntax highlighting for more languages (JS, HTML, CSS, etc.)
- [ ] Find/replace functionality
- [ ] Go to line
- [ ] Undo/redo (already works, just wire up menu)
- [ ] Copy/paste/cut (already works, just wire up menu)
- [ ] Keyboard shortcuts

### 6. Session Management
- [ ] Better session naming (user-editable)
- [ ] Session deletion
- [ ] Session export/import
- [ ] Show session branch in tab
- [ ] Indicate active/inactive sessions

### 7. UI Polish
- [ ] Keyboard shortcuts (Ctrl+O, Ctrl+S, Ctrl+N, etc.)
- [ ] Better status bar (show git branch, file path, line/col)
- [ ] Confirmation dialogs for destructive actions
- [ ] Loading indicators for LLM calls
- [ ] Better error messages
- [ ] Dark mode support

## Phase 2: Self-Development Features

### 8. Advanced Tools
- [ ] Multi-file search/replace tool
- [ ] File creation tool
- [ ] File deletion tool
- [ ] Directory operations tool
- [ ] Run command tool (with approval)

### 9. Code Intelligence
- [ ] Show file diffs before applying
- [ ] Syntax validation before saving
- [ ] Basic linting integration
- [ ] Code formatting tool

### 10. Git Workflow
- [ ] Rebase session branch onto main
- [ ] Merge session changes
- [ ] Conflict resolution UI
- [ ] Cherry-pick commits
- [ ] Branch visualization

## Phase 3: Quality of Life

### 11. Code Quality
- [x] Add mypy type checking
- [x] Add ruff linting/formatting
- [x] Create Makefile for checks
- [ ] Add type hints to all modules
- [ ] Fix all mypy errors
- [ ] Fix all ruff warnings

### 12. Performance
- [ ] Lazy load sessions
- [ ] Cache tool schemas
- [ ] Optimize chat rendering
- [ ] Background git operations

### 13. Documentation
- [ ] User guide
- [ ] Tool development guide
- [ ] Architecture documentation
- [ ] Video tutorials

### 14. Testing
- [ ] Unit tests for core components
- [ ] Integration tests
- [ ] Tool test framework
- [ ] CI/CD setup

## Immediate Next Steps (to develop Forge in Forge)

1. ✅ **API Key Setup** - Add OPENROUTER_API_KEY support
2. ✅ **Wire LLM** - Connect chat widget to actual LLM
3. ✅ **Tool Discovery** - Make tool system functional
4. ✅ **search_replace** - Refactor to work on git content, not filesystem
5. ✅ **SessionManager** - Coordinate AI turns and atomic commits
6. ✅ **Git Commits** - Implement commit_changes() with tree building
7. ✅ **Commit Messages** - Use cheap model to generate messages
8. ✅ **Repository Summaries** - Generate and cache file summaries with LLM
9. ✅ **Active Files** - Track and manage files in context
10. ✅ **VFS Abstraction** - Complete git-backed virtual filesystem

**Forge can now develop itself!** The git-first workflow is complete. Each AI turn creates an atomic commit with all changes.

## Top Priorities (Ordered by Importance)

### 1. **Tool Approval Workflow** ⚠️ CRITICAL SECURITY
- Tools currently execute without user review
- Need approval dialog for new/modified tools
- Track approved tools in `.forge/approved_tools.json`
- This is a security requirement before Forge can be safely used

### 2. **Active Files UI** ⚠️ HIGH USABILITY
- Backend tracks active files, but no UI to manage them
- Users can't add/remove files from context
- Blocks effective use of the context system
- Add file tree or buttons to manage active files

### 3. **File Save Functionality** ⚠️ HIGH USABILITY
- Editor can open and display files
- But Ctrl+S doesn't work - can't save manual edits
- Need to implement save functionality in EditorWidget

### 4. **Context Optimization** 🔧 MEDIUM PERFORMANCE
- Context currently rebuilt and sent on every turn
- Should build once and update incrementally
- Wastes tokens and API costs

### 5. **Error Handling Cleanup** 🔧 MEDIUM QUALITY
- Remove silent try/except blocks
- Let errors propagate or show to user
- Follow "no fallbacks" philosophy from CLAUDE.md

## Known Issues

**NOTE**: Most critical issues moved to "Phase 0: Critical Design Issues" above.

- [ ] **Editor doesn't save files** - No Ctrl+S implementation (HIGH PRIORITY)
- [ ] **No UI for active files** - Can't add/remove files from context (HIGH PRIORITY)
- [ ] **Tool approval missing** - Tools execute without review (CRITICAL SECURITY)
- [ ] **No keyboard shortcuts** - Most shortcuts not implemented
- [ ] **Session loading doesn't restore chat display properly** - Messages load but display may be wrong
- [ ] **Too many try/except blocks** - Violates "no fallbacks" philosophy
- [ ] **Type hints too loose** - Many `Any` types should be specific
- [ ] **Context sent on every turn** - Wastes tokens, should be optimized

## Nice to Have (Post-MVP)

- [ ] Multiple cursors
- [ ] Minimap
- [ ] Breadcrumbs
- [ ] Integrated terminal
- [ ] Debugger integration
- [ ] Test runner
- [ ] Git blame view
- [ ] Inline git diff
- [ ] Code folding
- [ ] Autocomplete
- [ ] Snippet support
- [ ] Vim/Emacs keybindings
