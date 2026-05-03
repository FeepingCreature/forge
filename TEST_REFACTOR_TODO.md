# Test Harness Refactor — Plan

## Why

Current tests for the inline pipeline / VFS / prompt-flow path are awkward
to write. Each test bootstraps a git repo, builds multi-line assistant
content with literal `<replace>` tags (escape hell), and calls low-level
functions across several layers. Adding a *new* test costs ~30 lines of
setup before the assertion you actually care about.

Two underlying problems make this hard:

1. **No seam for the LLM.** `LiveSession` directly imports `LLMClient` and
   constructs it inline. Tests can't substitute a scripted response.

2. **No seam for threading.** `LiveSession` directly creates `QThread`,
   `moveToThread`, `quit`, `wait` — three times, one per worker. Tests
   can't run the pipeline synchronously.

So the right fix is **first** to introduce those seams in production code,
**then** build the harness on top.

## Goal — what the final tests should look like

```python
def test_failed_test_preserves_edits_in_ai_view(session):
    session.given_files({
        "a.py": "def foo():\n    return 1\n",
        "b.py": "def bar():\n    return 10\n",
    })
    session.given_files_in_context("a.py", "b.py")
    session.given_failing_tests()

    session.user_says("Fix foo to return 2 and bar to return 20, then run tests.")
    session.ai_says("""
        I'll fix both functions.

        @edit a.py
            old: return 1
            new: return 2

        @edit b.py
            old: return 10
            new: return 20

        @run_tests

        Both updated.
    """)

    assert session.last_turn.failed_at == "run_tests"
    assert session.vfs["a.py"] == "def foo():\n    return 2\n"
    assert session.ai_view("a.py") == "def foo():\n    return 2\n"
    assert "old marker" not in session.next_prompt_text()
```

The `session` fixture absorbs: repo bootstrap, test-runner stubbing,
DSL → inline-XML compilation, full pipeline execution, and convenient
assertion lenses (`vfs[path]`, `ai_view(path)`, `next_prompt_text()`,
`last_turn`).

## Phased plan

Each phase is a self-contained change that lands independently. We do
**one phase per session**, and within a phase we do one action at a time
unless explicitly batched.

---

### Phase 1 — Extract `TaskRunner`

Introduce a seam for "run work off the main thread." This alone unlocks
~80% of the harness because tests can swap in `SyncTaskRunner` and have
the entire pipeline execute straight-line.

**Scope: small.** No worker rewrites. No LLM changes. Just the seam.

**Files added:**
- `forge/runtime/__init__.py`
- `forge/runtime/tasks.py`

**Contents:**
- `TaskRunner` protocol with one generic method:
  ```python
  def submit(
      work: Callable[[], T],
      on_result: Callable[[T], None],
      on_error: Callable[[str], None],
  ) -> Handle
  ```
  Plus `cancel_all()`.
- `QtTaskRunner` — wraps a `QThread` running an internal worker; posts
  callbacks back to the caller's thread via Qt's queued signals.
- `SyncTaskRunner` — runs `work()` on the calling thread, calls
  `on_result` synchronously. Used by tests.

**Files changed:**
- `forge/session/live_session.py` — accept optional `task_runner` in
  `__init__` (defaults to `QtTaskRunner()`). Replace the three
  `QThread`/`moveToThread`/`connect` blocks (`_process_llm_request`,
  `_start_inline_command_execution`, `_execute_tool_calls`) with
  `self._tasks.submit(...)`. The existing workers (`StreamWorker`,
  `InlineCommandWorker`, `ToolExecutionWorker`) still exist — they
  become the thing run *inside* `submit`'s `work` callable. Adapter
  shim translates their signal-based output into TaskRunner callbacks.
- `forge/session/manager.py` — same treatment for `SummaryWorker`.

**Out of scope for Phase 1:**
- Changing the workers themselves (still QObjects).
- Touching the LLM client.

**Done when:**
- `LiveSession` no longer imports `QThread` directly.
- All existing tests still pass unchanged.
- A new `tests/test_task_runner.py` covers `SyncTaskRunner` (success,
  error, cancellation) and a smoke test of `QtTaskRunner` with a real
  Qt event loop.

---

### Phase 2 — Extract `LLMBackend`

Introduce a seam for "talk to the LLM." Now tests can script responses
without mocking `requests` or `LLMClient`.

**Scope: medium.** New protocol + production wrapper + scripted impl.
One call site changes.

