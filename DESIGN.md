# Forge Design Specification

> **⚠️ DEPRECATED:** This document is superseded by `NEW_DESIGN.md` which describes the branch-first architecture. This file is retained for historical context and some implementation details that remain relevant (VFS architecture, tool system internals, etc.).

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

Tools are Python modules in `./tools/` directory that:
- Export a `get_schema()` function returning JSON schema for LLM
- Export an `execute(vfs, args)` function that performs the operation
- **Operate on VFS abstraction, not filesystem** - read/write through VFS interface
- Are sandboxed (can only propose file changes via VFS, not execute arbitrary code)
- Must be explicitly approved before AI can use them
- Loaded via importlib for better integration and type safety

**Virtual Filesystem (VFS) Model**:

The VFS abstraction solves a critical problem: during an AI turn with multiple tool calls, 
we're not working with a pure git commit state - we're working with "commit + accumulated changes".

Two VFS implementations:
1. **GitCommitVFS** - Read-only view of a git commit (immutable)
2. **WorkInProgressVFS** - Writable layer on top of a commit
   - Accumulates changes in memory during AI turn
   - Each tool call sees: base commit + all previous tool changes
   - Can materialize to tempdir if needed (for running tests, etc.)
   - Can create new git commit from accumulated diff

**Git-Aware Tool Model**:
- Tools receive a VFS instance (usually WorkInProgressVFS)
- Tools read files via `vfs.read_file(path)` - gets current state (commit + pending changes)
- Tools write files via `vfs.write_file(path, content)` - accumulates in VFS
- ToolManager provides the VFS to all tools in a turn
- After AI turn completes, VFS.commit() creates atomic git commit
- No direct filesystem I/O during tool execution

Tool lifecycle:
1. AI proposes a new tool (writes Python module to `./tools/`)
2. User reviews and approves (commits to git, marks as approved)
3. AI can use tool autonomously - no per-use approval needed
4. Tools are versioned with the code in git
5. Tools are loaded via importlib.import_module()

**Trust Model**: Tools are reviewed once at creation/modification time. Once approved, they run autonomously. This amortizes the review cost - you pay it once, not on every use.

**Built-in Tools**: A core set of tools is always available without approval:
- `write_file` - Write complete file to VFS (create or overwrite)
- `delete_file` - Delete file from VFS
- `search_replace` - Make SEARCH/REPLACE edits to files
- `update_context` - Add/remove files from active context (batch operation)

These tools live in `src/tools/builtin/` (part of Forge itself) and are marked as `BUILTIN_TOOLS` in ToolManager. They skip approval checks and provide the essential operations needed in any repo from day one.

**Context Model**: The AI always receives:
1. **Summaries of all files** - cheap, always included
2. **Full content of active files** - files the user has open in tabs

The AI uses `update_context` to load multiple files at once when it needs full content. This minimizes round-trips and keeps context efficient.

User-created tools go in `./tools/` (repo-specific) and require approval before use.

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

**Critical Principle: Repository Must Be Committed When Control Returns to User**

When the AI finishes its turn and control returns to the user, the repository MUST be in a committed state. This applies even if there are pending user actions (like tool approvals).

- AI creates a tool → commits it → control returns to user
- User approves/rejects tool → separate commit for approval
- User sends next message → AI turn → commit → control returns

The repository is NEVER left in an uncommitted state when waiting for user input. User actions (approvals, file edits, etc.) are separate from AI turns and get their own commits.

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
ToolManager provides WorkInProgressVFS to tool
  ↓
Tool calls vfs.read_file(path) - gets commit + pending changes
  ↓
Tool performs search/replace on content
  ↓
Tool calls vfs.write_file(path, new_content)
  ↓
VFS accumulates change in memory
  ↓
Tool returns success/failure to LLM
  ↓
