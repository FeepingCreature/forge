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
    raw.create_commit("refs/heads/master", sig, sig, "initial", tree, [])
    raw.set_head("refs/heads/master")

    # Patch out summary generation (background thread, not needed for this test)
    monkeypatch.setattr(
        "forge.session.manager.SessionManager.start_summary_generation",
        lambda self: None,
    )

    return ForgeRepository(str(tmp_path))


def test_vfs_sees_content_after_commit(repo):
    """
    After a commit via the VFS, the same VFS instance must immediately
    serve the new content — without any refresh.

    Regression test: WorkInProgressVFS.commit() used to clear pending_changes
    but leave base_vfs pointing at the old commit, so the committed content
    vanished from the VFS's perspective the moment pending_changes was cleared.
    """
    from forge.config.settings import Settings

    settings = Settings.__new__(Settings)
    settings.config = {}

    workspace = BranchWorkspace("master", repo, settings)

    # Write a file and commit it through the VFS
    workspace.vfs.write_file("new.txt", "world")
    workspace.commit("add new.txt")

    # No refresh — the VFS must see the content immediately after commit
    assert workspace.vfs.read_file("new.txt") == "world"


def test_vfs_sees_content_after_refresh(repo):
    """
    refresh_vfs() must also produce a VFS that sees previously committed content.
    """
    from forge.config.settings import Settings

    settings = Settings.__new__(Settings)
    settings.config = {}

    workspace = BranchWorkspace("master", repo, settings)

    workspace.vfs.write_file("new.txt", "world")
    workspace.commit("add new.txt")
    workspace.refresh_vfs()

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

    workspace = BranchWorkspace("master", repo, settings)

    assert workspace.vfs is workspace.session_manager.tool_manager.vfs