**Files added:**
- `forge/runtime/llm_backend.py`

**Contents:**
- `StreamEvent` union: `ChunkEvent(text)`, `ToolCallDeltaEvent(index,
  tool_call)`, `FinishedEvent(content, tool_calls)`. Plain dataclasses,
  no Qt.
- `LLMBackend` protocol:
  ```python
  def stream(
      messages: list[dict],
      tools: list[dict] | None,
  ) -> Iterator[StreamEvent]
  ```
- `OpenRouterBackend` — production impl wrapping the existing
  `LLMClient.chat_stream`. Translates the SSE stream into `StreamEvent`s.
- `ScriptedBackend` — test impl. API:
  ```python
  backend.queue_response(content="...", tool_calls=[...])
  backend.queue_error("rate limited")
  ```
  Each call to `stream()` consumes one queued item. Asserts queue is
  drained at end (configurable via fixture).

**Files changed:**
- `forge/session/live_session.py` — accept optional `llm_backend` in
  `__init__` (defaults to `OpenRouterBackend(...)` constructed from
  `session_manager.settings`). `_process_llm_request` calls
  `self._llm.stream(...)` instead of constructing `LLMClient` and
  `StreamWorker` directly.
- `forge/ui/chat_workers.py` — `StreamWorker` becomes a thin adapter
  around `LLMBackend.stream()` for now. Or possibly deleted if Phase 3
  lands soon after.

**Out of scope for Phase 2:**
- Other workers (still around).
- `chat_workers.py` cleanup.

**Done when:**
- `LiveSession` no longer imports `LLMClient`.
- New tests in `tests/test_llm_backend.py` cover `ScriptedBackend`
  behavior (queue draining, errors, ordering).
- A first end-to-end test using both `SyncTaskRunner` and
  `ScriptedBackend`: scripted response with one tool call → tool
  executes → next scripted response → assert state. ~30 lines.

---

### Phase 3 — De-Qt the workers

Now that the seams exist, rewrite the worker bodies as plain functions
returning data. Removes the QObject inheritance and signal plumbing
that's no longer needed.

**Scope: medium-large.** Mechanical but touches a lot of code. Lots of
`Signal()` declarations and `.emit()` calls go away.

**Files moved/renamed:**
- `forge/ui/chat_workers.py` → split into:
  - `forge/runtime/inline_executor.py` — function that takes
    `(vfs, commands, content)` and returns
    `(results, failed_index)`. Just calls
    `execute_inline_commands_with_parse_check`. Probably ~10 lines.
  - `forge/runtime/tool_executor.py` — function that takes tool calls
    and the tool manager, yields `ToolStartedEvent`/`ToolFinishedEvent`,
    then returns the full results list.
  - `forge/runtime/summarizer.py` — `SummaryWorker`'s logic as a
    plain function with a progress callback.
- The Qt-coupled `StreamWorker` from Phase 2 becomes unused and is
  deleted.

**Files changed:**
- `forge/session/live_session.py` — call sites switch from
  `self._tasks.submit(StreamWorker(...).run, ...)` to
  `self._tasks.submit(lambda: stream_llm(self._llm, messages, tools), ...)`.
  Same for inline and tool execution.
- `forge/session/manager.py` — same for summary generation.
- `forge/ui/chat_workers.py` — deleted (or kept as an empty
  back-compat shim if anything outside the repo imports from it).

**Done when:**
- No `QObject` subclasses outside `forge/session/` and `forge/ui/`.
- Worker logic is testable as plain functions (no Qt event loop needed).
- `tests/test_inline_executor.py` and `tests/test_tool_executor.py`
  cover the extracted functions directly.

---

### Phase 4 — Build the harness + DSL

With both seams in place, the harness shrinks dramatically. Estimated
~250 lines instead of the ~500 it would have been against the current
code.

**Files added:**
- `tests/harness/__init__.py`
- `tests/harness/session.py` — the `SessionTestHarness` class.
- `tests/harness/dsl.py` — DSL parser (`@edit ...`, `@run_tests`, etc.
  → inline XML).
- `tests/harness/repo.py` — git-repo bootstrap helpers (extract from
  duplicated `repo` fixtures across existing tests).
- `tests/conftest.py` — `session` fixture that yields a configured
  `SessionTestHarness`.

**Harness API (final):**

Setup (chainable, all return `self`):
- `.given_files({path: content, ...})` — seed the repo.
- `.given_passing_tests()` / `.given_failing_tests()` — write a
  hermetic Makefile that exits 0 / exits 1 without touching files.
