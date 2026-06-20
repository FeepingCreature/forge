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


def _schema_for(schemas, name):
    for s in schemas:
        if s.get("function", {}).get("name") == name:
            return s
    return None


def test_inline_markers_stripped_when_inline_disabled(repo):
    """In API-only mode, inline tools are exposed but without inline markers.

    `edit` is an inline tool whose schema carries invocation="inline" and an
    inline_syntax pointing at <replace>/<write>. When inline parsing is off the
    model can only call it as an API function, so those markers must be stripped
    -- otherwise the schema advertises XML syntax the model can't actually use.
    """
    tm = ToolManager(repo, "master", inline_enabled=False)
    edit_schema = _schema_for(tm.discover_tools(), "edit")
    assert edit_schema is not None, "edit should be exposed as an API tool in API-only mode"
    assert "invocation" not in edit_schema
    assert "inline_syntax" not in edit_schema
    # The actual function schema must be left intact.
    assert edit_schema["function"]["name"] == "edit"
    assert "parameters" in edit_schema["function"]


def test_inline_markers_present_when_inline_enabled(repo):
    """With inline parsing on, inline tools are driven by prose parsing and are
    filtered out of the API tool list entirely (markers untouched on the cache).
    """
    tm = ToolManager(repo, "master", inline_enabled=True)
    assert _schema_for(tm.discover_tools(), "edit") is None
