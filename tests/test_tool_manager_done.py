"""Regression tests for `done` tool exposure gating.

The `done` tool exists only to declare SideEffect.END_TURN in strict mode
(llm.require_done_tag). When that mode is off, `done` is a no-op that actively
prevents a turn from ending -- it's a tool call, so the orchestrator re-drives
the model to act on its result, trapping it in a loop. So `done` must only be
exposed as an API tool when require_done_tag is True.

NOTE: `done` is an inline tool, so it is only exposed as an API tool in
API-only mode (inline_enabled=False). With inline_enabled=True it's reachable
via `<done/>` prose parsing instead, and the inline filter strips it from the
API schema list regardless of require_done_tag. These tests therefore run in
API-only mode, which is the configuration where the footgun actually bites.
"""

import pygit2
import pytest

from forge.git_backend.repository import ForgeRepository
from forge.tools.manager import ToolManager


@pytest.fixture
def repo(tmp_path):
    """Bootstrap a minimal real git repo with one commit on 'master'."""
    pygit2.init_repository(str(tmp_path))
    raw = pygit2.Repository(str(tmp_path))

    sig = pygit2.Signature("Test", "test@test.com")
    blob = raw.create_blob(b"hello")
    tb = raw.TreeBuilder()
    tb.insert("file.txt", blob, pygit2.GIT_FILEMODE_BLOB)
    tree = tb.write()
    raw.create_commit("refs/heads/master", sig, sig, "initial", tree, [])
    raw.set_head("refs/heads/master")

    return ForgeRepository(str(tmp_path))


def _tool_names(schemas):
    return {s.get("function", {}).get("name") for s in schemas}


def test_done_filtered_when_require_done_tag_off(repo):
    """`done` must NOT be exposed as an API tool when require_done_tag is off."""
    tm = ToolManager(repo, "master", inline_enabled=False, require_done_tag=False)
    assert "done" not in _tool_names(tm.discover_tools())


def test_done_exposed_when_require_done_tag_on(repo):
    """`done` must be exposed as an API tool when require_done_tag is on."""
    tm = ToolManager(repo, "master", inline_enabled=False, require_done_tag=True)
    assert "done" in _tool_names(tm.discover_tools())


def test_require_done_tag_defaults_off(repo):
    """Default construction (no require_done_tag) filters `done`."""
    tm = ToolManager(repo, "master", inline_enabled=False)
    assert "done" not in _tool_names(tm.discover_tools())


def test_filtering_is_idempotent_across_cached_discovery(repo):
    """The filter applies on the cached path too (second discover_tools call)."""
    tm = ToolManager(repo, "master", inline_enabled=False, require_done_tag=False)
    tm.discover_tools()  # populate schema cache
    assert "done" not in _tool_names(tm.discover_tools())
