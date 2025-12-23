# Refactoring Opportunities

A comprehensive audit of code duplication, design smells, and style inconsistencies.

---

## 1. Code Duplication

### 1.1 ~~Summary Generation Prompt~~ ✅ FIXED

**Files:** `forge/session/manager.py`

~~The exact same LLM prompt for generating file summaries appears twice.~~

**Fixed:** Extracted to `_build_summary_prompt(filepath, content)`.

---

### 1.2 ~~Fuzzy Match Implementation~~ ✅ FIXED

**Files:** `forge/ui/command_palette.py`, `forge/ui/quick_open.py`

~~Both files implement nearly identical `fuzzy_match` functions.~~

**Fixed:** Extracted to `forge/ui/fuzzy.py`.

---

### 1.3 ~~Grep Exclusion Logic~~ ✅ FIXED

**Files:** `forge/tools/builtin/grep_open.py`, `forge/tools/builtin/grep_context.py`

~~Both tools have identical code for exclusions and filtering.~~

**Fixed:** Extracted to `forge/tools/builtin/grep_utils.py`.

---

### 1.4 Tool Schema Boilerplate (LOW)

**Files:** All tools in `forge/tools/builtin/`

Every tool has nearly identical `get_schema()` structure. The only variations are name, description, and parameters.

**Fix:** Consider a decorator or base class:
```python
@tool_schema(name="write_file", description="...", params={...})
def execute(vfs, args): ...
```

---

### 1.5 Test Command Discovery (LOW)

**Files:** `forge/tools/builtin/run_tests.py`, `tools/check.py`

Both tools discover project type (Makefile, pytest, package.json, etc.) with similar but not identical logic.

**Fix:** Extract project detection to a shared utility.

---

## 2. Design Smells

### 2.1 God Object: `SessionManager` (HIGH)

**File:** `forge/session/manager.py` (~450 lines)

SessionManager does too many things:
- Manages active files and context
- Handles LLM client creation
- Generates summaries (with its own LLM calls)
- Manages prompt construction
- Handles commits
- Caches summaries to disk

**Fix:** Split into:
- `ContextManager` - active files, prompt construction
- `SummaryService` - summary generation and caching
- Keep `SessionManager` as coordinator

---

### 2.2 God Object: `AIChatWidget` (HIGH)

**File:** `forge/ui/ai_chat_widget.py` (~1400 lines)

This widget handles:
- UI rendering (HTML generation, streaming updates)
- Tool approval workflow
- LLM streaming coordination
- Tool execution coordination
- Session state management
- Turn rewind/fork logic
- System notifications

**Fix:** Split into:
- `ChatRenderer` - HTML generation, streaming display
- `ToolApprovalManager` - approval workflow
- `AITurnCoordinator` - streaming and tool execution
- Keep `AIChatWidget` as thin orchestrator

---

### 2.3 Circular Import Prevention via TYPE_CHECKING (MEDIUM)

**Files:** Many, especially VFS and tools

Heavy use of `if TYPE_CHECKING:` imports suggests architectural coupling issues. Examples:
- `WorkInProgressVFS` imports `ForgeRepository` 
- Tools import `WorkInProgressVFS`
- Session imports tools

**Fix:** Consider dependency injection or interface abstractions to reduce coupling.

---

### 2.4 Mixed Responsibilities in `ToolManager` (MEDIUM)

**File:** `forge/tools/manager.py`

ToolManager handles:
- Tool discovery
- Tool approval (including persisting to JSON)
- Tool execution
- VFS ownership
- Module loading

**Fix:** Split approval tracking to `ToolApprovalService`.

---

### 2.5 Settings Access Pattern (LOW)

**Files:** Throughout codebase

Settings are passed around and accessed inconsistently:
- Sometimes via `self.settings.get("path")`
- Sometimes via `self.settings.get_api_key()`
- Sometimes stored on multiple objects

**Fix:** Consider a more structured configuration object or dependency injection.

---

## 3. Style Inconsistencies

### 3.1 Docstring Format (MEDIUM)

Some files use full docstrings:
```python
def foo():
    """
    Long description.
    
    Args:
        x: The x value
    """
```

Others use single-line:
```python
def foo():
    """Short description"""
```

**Files with mixed styles:** `forge/git_backend/repository.py`, `forge/session/manager.py`

**Fix:** Adopt consistent style (Google-style docstrings recommended).

---

### 3.2 Return Type Annotations (MEDIUM)

Inconsistent use of `-> None` for functions that return nothing:
- Some have it: `def foo() -> None:`
- Some omit it: `def foo():`

**Files:** `forge/ui/` in particular has inconsistent usage.