When AI turn completes: vfs.commit() creates atomic git commit
```

**Key insight**: VFS provides consistent view of "commit + work in progress" state.
Each tool in a turn sees the cumulative effect of all previous tools.

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

## Virtual Filesystem Architecture

### The Work-in-Progress Problem

During an AI turn with multiple tool calls, we face a fundamental challenge:
- Tool 1 modifies `file.py`
- Tool 2 needs to see Tool 1's changes to `file.py`
- But we haven't committed yet - we're accumulating changes for one atomic commit

We're not working with a pure git commit state. We're working with **"commit + patch"**.

### VFS Solution

**Abstract VFS Interface** (`src/vfs/base.py`):
```python
class VFS(ABC):
    @abstractmethod
    def read_file(self, path: str) -> str:
        """Read file content"""
        
    @abstractmethod
    def write_file(self, path: str, content: str) -> None:
        """Write file content"""
        
    @abstractmethod
    def list_files(self) -> list[str]:
        """List all files"""
        
    @abstractmethod
    def file_exists(self, path: str) -> bool:
        """Check if file exists"""
```

**GitCommitVFS** - Read-only view of a commit:
- Reads files from git tree objects
- Immutable - write operations raise error
- Used for historical commits, read-only operations

**WorkInProgressVFS** - Writable layer:
- Wraps a base GitCommitVFS
- Maintains `pending_changes: dict[str, str]` in memory
- `read_file()` checks pending_changes first, falls back to base VFS
- `write_file()` updates pending_changes
- `commit()` creates new git commit with all changes
- `materialize_to_tempdir()` creates actual filesystem for running tests/commands

### Tool Integration

Tools are Python modules, not subprocess scripts:

```python
# tools/search_replace.py
def get_schema() -> dict:
    return {...}

def execute(vfs: VFS, args: dict) -> dict:
    filepath = args['filepath']
    search = args['search']
    replace = args['replace']
    
    # Read current state (commit + pending changes)
    content = vfs.read_file(filepath)
    
    # Perform operation
    new_content = content.replace(search, replace, 1)
    
    # Write back to VFS
    vfs.write_file(filepath, new_content)
    
    return {'success': True}
```

ToolManager loads tools via importlib and provides VFS:

```python
class ToolManager:
    def __init__(self, repo, branch_name):
        self.vfs = WorkInProgressVFS(repo, branch_name)
        
    def execute_tool(self, tool_name: str, args: dict) -> dict:
        # Load tool module
        tool = importlib.import_module(f'tools.{tool_name}')
        
        # Execute with VFS
        return tool.execute(self.vfs, args)
        
    def commit_turn(self, message: str) -> str:
        # Create atomic commit from all VFS changes
        return self.vfs.commit(message)
```

### Benefits

1. **Consistent State**: Each tool sees cumulative changes from previous tools
2. **Type Safety**: Python modules with proper types, not JSON over stdin
3. **Testability**: Can mock VFS, test tools in isolation
4. **Performance**: No subprocess overhead
5. **Flexibility**: Can materialize to tempdir when needed (tests, commands)
6. **Git-First**: Still creates atomic commits, never touches working directory

## Technical Stack

- **UI**: PySide6 (Qt for Python)
- **Editor**: Custom QPlainTextEdit with syntax highlighting
- **Git**: pygit2 (libgit2 bindings) - chosen specifically for ability to create commits without touching working directory
- **VFS**: Custom abstraction for "commit + work in progress" state
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
│   ├── tools/
│   │   ├── manager.py          # Tool discovery/execution
│   │   └── builtin/            # Built-in tools (part of Forge)
│   │       ├── read_file.py
│   │       ├── write_file.py
│   │       ├── delete_file.py
│   │       ├── search_replace.py
│   │       ├── update_context.py
│   │       └── list_active_files.py
│   └── vfs/
│       ├── base.py             # VFS interface
│       ├── git_commit.py       # Read-only git VFS
│       └── work_in_progress.py # Writable VFS layer
├── tools/                  # User-created tools (repo-specific)
│   └── (empty initially)
└── .forge/                # Forge metadata (tracked in git)
    ├── approved_tools.json # Tool approval tracking
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
- AI manages its own context via tool calls (`add_file_to_context`, `remove_file_from_context`)
- Full file contents included in context for active files
- Changes to active file list saved with next commit (not immediately)
- Manual UI for file management is post-MVP (AI-driven is primary workflow)

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
