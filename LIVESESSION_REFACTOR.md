# LiveSession Refactoring Plan

## Problem Statement

The current session management has overlapping responsibilities and duplicate state that gets out of sync:

- `SessionRunner` owns `_child_sessions`, `_parent_session`, execution state
- `SessionRegistry` has `SessionInfo` with duplicate `child_sessions`, `parent_session`, `state`
- `SessionInfo` is loaded from `session.json` at startup and becomes stale immediately
- When code needs to check child states, it's unclear which source to use
- Result: parent sessions fail to resume when children complete

## Design Principles

1. **Single source of truth** - No duplicate state that can diverge
2. **Tabs don't own sessions** - Opening/closing tabs is purely UI attachment
3. **Active sessions must be loaded** - Any session in `WAITING_CHILDREN` or `RUNNING` state has a `LiveSession`
4. **Structural guarantees** - Parent/child coordination is impossible to break

## Key Entities

### Session Branch
A git branch with `.forge/session.json`. The persistent identity.

### LiveSession (renamed from SessionRunner)
The in-memory representation of a session. Owns:
- `messages` - Conversation history
- `_child_sessions`, `_parent_session` - Relationships
- `_pending_wait_call` - Blocked wait state
- `_state` - Current lifecycle state
- The run loop (stream → tools → repeat)

### Tab/UI
A visual representation that *attaches* to a `LiveSession` to observe/interact.
Purely observational - doesn't affect session lifecycle.

### SessionRegistry
Simple index: `branch_name → LiveSession | None`

## Session States

```
On-disk only (no LiveSession loaded):
  SUSPENDED - session.json exists, not in memory
              UI can show read-only history
              Any interaction loads it first

In-memory (has LiveSession):
  IDLE             - ready for input
  RUNNING          - actively streaming/executing tools
  WAITING_INPUT    - AI asked a question, waiting for user
  WAITING_CHILDREN - blocked on child sessions
  COMPLETED        - done() called with no question
  ERROR            - something went wrong
```

## Lifecycle Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│   SUSPENDED                        LOADED                       │
│   (no LiveSession)                 (has LiveSession)            │
│                                                                 │
│   ┌─────────┐    load()           ┌──────────────────────────┐ │
│   │         │ ──────────────────► │                          │ │
│   │ .forge/ │                     │      LiveSession         │ │
│   │ session │ ◄────────────────── │      (idle, running,     │ │
│   │  .json  │    unload()         │       waiting_children,  │ │
│   │         │    (only if idle)   │       waiting_input,     │ │
│   └─────────┘                     │       completed, error)  │ │
│                                   └──────────────────────────┘ │
│                                              ▲                  │
│                                              │ attach/detach    │
│                                              ▼                  │
│                                   ┌──────────────────────────┐ │
│                                   │         Tab/UI           │ │
│                                   │   (purely observational) │ │
│                                   └──────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

## Invariants

1. **WAITING_CHILDREN implies loaded** - Any session with `state: waiting_children` in session.json must have a LiveSession loaded, because we need the parent runner to wake up when children complete.

2. **Children of waiting parents are loaded** - If parent is WAITING_CHILDREN, all its children are also loaded (so they can notify parent on completion).

3. **Tabs attach, not own** - Closing a tab detaches from LiveSession. The LiveSession continues running/waiting headlessly.

4. **Unload only when safe** - A LiveSession can only be unloaded when state is IDLE, COMPLETED, or ERROR, and no tab is attached.

## Registry Simplification

```python
class SessionRegistry:
    """Index of loaded sessions."""
    
    _sessions: dict[str, LiveSession]  # branch → LiveSession
    
    def load(self, branch_name: str) -> LiveSession:
        """Load session from disk, creating a LiveSession."""
        if branch_name in self._sessions:
            return self._sessions[branch_name]
        session = self._create_from_disk(branch_name)
        self._sessions[branch_name] = session
        return session
    
    def unload(self, branch_name: str) -> bool:
        """Unload session if safe. Returns success."""
        session = self._sessions.get(branch_name)
        if not session:
            return True
        if session.state not in (IDLE, COMPLETED, ERROR):
            return False  # Can't unload active session
        if session.has_attached_ui():
            return False  # Tab is watching
        del self._sessions[branch_name]
        return True
    
    def get(self, branch_name: str) -> LiveSession | None:
        """Get loaded session or None."""
        return self._sessions.get(branch_name)
    
    def ensure_loaded(self, branch_name: str) -> LiveSession:
        """Load if needed, return session."""
        return self.load(branch_name)
    
    def get_all_branches(self) -> list[str]:
        """List all session branches (loaded or not)."""
        # Scans git branches for .forge/session.json
        ...
```

