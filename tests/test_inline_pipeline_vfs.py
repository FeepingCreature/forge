"""
Tests for the inline-command pipeline's interaction with the VFS.

These tests run the full parse_inline_commands → execute_inline_commands
pipeline against a real WorkInProgressVFS over a tiny git repo, and assert
that pending VFS state is correct after the pipeline finishes — including
when a later command in the pipeline fails.
"""

import pygit2
import pytest

from forge.git_backend.repository import ForgeRepository
from forge.tools.invocation import (
    execute_inline_commands_with_parse_check,
    parse_inline_commands,
)
from forge.vfs.work_in_progress import WorkInProgressVFS


@pytest.fixture
def repo_with_files(tmp_path):
    """
    Bootstrap a git repo with two source files plus a Makefile whose
    `test` target always fails *without* touching any files.

    The failing-but-non-mutating Makefile lets us exercise the
    <run_tests/> inline command in a hermetic, deterministic way:
    run_tests will discover `make test`, run it, see returncode != 0,
    and report failure — but no files in the materialized tempdir
    will have been modified, so the writeback loop is a no-op.
    """
    pygit2.init_repository(str(tmp_path))
    raw = pygit2.Repository(str(tmp_path))

    sig = pygit2.Signature("Test", "test@test.com")

    # Two source files we'll edit, plus a Makefile that fails tests.
    files = {
        "a.py": b"def foo():\n    return 1\n",
        "b.py": b"def bar():\n    return 10\n",
        # Use printf so this works without bash; `false` exits non-zero.
        "Makefile": b"test:\n\t@printf 'simulated test failure\\n'; false\n",
    }

    tb = raw.TreeBuilder()
    for name, data in files.items():
        blob = raw.create_blob(data)
        tb.insert(name, blob, pygit2.GIT_FILEMODE_BLOB)
    tree = tb.write()
    raw.create_commit("refs/heads/master", sig, sig, "initial", tree, [])
    raw.set_head("refs/heads/master")

    return ForgeRepository(str(tmp_path))


def test_edits_persist_in_vfs_when_run_tests_fails(repo_with_files):
    """
    Pipeline: <replace a.py> → <replace b.py> → narration → <run_tests/>.

    The two replaces succeed; run_tests runs against the materialized VFS
    state (which includes both edits) and reports failure because the
    Makefile's `test` target exits non-zero. The Makefile does NOT modify
    any files, so the writeback loop in run_tests is a no-op.

    Expected behavior: after the pipeline, the VFS still contains both
    edits in pending_changes, and a.py / b.py read back with the new
    content. The fact that run_tests failed must not erase the edits.
    """
    vfs = WorkInProgressVFS(repo_with_files, "master")

    content = """\
I'll fix both functions and then verify with the test suite.

<replace file="a.py">
<old>
def foo():
    return 1
</old>
<new>
def foo():
    return 2
</new>
</replace>

<replace file="b.py">
<old>
def bar():
    return 10
</old>
<new>
def bar():
    return 20
</new>
</replace>

Now let's verify the changes pass the tests.

<run_tests/>
"""

    commands = parse_inline_commands(content)

    # Sanity: parsed three commands in source order.
    assert len(commands) == 3, (
        f"Expected 3 inline commands (replace, replace, run_tests); "
        f"got {len(commands)}: {[c.tool_name for c in commands]}"
    )
    assert commands[0].tool_name == "edit"
    assert commands[0].args["filepath"] == "a.py"
    assert commands[1].tool_name == "edit"
    assert commands[1].args["filepath"] == "b.py"
    assert commands[2].tool_name == "run_tests"

    results, failed_index = execute_inline_commands_with_parse_check(
        vfs, content, commands
    )

    # The pipeline must have executed all three commands and stopped on
    # run_tests (index 2) because tests failed.
    assert failed_index == 2, (
        f"Expected pipeline to fail at run_tests (index 2); "
        f"got failed_index={failed_index}, results={results}"
    )
    assert len(results) == 3
    assert results[0]["success"] is True, f"first replace failed: {results[0]}"
    assert results[1]["success"] is True, f"second replace failed: {results[1]}"
    assert results[2]["success"] is False, "run_tests should have failed"

    # ── The actual claim under test ────────────────────────────────────
    # Both edits must still be visible through the VFS afterwards.
    assert vfs.read_file("a.py") == "def foo():\n    return 2\n", (
        "a.py edit was lost from the VFS after run_tests failed"
    )
    assert vfs.read_file("b.py") == "def bar():\n    return 20\n", (
        "b.py edit was lost from the VFS after run_tests failed"
    )

    # And they should be present specifically in pending_changes (not
    # somehow committed or hiding in base_vfs).
    assert "a.py" in vfs.pending_changes
    assert "b.py" in vfs.pending_changes