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