- `.given_files_in_context(*paths)` — populate `active_files` and
  prompt manager with current contents.
- `.given_user_message(text)` — append to prompt manager.

Actions:
- `.user_says(text)` — full `LiveSession.send_message()` path.
- `.ai_says(dsl_or_raw)` — compile DSL → inline XML, queue as next
  scripted response, then run the pipeline to completion (or to the
  next yield point).

Inspection:
- `.vfs[path]` — read VFS (with pending changes).
- `.ai_view(path)` — what the next prompt would show for that file.
- `.next_prompt_text()` — full rendered user-side text from
  `to_messages()`.
- `.last_turn` — `TurnResult(succeeded, failed_at, results,
  annotated_assistant_content)`.
- `.prompt_blocks()` — raw blocks for advanced assertions.
- `.messages` — session message list.

**DSL:**

```
@edit <path>
    old: <text>
    new: <text>
@write <path>
    <content>
@run_tests
@check
@commit message=<text>
@delete <path>
free-form prose stays as-is between commands
```

Indentation-based: `old:` / `new:` continue until next `@` or dedent.
Compiles to nonced inline XML so the harness body never contains
literal `<replace>` tags.

**Migration:**
- Port `tests/test_inline_pipeline_vfs.py` and
  `tests/test_prompt_manager_inline_failure.py` to the harness as the
  first proof points. The diff should be dramatic — both tests should
  shrink to ~15 lines each and read top-to-bottom as a flow.
- Leave `tests/test_inline_edit.py` alone (those are unit tests for
  the parser, not flow tests).
- Leave `tests/test_branch_workspace_vfs.py` alone (VFS lifecycle, not
  flow tests).
- `tests/test_queued_messages.py` likely benefits from harness — port
  in a follow-up.

**Done when:**
- `session` fixture in `conftest.py` works.
- Two existing tests are ported and pass.
- README section in `tests/harness/__init__.py` documents the API and
  DSL with examples.

---

## Open questions to resolve as we go

1. **`QtTaskRunner` cancellation semantics.** Today `LiveSession.cancel()`
   calls `_cleanup_threads()` which `quit()`s and `wait()`s with a 3s
   timeout, then `terminate()`s. Should `TaskRunner.cancel_all()` mirror
   this, or do we want a softer "request stop" flag the work function
   checks? Decide in Phase 1.

2. **`ScriptedBackend` strictness.** Should an unmet expectation (queued
   response never consumed) raise at fixture teardown? Default yes;
   provide opt-out for tests that intentionally bail mid-flow. Decide in
   Phase 2.

3. **DSL multi-line bodies.** What if `old:` text contains a line that
   starts with `@`? Need an escape or a fenced form. Probably:
   ```
   @edit a.py
       old:::
       multi-line body
       can contain @ at-signs and anything else
       :::
       new:::
       likewise
       :::
   ```
   triple-colon as the fence. Decide in Phase 4 when writing the DSL
   parser, before users hit the limitation.

4. **`ai_says` and tool calls.** If the scripted response has tool calls
   (not inline commands), the harness needs to actually run the tools.
   The TaskRunner is already `SyncTaskRunner` so this works for free —
   but tests need a way to script the tool *result* too if a tool would
   normally call out to network/disk. Probably: `session.given_tool_stub("grep_open", returns={...})`.
   Decide in Phase 4.

## Order of operations summary

| Phase | What lands | Lines changed (rough) | Test surface added |
|-------|------------|-----------------------|--------------------|
| 1 | `TaskRunner` + `QtTaskRunner` + `SyncTaskRunner`; `LiveSession` and `SessionManager` switched to use it | ~200 | `test_task_runner.py` |
| 2 | `LLMBackend` + `OpenRouterBackend` + `ScriptedBackend`; `LiveSession` switched | ~150 | `test_llm_backend.py` |
| 3 | Workers rewritten as plain functions; `chat_workers.py` deleted | ~250 (mostly deletions) | `test_inline_executor.py`, `test_tool_executor.py` |
| 4 | Harness + DSL + port of two existing tests | ~250 (new test infra) | the harness itself |

Total: ~850 LOC changed across 4 phases, each independently reviewable
and revertible.

## Status

- [ ] Phase 1 — TaskRunner
- [ ] Phase 2 — LLMBackend
- [ ] Phase 3 — De-Qt workers
- [ ] Phase 4 — Harness + DSL + migrations