## SessionInfo Goes Away

The current `SessionInfo` dataclass is removed entirely. It was duplicating state that belongs in `LiveSession`.

For UI display of unloaded sessions (e.g., session dropdown), we read directly from `session.json` as a pure display operation - never for operational logic.

## Parent/Child Coordination

### Spawning a Child

```python
# In LiveSession
def _on_spawn_session_result(self, child_branch: str):
    # Track child in our list
    self._child_sessions.append(child_branch)
    # Child's LiveSession is created and registered automatically
```

### Child Completes

```python
# In LiveSession
def _finish_turn(self):
    self.state = SessionState.IDLE
    self._persist()
    
    if self._parent_session:
        # Parent MUST be loaded (invariant: waiting parent is always loaded)
        parent = SESSION_REGISTRY.get(self._parent_session)
        if parent and parent.state == SessionState.WAITING_CHILDREN:
            parent.child_ready(self.branch_name)
```

### Why This Can't Break

1. Parent spawns child → parent is RUNNING → parent has LiveSession
2. Parent calls wait_session → parent becomes WAITING_CHILDREN → still has LiveSession (can't unload)
3. Child runs → child has LiveSession
4. Child completes → looks up parent via registry → parent LiveSession guaranteed to exist
5. Parent wakes up

The invariant (WAITING_CHILDREN implies loaded) makes the failure mode impossible.

## App Restart Behavior

On startup:

1. **Scan all session branches** - Find all branches with `.forge/session.json`

2. **Load active sessions** - For each with `state: waiting_children`:
   - Load a LiveSession
   - Leave in WAITING_CHILDREN state (don't auto-run)
   - Also load its children
   - If any child is COMPLETED, parent can resume when user interacts

3. **Handle crashed sessions** - For each with `state: running`:
   - This means app crashed mid-turn
   - Load a LiveSession but set state to IDLE
   - User sees conversation and can retry

4. **Leave idle sessions suspended** - For `idle`/`completed`/`error`:
   - Don't auto-load
   - Load on-demand when user opens tab

**Key: We never auto-start execution on restart.** We just restore the in-memory structure so parent/child coordination works when the user resumes.

## Migration Steps

### Phase 1: Rename and Consolidate

1. Rename `SessionRunner` → `LiveSession`
2. Rename `SessionRunner._child_sessions` → `LiveSession.child_sessions` (public)
3. Rename `SessionRunner._parent_session` → `LiveSession.parent_session` (public)
4. Remove `SessionInfo` dataclass
5. Update `SessionRegistry` to simpler interface

### Phase 2: Fix Registry Queries

1. `get_children_states()` → queries `LiveSession.child_sessions` directly
2. `notify_parent()` → simplified, parent guaranteed to exist
3. Remove `refresh_branch()` - not needed when state is authoritative

### Phase 3: Startup Loading

1. Implement `load_active_sessions_on_startup()`
2. Load all WAITING_CHILDREN sessions and their children
3. Normalize RUNNING → IDLE for crashed sessions

### Phase 4: Tab Attach/Detach

1. Ensure tabs only attach/detach, never create/destroy LiveSession
2. Add `LiveSession.has_attached_ui()` check
3. Update unload logic to respect attached tabs

## Files to Modify

- `forge/session/runner.py` → rename to `live_session.py`, class `LiveSession`
- `forge/session/registry.py` → simplify, remove `SessionInfo`
- `forge/session/startup.py` → update to new model
- `forge/session/__init__.py` → update exports
- `forge/ui/ai_chat_widget.py` → attach/detach pattern
- `forge/ui/branch_workspace.py` → use registry.ensure_loaded()
- `forge/tools/builtin/wait_session.py` → use LiveSession directly
- `forge/tools/builtin/spawn_session.py` → use LiveSession directly
- `tests/test_session_spawn.py` → update for new API