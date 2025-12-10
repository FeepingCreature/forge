# Forge MVP TODO

## Goal
Get Forge to the point where it can develop itself - a working AI-assisted IDE with tool support and git integration.

## Phase 1: Core Functionality (MVP)

### 1. LLM Integration ⚠️ CRITICAL
- [x] Add API key configuration (env var or config file)
- [x] Wire up LLMClient to AIChatWidget
- [x] Implement actual message sending (not echo)
- [x] Handle streaming responses
- [x] Display assistant responses in chat
- [x] Error handling for API failures

### 2. Tool System ⚠️ CRITICAL
- [x] Wire up ToolManager to AI sessions
- [x] Discover tools on session start
- [x] Send tool schemas to LLM
- [x] Handle tool calls from LLM responses
- [x] Execute tools and return results
- [x] Display tool execution in chat UI
- [ ] **Add tool approval workflow** (new/modified tools need user OK before first use)
  - Tools should be marked as approved/unapproved
  - UI dialog to review tool code before first use
  - Track approval state in session or global config
  - Prevent execution of unapproved tools
- [ ] Track approved vs unapproved tools
- [ ] UI to review and approve pending tools

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
8. **Repository Summaries** - Generate and cache file summaries (basic version done)
9. ✅ **Active Files** - Track and manage files in context

**Forge can now develop itself!** The git-first workflow is complete. Each AI turn creates an atomic commit with all changes.

Next priorities:
- **Fix context sending** - Repository summaries and active files now sent to LLM ✓
- **Improve repository summary generation** - Currently just "File: {path}", should use cheap LLM
- **Add UI for managing active files** - No way to add/remove files from context yet
- **File save functionality** - Manual edits in editor don't save
- **Tool approval workflow** - Tools run without user review (security issue)
- **Session persistence in git** - Sessions saved to filesystem, should be in git commits

## Known Issues

- [ ] **Repository summaries not LLM-generated** - Just placeholder text, need to use cheap model
- [ ] **No tool approval workflow** - Tools execute without user review (security risk)
- [ ] **Context not properly sent to LLM** - Fixed in this commit, but needs testing
- [ ] **Session state not in git** - Saved to filesystem instead of `.forge/sessions/` in git
- [ ] **No UI for managing active files** - Can't add/remove files from context
- [ ] **Editor doesn't save files** - No Ctrl+S implementation
- [ ] **No keyboard shortcuts** - Most shortcuts not implemented
- [ ] **Session loading doesn't restore chat display properly** - Messages load but display may be wrong

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
