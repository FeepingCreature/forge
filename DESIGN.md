# Forge Design Specification

## Core Philosophy

Forge is an AI-assisted development environment where **git is the source of truth**. All AI operations are git-backed, making them safe, auditable, and reversible.

## Key Principles

1. **Git-First Architecture**: The filesystem is a view of git state, not the primary source of truth
2. **Tool-Based AI**: LLMs can only interact through approved, sandboxed tools
3. **Opt-In Everything**: No automatic changes; user must approve all AI actions
4. **Session Persistence**: AI conversations are part of the git history
5. **Concurrent Sessions**: Multiple AI tasks can run on separate branches simultaneously

## Architecture

### Git Backend

- Each AI session gets its own branch: `forge/session/<session_id>`
- Session state stored in `.forge/sessions/<session_id>.json` (tracked in git)
- AI commits include both code changes AND session state updates
- Can checkout any commit to see exact AI conversation at that point
- Working directory can be "dirty" - AI works on clean git state

### Tool System

Tools are executable scripts in `./tools/` directory that:
- Accept `--schema` flag to output JSON schema for LLM
- Accept JSON input via stdin
- Output JSON results via stdout
- Are sandboxed (can only modify files, not execute arbitrary code)
- Must be explicitly approved before AI can use them

Tool lifecycle:
1. AI proposes a new tool (writes code to `./tools/`)
2. User reviews and approves (makes it executable)
3. AI can use tool in next interaction
4. Tools are versioned with the code

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
Tool reads file from disk
  ↓
Performs search/replace
  ↓
Writes back to disk
  ↓
Returns success/failure
  ↓
(Later: stage changes for commit)
```

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
- **Git**: pygit2 (libgit2 bindings)
- **LLM**: OpenRouter API
- **Markdown**: python-markdown + MathJax
- **Language**: Python 3.10+

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

## Testing Strategy

- Unit tests for core components
- Integration tests for git operations
- UI tests for critical workflows
- Tool tests (each tool has test suite)
- End-to-end tests for common scenarios
