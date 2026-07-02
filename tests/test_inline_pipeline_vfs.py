"""
Flow test: an inline-command pipeline whose final command (run_tests) fails
must NOT erase the successful edits that came before it. The VFS state
should still reflect the edits, and the AI's next prompt must show the
new content.

This used to be a low-level test driving parse_inline_commands +
execute_inline_commands_with_parse_check directly. Now it's a flow test
written against SessionTestHarness — same claim, much shorter.
"""

from __future__ import annotations


def test_failed_run_tests_does_not_erase_successful_edits(session):
    session.given_files(
        {
            "a.py": "def foo():\n    return 1\n",
            "b.py": "def bar():\n    return 10\n",
        }
    )
    session.given_files_in_context("a.py", "b.py")
    session.given_failing_tests()

    session.user_says("Fix foo to return 2 and bar to return 20, then run tests.")
    session.ai_says(
        """
        I'll fix both functions.

        @edit a.py
            old:
                def foo():
                    return 1
            new:
                def foo():
                    return 2

        @edit b.py
            old:
                def bar():
                    return 10
            new:
                def bar():
                    return 20

        @run_tests

        Both functions are updated; tests should now pass.
        """
    )
    result = session.run_turn()

    # The pipeline must have executed all three commands and stopped on
    # run_tests because tests failed.
    assert result.failed_at == "run_tests", (
        f"Expected pipeline to fail at run_tests; got failed_at={result.failed_at!r} "
        f"results={result.inline_results}"
    )

    # Both edits must still be visible through the VFS afterwards.
    assert session.vfs["a.py"] == "def foo():\n    return 2\n", (
        "a.py edit was lost from the VFS after run_tests failed"
    )
    assert session.vfs["b.py"] == "def bar():\n    return 20\n", (
        "b.py edit was lost from the VFS after run_tests failed"
    )


def _tool_call(call_id: str, name: str, arguments: dict) -> dict:
    """Build one API tool-call dict in the shape the executor expects."""
    import json

    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def test_edit_then_update_context_then_commit_sees_pending_edit(session):
    """Regression: edit -> update_context (changing selection) -> commit,
    all in ONE API tool-call batch (as observed in the wild - block 124 of
    the failing session: [edit, update_context, commit]).

    update_context runs inside the same claimed-VFS tool batch as the edit.
    It used to persist the active-file selection immediately by calling
    vfs.commit() + swapping in a fresh VFS - which swept up the preceding
    edit under a "save active files" PREPARE commit, cleared pending, and
    left the following commit tool looking at an empty VFS ("No pending
    changes to commit"). The AI-tool path now passes persist=False, so the
    edit stays pending for the explicit commit tool.
    """
    session.given_files(
        {
            "a.py": "def foo():\n    return 1\n",
            "b.py": "def bar():\n    return 10\n",
        }
    )
    session.given_files_in_context("a.py", "b.py")

    session.user_says("Fix foo, drop b.py from context, then commit.")
    session.ai_returns_tool_calls(
        [
            _tool_call(
                "call_edit",
                "edit",
                {
                    "edits": [
                        {
                            "filepath": "a.py",
                            "search": "def foo():\n    return 1\n",
                            "replace": "def foo():\n    return 2\n",
                        }
                    ]
                },
            ),
            _tool_call("call_ctx", "update_context", {"remove": ["b.py"]}),
            _tool_call("call_commit", "commit", {"message": "Fix foo"}),
        ]
    )
    session.run_turn()

    # The commit tool must have seen the pending edit and committed it, so the
    # fresh VFS reads the new content. Before the fix, update_context's
    # immediate persist swept the edit into a PREPARE commit and cleared
    # pending, so the commit tool aborted ("No pending changes") and the edit
    # never landed under the intended commit.
    assert session.vfs["a.py"] == "def foo():\n    return 2\n"

    # The edit landed under the explicit "Fix foo" commit, not swept into a
    # "save active files" PREPARE commit.
    head = session.repo.get_branch_head("master")
    assert "save active files" not in head.message, (
        f"edit was absorbed into the active-files PREPARE commit: {head.message!r}"
    )
    assert "Fix foo" in head.message, (
        f"edit was not committed under the explicit commit message: {head.message!r}"
    )