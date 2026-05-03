"""
Session test harness for Forge.

A minimal-ceremony way to write flow tests for the AI session pipeline.
Tests look like a script:

    def test_failed_run_tests_preserves_edits(session):
        session.given_files({
            "a.py": "def foo():\\n    return 1\\n",
            "b.py": "def bar():\\n    return 10\\n",
        })
        session.given_files_in_context("a.py", "b.py")
        session.given_failing_tests()

        session.user_says("Fix foo->2 and bar->20, then run tests.")
        session.ai_says('''
            I'll fix both.
            @edit a.py
                old: return 1
                new: return 2
            @edit b.py
                old: return 10
                new: return 20
            @run_tests
        ''')
        session.run_turn()

        assert session.last_turn.failed_at == "run_tests"
        assert session.vfs["a.py"] == "def foo():\\n    return 2\\n"
        assert session.vfs["b.py"] == "def bar():\\n    return 20\\n"

The harness sits on top of three Phase-1/2/3 seams:

  - SyncTaskRunner so the pipeline executes straight-line.
  - ScriptedBackend so no network is touched.
  - The extracted runtime helpers (stream_to_events, run_inline_commands,
    execute_tool_calls), which are what make the whole pipeline observable
    deterministically.

API surface (see SessionTestHarness for full docs):

  Setup (chainable, return self):
    .given_files({path: content, ...})
    .given_files_in_context(*paths)
    .given_passing_tests() / .given_failing_tests()

  Script (queues, doesn't trigger):
    .user_says(text)
    .ai_says(dsl_or_raw)        - DSL gets compiled to inline XML
    .ai_says_raw(content)       - skip DSL compilation
    .ai_returns_tool_calls([...])  - rare; queue an API tool-call response

  Run:
    .run_turn()                 - drains the queued script

  Inspect:
    .vfs[path]                  - current VFS state
    .last_turn                  - TurnResult from the most recent .run_turn()
    .messages                   - LiveSession.messages
    .next_prompt_text()         - rendered user-side text from to_messages()
    .prompt_blocks              - PromptManager block list

Use the `session` fixture from `tests/conftest.py` to get a configured
SessionTestHarness.
"""

from tests.harness.dsl import compile_dsl
from tests.harness.session import SessionTestHarness, TurnResult

__all__ = ["SessionTestHarness", "TurnResult", "compile_dsl"]