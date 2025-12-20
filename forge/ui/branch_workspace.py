"""
BranchWorkspace - manages per-branch state for the branch-first architecture
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.git_backend.repository import ForgeRepository
    from forge.vfs.work_in_progress import WorkInProgressVFS


class BranchWorkspace:
    """
    Manages state for a single branch tab.

    Each open branch in the UI has its own BranchWorkspace instance,
    containing its VFS, open files, and AI chat state.

    All file access goes through the VFS - the repo is only used
    for git operations (commits, branch management).
    """

    def __init__(self, branch_name: str, repo: "ForgeRepository") -> None:
        self.branch_name = branch_name
        self._repo = repo  # Private - only for git operations, not file reading

        # Open file tabs within this branch (paths)
        self.open_files: list[str] = []

        # Currently active file tab index (0 = AI chat, 1+ = files)
        self.active_tab_index: int = 0

        # AI chat state (messages loaded from .forge/session.json)
        self.ai_messages: list[dict[str, Any]] = []

        # VFS instance - created lazily, THE source of truth for file content
        self._vfs: WorkInProgressVFS | None = None

    @property
    def vfs(self) -> "WorkInProgressVFS":
        """Get or create the VFS for this branch - THE source of truth for file content"""
        if self._vfs is None:
            from forge.vfs.work_in_progress import WorkInProgressVFS

            self._vfs = WorkInProgressVFS(self._repo, self.branch_name)
        return self._vfs

    @property
    def has_session(self) -> bool:
        """Check if this branch has a session file"""
        return self.vfs.file_exists(".forge/session.json")

    @property
    def display_name(self) -> str:
        """Get a display-friendly name for the branch"""
        return self.branch_name

    def open_file(self, filepath: str) -> int:
        """
        Open a file in this workspace.

        Returns the tab index where the file is (or was already) open.
        """
        if filepath in self.open_files:
            return self.open_files.index(filepath) + 1  # +1 for AI chat tab

        self.open_files.append(filepath)
        return len(self.open_files)  # New tab index (AI chat is 0)

    def close_file(self, filepath: str) -> bool:
        """
        Close a file in this workspace.

        Returns True if the file was open and is now closed.
        """
        if filepath in self.open_files:
            self.open_files.remove(filepath)
            return True
        return False

    def has_unsaved_changes(self) -> bool:
        """Check if there are uncommitted changes in the VFS"""
        if self._vfs is None:
            return False
        return bool(self._vfs.pending_changes) or bool(self._vfs.deleted_files)

    def get_file_content(self, filepath: str) -> str:
        """Read file content through VFS"""
        return self.vfs.read_file(filepath)

    def set_file_content(self, filepath: str, content: str) -> None:
        """Write file content through VFS (accumulates in pending changes)"""
        self.vfs.write_file(filepath, content)

    def commit(self, message: str) -> str:
        """
        Commit all pending changes.

        Returns the commit OID.
        """
        return self.vfs.commit(message)

    def refresh_vfs(self) -> None:
        """
        Refresh the VFS to see latest branch state.

        Call this after external changes (e.g., AI made commits).
        """
        # Clear and recreate VFS to pick up new HEAD
        from forge.vfs.work_in_progress import WorkInProgressVFS

        self._vfs = WorkInProgressVFS(self._repo, self.branch_name)

    def load_session_data(self) -> dict[str, Any] | None:
        """Load session data from .forge/session.json in this branch"""
        import json

        try:
            content = self.vfs.read_file(".forge/session.json")
            result: dict[str, Any] = json.loads(content)
            return result
        except FileNotFoundError:
            return None

    def save_session_data(self, data: dict[str, Any]) -> None:
        """Save session data to .forge/session.json (accumulates in VFS)"""
        import json

        self.vfs.write_file(".forge/session.json", json.dumps(data, indent=2))
