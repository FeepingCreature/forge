# Forge Design Specification

## Core Philosophy

Forge is an AI-assisted development environment where **git is the source of truth**. All AI operations are git-backed, making them safe, auditable, and reversible.

The fundamental insight: **AI time travel**. You can checkout any commit and see not just the code state, but the exact AI conversation and session state that produced those changes. This makes AI development fully auditable and reversible.

## Key Principles

1. **Git is More Fundamental Than Filesystem**: The git repository is the primary reality. The filesystem is just a view. AI sessions work entirely within git - no temporary directories, no ephemeral state.

2. **Tool-Based AI**: LLMs can only interact through approved, sandboxed tools that operate on git state, not filesystem state. Tools are reviewed once when created/modified, then run autonomously.

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
2. User sends message to AI
3. AI analyzes task, may use tools (read files, run commands, etc.)
4. AI proposes all changes as SEARCH/REPLACE blocks in one response
5. Changes are applied and committed atomically (code + session state)
6. Control returns to user
7. Repeat until task complete
8. User merges session branch to main (or rebases, cherry-picks, etc.)

**One Commit Per Cycle**: Each AI interaction produces exactly one commit. This:
- Keeps costs down (AI must plan all changes upfront)
- Creates clean, atomic history
- Makes rollback trivial (just reset to previous commit)
- Ensures AI thinks through the full solution before acting

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
3. AI can use tool autonomously - no per-use approval needed
4. Tools are versioned with the code in git

**Trust Model**: Tools are reviewed once at creation/modification time. Once approved, they run autonomously. This amortizes the review cost - you pay it once, not on every use.

### Session Management

Each session maintains:
- Unique session ID (UUID)
- Associated git branch
- Message history (user + assistant)
- Tool call history
- **Active files list**: Files fully loaded into context (user or AI can expand)
- **Repository summary**: Cheap-model-generated per-file summaries for context

**Context Management**:
- Repository summaries always included (cheap, broad context)
- Active files fully included (expensive, detailed context)
- User can expand/collapse files
- AI can request file expansion
- Active file changes saved with next commit (not immediately)

**Agent Flow**:
1. AI receives message with repo summaries + active files
2. AI may use 2-3 tool calls (read files, run commands, etc.)
3. AI proposes all code changes via SEARCH/REPLACE blocks
4. AI ends turn, returning control to user
5. All changes committed atomically

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

**One Commit Per AI Turn**:
1. User sends message
2. AI analyzes, uses tools, proposes changes
3. All SEARCH/REPLACE blocks applied to git tree in memory
4. Session state updated (messages, active files)
5. Single atomic commit created (code + session state)
6. Smaller LLM generates commit message
7. Control returns to user

**What Gets Committed**:
- All code changes from SEARCH/REPLACE blocks
- Updated session state (`.forge/sessions/<id>.json`)
- Active files list changes (if modified this turn)
- Tool execution results (logged in session state)

**Cost Control**:
- AI must plan all changes before committing
- Can't do incremental "try and see" approaches
- Forces complete solutions per turn
- Repository summaries provide broad context cheaply

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
- Tools can only modify files in repo (via git, not filesystem)
- No network access from tools (except explicit tools)
- User must approve new/modified tools before first use
- Once approved, tools run autonomously without per-use approval
- All changes are git-tracked and reversible
- Tool code itself is versioned in git, so tool changes are auditable

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

**Goal**: Make tools work entirely in git, no filesystem I/O. One commit per AI turn.

1. **SessionManager**: Coordinates AI turns and commits
   ```python
   class SessionManager:
       def __init__(self, repo, session):
           self.repo = repo
           self.session = session
           self.pending_changes = {}  # Accumulate during AI turn
           self.active_files = set()  # Files in context
       
       def process_ai_turn(self, user_message):
           # 1. Add user message to session
           # 2. Get repo summaries + active file contents
           # 3. Send to LLM with tools
           # 4. Accumulate all SEARCH/REPLACE changes
           # 5. Commit everything atomically
           # 6. Return control to user
   ```

2. **Context Management**: Cheap summaries + selective full files
   ```python
   def build_context(self):
       context = {
           'summaries': self.get_repo_summaries(),  # All files, cheap
           'active_files': {
               path: self.repo.get_file_content(path)
               for path in self.active_files
           }
       }
       return context
   ```

3. **Atomic Commits**: All changes in one commit
   ```python
   def commit_ai_turn(self):
       # Build tree with all pending changes
       tree = self.repo.create_tree_from_changes(self.pending_changes)
       
       # Update session state
       session_state = self.session.get_session_data()
       tree = self.repo.add_file_to_tree(tree, 
           f'.forge/sessions/{self.session.id}.json',
           json.dumps(session_state))
       
       # Generate commit message with cheap model
       message = self.generate_commit_message()
       
       # Create commit
       self.repo.commit_tree(tree, message, self.session.branch_name)
       
       # Clear pending changes
       self.pending_changes = {}
   ```

4. **Tool Refactoring**: Tools receive/return content, not file paths
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

### Phase 2: Context & Summary System

**Repository Summaries**:
- Use cheap model (e.g., Claude Haiku) to generate per-file summaries
- Summaries cached and regenerated only when files change
- Always included in AI context (low token cost, broad awareness)
- Format: `path/to/file.py: "Brief description of purpose and key functions"`

**Active Files**:
- User can expand files into full context
- AI can request expansion via tool call
- Full file contents included in context
- Changes to active file list saved with next commit (not immediately)

**Cost Optimization**:
- Summaries: ~50 tokens per file × 100 files = 5K tokens (cheap)
- Active files: Full content only for relevant files
- AI must work with summaries first, expand only when needed
- One commit per turn prevents wasteful back-and-forth

### Phase 3: Session-Git Integration

- Session state commits alongside code changes ✓
- Proper branch management UI
- Merge/rebase workflows
- Conflict resolution

### Phase 4: Advanced Git Features

- Visual branch/commit history
- Cherry-picking
- Interactive rebase
- Diff viewer before committing
- Commit message editing
- Amend last commit

## Testing Strategy

- Unit tests for core components
- Integration tests for git operations (critical - test tree building, commits)
- UI tests for critical workflows
- Tool tests (each tool has test suite)
- End-to-end tests for common scenarios
- **Git time travel tests**: Verify checkout of old commits restores full state
