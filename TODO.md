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
- [ ] Add tool approval workflow (new/modified tools need user OK before first use)
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
- [ ] Implement commit_changes() in ForgeRepository
- [ ] Create commits directly without touching working dir
- [ ] Stage session state files with code changes
- [ ] Generate commit messages with smaller LLM
- [ ] Show current branch in status bar
- [ ] Basic branch switching UI
- [ ] Commit history viewer

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

### 11. Performance
- [ ] Lazy load sessions
- [ ] Cache tool schemas
- [ ] Optimize chat rendering
- [ ] Background git operations

### 12. Documentation
- [ ] User guide
- [ ] Tool development guide
- [ ] Architecture documentation
- [ ] Video tutorials

### 13. Testing
- [ ] Unit tests for core components
- [ ] Integration tests
- [ ] Tool test framework
- [ ] CI/CD setup

## Immediate Next Steps (to develop Forge in Forge)

1. **API Key Setup** - Add OPENROUTER_API_KEY support
2. **Wire LLM** - Connect chat widget to actual LLM
3. **Tool Discovery** - Make tool system functional
4. **search_replace** - Ensure the built-in tool works
5. **File Save** - Implement Ctrl+S and save functionality
6. **Git Commits** - Make AI changes create real commits

Once these 6 items are done, Forge can theoretically develop itself!

## Known Issues

- [ ] Session loading doesn't restore chat display properly
- [ ] No way to configure API key yet
- [ ] Tool execution not wired up
- [ ] No commit creation yet
- [ ] Editor doesn't show file path in tab tooltip
- [ ] No keyboard shortcuts implemented

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
