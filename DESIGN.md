# Forge Design Specification

## Core Philosophy

Forge is an AI-assisted development environment where **git is the source of truth**. All AI operations are git-backed, making them safe, auditable, and reversible.

The fundamental insight: **AI time travel**. You can checkout any commit and see not just the code state, but the exact AI conversation and session state that produced those changes. This makes AI development fully auditable and reversible.

## Key Principles

1. **Git is More Fundamental Than Filesystem**: The git repository is the primary reality. The filesystem is just a view. AI sessions work entirely within git - no temporary directories, no ephemeral state.

2. **Tool-Based AI**: LLMs can only interact through approved, sandboxed tools that operate on git state, not filesystem state.

3. **Opt-In Everything**: No automatic changes; user must approve all AI actions.

4. **Session Persistence in Git**: AI conversations are committed alongside code changes. Every commit contains both the code diff AND the session state that produced it.

5. **Concurrent Sessions via Branches**: Multiple AI tasks run on separate git branches. They don't interfere with each other. Cross-session collaboration happens through git merges, not shared filesystem state.

6. **No Ephemeral State**: If it's not in git, it doesn't exist. This ensures perfect reproducibility and time travel.

## Architecture

### Git Backend

**Core Principle**: AI sessions operate entirely within git, never touching the working directory.

- Each AI session gets its own branch: `forge/session/<session_id>`
- Session state stored in `.forge/sessions/<session_id>.json` (tracked in git)
- AI commits include both code changes AND session state updates
- Tools operate on git trees, not filesystem files
- Changes are accumulated in memory and committed atomically
- Working directory remains independent - user can work while AI runs

**AI Time Travel**: Checkout any commit to see:
- Exact code state at that point
- Exact AI conversation that produced those changes
- All session state (messages, tool calls, context)
- Can resume from any historical point

**Workflow**:
1. User creates AI session → new branch created
2. AI proposes changes → tools build new git tree in memory
3. User approves → atomic commit to session branch (code + session state)
4. Repeat until task complete
5. User merges session branch to main (or rebases, cherry-picks, etc.)

**Concurrent Sessions**:
- Each session branch is independent
- Sessions don't see each other's changes unless explicitly merged
- User's working directory is never touched by AI
- Can run multiple AI tasks in parallel on different branches

### Tool System

Tools are executable scripts in `./tools/` directory that:
- Accept `--schema` flag to output JSON schema for LLM
- Accept JSON input via stdin
- Output JSON results via stdout
- **Operate on git state, not filesystem** - receive file contents, return new contents
- Are sandboxed (can only propose file changes, not execute arbitrary code)
- Must be explicitly approved before AI can use them

**Git-Aware Tool Model**:
- Tools receive current file content from git tree
- Tools return new file content (or diffs)
- ToolManager accumulates changes in memory
- Changes are committed atomically when user approves
- No filesystem I/O during tool execution (except for tool code itself)

Tool lifecycle:
1. AI proposes a new tool (writes code to `./tools/`)
2. User reviews and approves (makes it executable, commits to git)
3. AI can use tool in next interaction
4. Tools are versioned with the code in git

### Session Management

Each session maintains:
- Unique session ID (UUID)
- Associated git branch
- Message history (user + assistant)
- Tool call history
- Current working state

Sessions persist across app restarts and are loaded from `.forge/sessions/`.

### UI Layout

```
┌─────────────────────────────────────────────────────────┐
│ Menu Bar                                                 │
├──────────────────────────┬──────────────────────────────┤
│                          │                              │
│  Editor Tabs             │  AI Session Tabs             │
│  ┌────────────────────┐  │  ┌────────────────────────┐  │
│  │ file1.py          │  │  │ Session abc123        │  │
│  │ file2.py          │  │  │ Session def456        │  │
│  └────────────────────┘  │  └────────────────────────┘  │
│                          │                              │
│  [Code Editor]           │  [Chat Display]              │
│  - Line numbers          │  - Markdown rendering        │
│  - Syntax highlighting   │  - LaTeX support             │
│  - AI integration hooks  │  - Code blocks               │
│                          │                              │
│                          │  [Input Field]               │
│                          │  [Send Button]               │
│                          │                              │
├──────────────────────────┴──────────────────────────────┤
│ Status Bar: branch info, file status, etc               │
└─────────────────────────────────────────────────────────┘
```

### LLM Integration

- Backend: OpenRouter (supports multiple models)
- Primary model: Claude 3.5 Sonnet
- Tool calling via OpenAI-compatible API
- Streaming responses (future)
- Context management (future)

### Commit Workflow

When AI completes a task:
1. Collect all file changes
2. Update session state file
3. Create commit on session branch with both
4. Use smaller LLM to generate commit message
5. User can review, amend, or revert

For concurrent sessions:
- Each session works on its own branch
- User changes on main branch
- When session completes, can rebase onto current main
- Handles conflicts like normal git workflow

## Data Flow

### User Message → AI Response

```
User types message
  ↓
Add to session.messages
  ↓
Save session state
  ↓
Discover available tools
  ↓
Send to LLM (messages + tools)
  ↓
LLM responds (text or tool calls)
  ↓
If tool calls:
  - Execute tools
  - Add results to messages
  - Send back to LLM
  ↓
Display response
  ↓
Save session state
```

