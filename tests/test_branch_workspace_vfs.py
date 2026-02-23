import pygit2
import pytest

from forge.git_backend.repository import ForgeRepository
from forge.ui.branch_workspace import BranchWorkspace


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Bootstrap a minimal real git repo with one commit on 'main'."""
    pygit2.init_repository(str(tmp_path))
    raw = pygit2.Repository(str(tmp_path))

    sig = pygit2.Signature("Test", "test@test.com")
    blob = raw.create_blob(b"hello")
    tb = raw.TreeBuilder()
    tb.insert("file.txt", blob, pygit2.GIT_FILEMODE_BLOB)
    tree = tb.write()
    raw.create_commit("refs/heads/main", sig, sig, "initial", tree, [])
    raw.set_head("refs/heads/main")

    # Patch out summary generation (background thread, not needed for this test)
    monkeypatch.setattr(
        "forge.session.manager.SessionManager.start_summary_generation",
        lambda self: None,
    )

    return ForgeRepository(str(tmp_path))


def test_vfs_sees_content_after_commit(repo):
    """
    After a commit via the VFS, workspace.vfs must serve the new content.

    This is the regression test for the dual-VFS bug where BranchWorkspace
    owned a separate _vfs instance from SessionManager.tool_manager.vfs.
    After a commit refreshed the session manager's VFS, the workspace still
    pointed at the old instance and served stale content.
    """
    from forge.config.settings import Settings

    settings = Settings.__new__(Settings)
    settings.config = {}

    workspace = BranchWorkspace("main", repo, settings)

    # Write a file and commit it through the VFS
    workspace.vfs.write_file("new.txt", "world")
    workspace.commit("add new.txt")

    # Simulate reopening: refresh the VFS (as done after an AI turn)
    workspace.refresh_vfs()

    # The refreshed VFS must see the committed content
    assert workspace.vfs.read_file("new.txt") == "world"


def test_workspace_vfs_is_session_manager_vfs(repo):
    """
    BranchWorkspace.vfs must be the exact same object as
    session_manager.tool_manager.vfs — not a separate instance.

    If they ever diverge, commits made through one won't be visible
    through the other.
    """
    from forge.config.settings import Settings

    settings = Settings.__new__(Settings)
    settings.config = {}

    workspace = BranchWorkspace("main", repo, settings)

    assert workspace.vfs is workspace.session_manager.tool_manager.vfs