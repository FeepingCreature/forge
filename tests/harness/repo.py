"""
Git repository bootstrap helpers for the test harness.

Extracted from the duplicated fixtures in tests/test_branch_workspace_vfs.py
and tests/test_inline_pipeline_vfs.py. Both did the same dance: init a
pygit2 repo, write a tree of seed files, create one commit on master.
"""

from pathlib import Path

import pygit2

from forge.git_backend.repository import ForgeRepository

_DEFAULT_SIG = pygit2.Signature("Test", "test@test.com")


def bootstrap_repo(
    tmp_path: Path,
    files: dict[str, str | bytes] | None = None,
    branch: str = "master",
) -> ForgeRepository:
    """Initialize a git repo at tmp_path with one commit holding `files`.

    Args:
        tmp_path: directory to init the repo in (typically the pytest tmp_path).
        files: path -> content mapping for the seed commit. Empty/None creates
            a repo with a single placeholder file (`.gitkeep`) so the initial
            commit isn't empty (some tooling chokes on empty trees).
        branch: branch name to create the commit on. Defaults to "master" to
            match the existing test suite's convention.

    Returns:
        A ForgeRepository pointing at the new repo, with HEAD on `branch`.
    """
    pygit2.init_repository(str(tmp_path))
    raw = pygit2.Repository(str(tmp_path))

    if not files:
        files = {".gitkeep": ""}

    tb = raw.TreeBuilder()
    for name, data in files.items():
        if isinstance(data, str):
            data = data.encode()
        blob = raw.create_blob(data)
        tb.insert(name, blob, pygit2.GIT_FILEMODE_BLOB)
    tree = tb.write()
    raw.create_commit(
        f"refs/heads/{branch}", _DEFAULT_SIG, _DEFAULT_SIG, "initial", tree, []
    )
    raw.set_head(f"refs/heads/{branch}")

    return ForgeRepository(str(tmp_path))


# Hermetic Makefile bodies for given_passing_tests / given_failing_tests.
# Both use printf + true/false so they don't depend on bash being available
# and don't touch any files (writeback after the test command is therefore
# always a no-op, which is what we want for deterministic flow tests).
PASSING_MAKEFILE = "test:\n\t@printf 'simulated test pass\\n'; true\n"
FAILING_MAKEFILE = "test:\n\t@printf 'simulated test failure\\n'; false\n"