### Tool Execution

```
LLM requests tool
  ↓
Validate tool exists
  ↓
Execute: ./tools/<name> < input.json
  ↓
Capture stdout (result JSON)
  ↓
Return to LLM
```

### File Editing (via search_replace tool)

```
LLM calls search_replace
  ↓
ToolManager gets current file content from git tree
  ↓
Tool receives content, performs search/replace
  ↓
Tool returns new content
  ↓
ToolManager accumulates change in memory
  ↓
Returns success/failure to LLM
  ↓
When user approves: commit all accumulated changes atomically
```

**Key difference**: No filesystem I/O. Everything happens in git objects.

## Security Model

- Tools run as user (no privilege escalation)
- Tools can only modify files in repo
- No network access from tools (except explicit tools)
- User must approve new tools before use
- All changes are git-tracked and reversible

## Future Enhancements

### Phase 2
- Inline code suggestions in editor
- Diff view for AI changes before applying
- Multi-file refactoring tools
- Test runner integration
- Debugger integration

### Phase 3
- Real-time collaboration (multiple users)
- Cloud sync for sessions
- Plugin system for custom tools
- LSP integration for better code intelligence
- Embedded terminal

### Phase 4
- Voice input/output
- Visual programming elements
- AI pair programming mode
- Automated testing suggestions
- Performance profiling integration

## Technical Stack

- **UI**: PySide6 (Qt for Python)
- **Editor**: Custom QPlainTextEdit with syntax highlighting
- **Git**: pygit2 (libgit2 bindings) - chosen specifically for ability to create commits without touching working directory
- **LLM**: OpenRouter API
- **Markdown**: python-markdown + MathJax
- **Language**: Python 3.10+

## Why pygit2?

pygit2 (libgit2 bindings) allows us to:
- Read/write git objects directly (trees, blobs, commits)
- Create commits without checking out files
- Build trees in memory
- Work with multiple branches simultaneously
- Never touch the working directory

This is essential for the "git as source of truth" model. GitPython would require filesystem operations.

## File Structure

```
forge/
├── main.py                 # Entry point
├── requirements.txt        # Dependencies
├── README.md              # User documentation
├── DESIGN.md              # This file
├── TODO.md                # Development roadmap
├── src/
│   ├── ui/
│   │   ├── main_window.py      # Main application window
│   │   ├── editor_widget.py    # Code editor
│   │   └── ai_chat_widget.py   # AI chat interface
│   ├── git_backend/
│   │   └── repository.py       # Git operations
│   ├── llm/
│   │   └── client.py           # LLM API client
│   └── tools/
│       └── manager.py          # Tool discovery/execution
├── tools/                  # User-facing tools
│   └── search_replace.py   # Built-in edit tool
└── .forge/                # Forge metadata (tracked in git)
    └── sessions/          # Session state files
        └── <uuid>.json    # Individual session
```

## Configuration

Future: `.forge/config.json` for:
- Default LLM model
- API keys (or reference to env vars)
- Tool permissions
- UI preferences
- Git settings

## Error Handling

- All git operations wrapped in try/catch
- Tool execution timeouts (30s default)
- LLM API retries with backoff
- Graceful degradation if not in git repo
- Session recovery if corrupted

## Implementation Strategy

### Phase 1: Pure Git Operations (Current Focus)

**Goal**: Make tools work entirely in git, no filesystem I/O.

1. **TreeBuilder in ToolManager**: Accumulate file changes in memory
   ```python
   class ToolManager:
       def __init__(self, repo, session_branch):
           self.repo = repo
           self.session_branch = session_branch
           self.pending_changes = {}  # filepath -> new_content
       
       def execute_tool(self, tool_name, args):
           # Get current content from git
           current_content = self.repo.get_file_content(args['filepath'])
           # Execute tool with content
           result = tool.execute(current_content, args)
           # Store new content
           self.pending_changes[args['filepath']] = result['new_content']
   ```

2. **Atomic Commits**: When user approves, commit all changes at once
   ```python
   def commit_session_changes(self, message):
       # Build new tree from pending changes
       tree = self.repo.create_tree_from_changes(self.pending_changes)
       # Create commit
       commit = self.repo.commit_tree(tree, message)
       # Update session state file
       # Commit session state too
   ```

3. **Tool Refactoring**: Tools receive/return content, not file paths
   ```python
   # Old way (filesystem):
   def execute(args):
       with open(args['filepath'], 'r') as f:
           content = f.read()
       # ... modify content ...
       with open(args['filepath'], 'w') as f:
           f.write(new_content)
   
   # New way (git-aware):
   def execute(current_content, args):
       # ... modify content ...
       return {'new_content': new_content}
   ```

### Phase 2: Session-Git Integration

- Session state commits alongside code changes
- Proper branch management UI
- Merge/rebase workflows
- Conflict resolution

### Phase 3: Advanced Git Features

- Visual branch/commit history
- Cherry-picking
- Interactive rebase
- Diff viewer before committing

## Testing Strategy

- Unit tests for core components
- Integration tests for git operations (critical - test tree building, commits)
- UI tests for critical workflows
- Tool tests (each tool has test suite)
- End-to-end tests for common scenarios
- **Git time travel tests**: Verify checkout of old commits restores full state