**Fix:** Always include `-> None` (already enforced by mypy's `disallow_incomplete_defs`).

---

### 3.3 Import Organization (LOW)

Most files follow the pattern:
```python
import stdlib
from stdlib import x

import thirdparty
from thirdparty import y

from forge.x import z
```

But some mix them or have unused imports (caught by ruff but still present in `__init__.py` files by design).

---

### 3.4 Error Handling Style (MEDIUM)

Inconsistent patterns:
- Some return `{"success": False, "error": "msg"}`
- Some raise exceptions
- Some return None on error

Tools are consistent (return dict), but internal code varies.

**Fix:** Establish convention: tools return dicts, internal code raises exceptions.

---

### 3.5 Signal Naming (LOW)

**File:** `forge/ui/` various

Some signals use past tense: `file_saved`, `ai_turn_finished`
Some use present: `merge_requested`, `context_changed`
Some use `_requested` suffix, some don't

**Fix:** Adopt convention: 
- `*_requested` for user intent
- `*_changed` for state changes  
- `*_finished` for completed operations

---

## 4. Specific Issues

### 4.1 Magic Numbers

**File:** `forge/ui/file_explorer_widget.py`
```python
LARGE_FILE_THRESHOLD = 10000
```
This is fine, but similar thresholds appear elsewhere without constants:
- `forge/session/manager.py`: `max_chars = 10000` (truncation for summary)
- `forge/prompts/manager.py`: `TOKEN_THRESHOLD = 30000`

**Fix:** Centralize threshold constants or make configurable.

---

### 4.2 Thread Safety Assertions

**File:** `forge/vfs/base.py`

Good pattern with `claim_thread()`/`release_thread()`, but:
- Not all VFS methods call `_assert_owner()`
- `GitCommitVFS` doesn't inherit the thread checks

**Fix:** Ensure consistent thread safety across all VFS implementations.

---

### 4.3 Hardcoded Strings

**Files:** Various

- Branch prefixes: `"forge/session/"` appears in multiple places
- File paths: `".forge/session.json"`, `".forge/approved_tools.json"`
- Model names: `"anthropic/claude-3-haiku"` as fallback

**Fix:** Centralize in a constants module.

---

### 4.4 HTML Generation in Python

**File:** `forge/ui/ai_chat_widget.py`, `forge/ui/diff_view.py`

Large amounts of HTML/CSS/JS generated via string concatenation. This is error-prone and hard to maintain.

**Fix:** Consider:
- Jinja2 templates for complex HTML
- Separate CSS/JS files loaded as resources
- At minimum, extract to dedicated rendering module (partially done with `diff_view.py`)

---

### 4.5 Inconsistent VFS Type Hints

Some tools type hint as `VFS`, others as `WorkInProgressVFS`:
- `write_file.py`: uses `VFS`
- `commit.py`: uses `WorkInProgressVFS`
- `undo_edit.py`: uses `WorkInProgressVFS`

**Fix:** Tools that need WIP-specific methods should hint `WorkInProgressVFS`. Read-only tools should use `VFS`.

---

## 5. Missing Abstractions

### 5.1 No LLM Service Layer

LLM calls happen in multiple places:
- `forge/llm/client.py` - the actual client
- `forge/session/manager.py` - creates clients, makes summary calls
- `forge/ui/ai_chat_widget.py` - streaming through workers
- `forge/ui/ask_widget.py` - direct httpx calls (!!)
- `forge/ui/code_completion.py` - direct httpx calls

**Fix:** Create `LLMService` that all components use.

---

### 5.2 No Unified Progress/Status System

Progress reporting is ad-hoc:
- Summary generation: callback function
- Streaming: Qt signals
- Tool execution: Qt signals
- Check tool: none (blocks)

**Fix:** Consider unified progress abstraction.

---

### 5.3 No Configuration Schema

Settings are a loose dict with magic string paths. No validation, no defaults in one place.

**Fix:** Define a dataclass-based config schema with validation.

---

## 6. Quick Wins

These can be fixed immediately with minimal risk:

1. ~~**Extract `fuzzy_match`** to shared module (2 files affected)~~ ✅ Done - `forge/ui/fuzzy.py`
2. ~~**Extract grep helpers** (2 files affected)~~ ✅ Done - `forge/tools/builtin/grep_utils.py`
3. **Add constants module** for magic strings
4. ~~**Consolidate summary prompt** (1 file, 2 locations)~~ ✅ Done - `_build_summary_prompt()`
5. **Fix `ask_widget.py`** to use `LLMClient` instead of raw httpx

---

## 7. Larger Refactors (Future)

These require more planning:

1. **Split `SessionManager`** - significant architectural change
2. **Split `AIChatWidget`** - complex Qt signal rewiring
3. **Unified LLM service** - needs careful threading consideration
4. **Template-based HTML** - requires asset bundling decisions

---

## Summary by Priority

| Priority | Count | Examples |
|----------|-------|----------|
| HIGH | 4 | SessionManager god object, AIChatWidget god object, duplicate summary prompt, grep duplication |
| MEDIUM | 8 | Fuzzy match duplication, ToolManager split, docstring style, error handling |
| LOW | 6 | Tool schema boilerplate, import organization, signal naming |
