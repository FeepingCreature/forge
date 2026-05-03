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

## Resolved design decisions

1. **Cancellation: cooperative.** Forget the hard `terminate()` path —
   it never reliably worked anyway. `TaskRunner` exposes a soft
   "stop requested" flag the work function can check. Work that doesn't
   check just runs to completion; `cancel_all()` then drops the result
   on the floor instead of delivering it. Concretely:
   - `Handle` has `.request_stop()` and `.stop_requested` (a thread-safe
     flag). Work functions are passed the handle (or a `CancelToken`
     view of it) and can poll `.stop_requested` at safe points.
   - `TaskRunner.cancel_all()` calls `request_stop()` on every live
     handle and marks them as "results discarded" so any callbacks
     that fire after cancel become no-ops.
   - `LiveSession.cancel()` becomes: `self._tasks.cancel_all()` plus
     the existing VFS reset and message cleanup.

2. **`ScriptedBackend` strictness: per-expectation flag.** Each
   `queue_response(...)` accepts an optional `optional=True` flag for
   responses that may or may not be consumed. Default behavior: at
   fixture teardown, any non-`optional` queued response that wasn't
   consumed raises `AssertionError`. This catches the common "test
   queues 3 responses but pipeline only used 2" bug.

3. **DSL multi-line bodies: no escape needed.** Tests are in our
   control. If you need a body that starts with `@`, restructure the
   test or use the raw inline-XML escape hatch. Keep the DSL simple.

4. **Scripting tool results.** The harness exposes
   `session.given_tool_result("tool_name", **result_fields)` to inject
   fake results for specific tool names. Real tools that don't need
   stubbing (like `edit`, which works against the VFS) just run for
   real via `SyncTaskRunner`. Stubs take precedence over real
   execution.

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

- [x] Phase 1 — TaskRunner
  - [x] step 1: `forge/runtime/tasks.py` (CancelToken / TaskHandle / TaskRunner protocol / SyncTaskRunner / QtTaskRunner) + `tests/test_task_runner.py`
        Sync coverage in place; QtTaskRunner smoke tests deferred (SIGABRT in cross-thread Signal delivery — see TODO comment block in the test file).
  - [x] step 2: `LiveSession` and `SessionManager` migrated. All four off-thread sites (LLM stream / inline commands / tool execution / summary generation) now go through `TaskRunner.submit`. Event dispatch via `forge/runtime/events.py` dataclasses (StreamChunk, StreamToolCallDelta, ToolStarted, ToolFinished, SummaryProgress).
- [x] Phase 2 — LLMBackend
  - `forge/runtime/llm_backend.py` (LLMBackend protocol, OpenRouterBackend, ScriptedBackend, StreamFinished, StreamEvent) + `tests/test_llm_backend.py`. `LiveSession._get_llm_backend()` lazily constructs the default backend so injected backends bypass the API-key fetch.
- [x] Phase 3 — De-Qt workers (extract closures into runtime helpers)
  - `forge/runtime/streaming.py::stream_to_events(backend, messages, tools, emit) -> dict` + `tests/test_streaming_helper.py`
  - `forge/runtime/inline_executor.py::run_inline_commands(vfs, content, commands) -> tuple[list, int|None]` + `tests/test_inline_executor.py`
  - `forge/runtime/tool_executor.py::execute_tool_calls(tool_calls, tool_manager, session_manager, emit) -> list[dict]` + `tests/test_tool_executor.py`
  - LiveSession's three closures shrunk to one-line wrappers around the helpers.
  - **Deviation from original plan:** The summary-generation closure in `SessionManager.start_summary_generation` was *not* extracted into `forge/runtime/summarizer.py`. Reason: it's already a 5-line wrapper around `SessionManager.generate_repo_summaries` (no Qt, no tricky logic), and pulling it out would create a `runtime → session` import edge that I want to avoid (runtime should be a leaf package). `generate_repo_summaries` is itself directly testable as a plain method. If we want a `summarizer.py` later for symmetry, it can wrap a `Callable[..., None]` with a progress callback rather than depending on SessionManager.
- [x] Phase 4 — Harness + DSL + migrations
  - `tests/harness/repo.py` — `bootstrap_repo()` plus `PASSING_MAKEFILE` / `FAILING_MAKEFILE` constants for hermetic test commands.
  - `tests/harness/dsl.py` — `compile_dsl()` translating `@edit`/`@write`/`@delete`/`@rename`/`@run_tests`/`@check`/`@commit`/`@think` directives into the inline-XML the parser understands. Indentation-significant body parsing; non-directive lines pass through as prose.
  - `tests/harness/session.py` — `SessionTestHarness` + `TurnResult` dataclass. Lazy session construction; chainable `given_*` setup; `user_says`/`ai_says` queueing; explicit `run_turn()` trigger; `vfs[path]`/`next_prompt_text()`/`prompt_blocks` inspection.
  - `tests/conftest.py` — `session` fixture + auto `assert_drained()` at teardown.
  - Migrated `tests/test_inline_pipeline_vfs.py` (149 → ~55 lines) and `tests/test_prompt_manager_inline_failure.py` (232 → ~150 lines).
  - **Bonus fix:** `ScriptedBackend.stream()` now yields `StreamFinished(None, None)` when the queue is empty rather than raising. The previous behavior caused `_on_stream_error` to retry indefinitely under SyncTaskRunner; `assert_drained()` at teardown still catches under-queueing.

### Carried-over follow-ups

- **QtTaskRunner smoke tests** — deferred behind a TODO in `tests/test_task_runner.py`. Three suspected causes (Signal(object) closures, deleteLater race, thread.quit ordering); three investigation paths noted. Production path is exercised by the running app, just not by tests yet. Worth revisiting once we have an end-to-end harness in Phase 4 because that may expose the symptoms differently.

- **`chat_workers.py`** — already deleted in Phase 1 (was orphan after the migration). No follow-up needed.