"""
BranchWorkspace - manages per-branch state for the branch-first architecture
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..git_backend.repository import ForgeRepository
    from ..vfs.work_in_progress import WorkInProgressVFS


@dataclass
class BranchWorkspace:
    """
    Manages state for a single branch tab.
    
    Each open branch in the UI has its own BranchWorkspace instance,
    containing its VFS, open files, and AI chat state.
    """
    
    branch_name: str
    repo: "ForgeRepository"
    
    # Open file tabs within this branch (paths)
    open_files: list[str] = field(default_factory=list)
    
    # Currently active file tab index (0 = AI chat, 1+ = files)
    active_tab_index: int = 0
    
    # AI chat state (messages, session_id if applicable)
    ai_messages: list[dict[str, Any]] = field(default_factory=list)
    session_id: str | None = None
    
    # VFS instance - created lazily
    _vfs: "WorkInProgressVFS | None" = field(default=None, repr=False)
    
    @property
    def vfs(self) -> "WorkInProgressVFS":
        """Get or create the VFS for this branch"""
        if self._vfs is None:
            from ..vfs.work_in_progress import WorkInProgressVFS
            self._vfs = WorkInProgressVFS(self.repo, self.branch_name)
        return self._vfs
    
    @property
    def is_session_branch(self) -> bool:
        """Check if this is an AI session branch"""
        return self.branch_name.startswith("forge/session/")
    
    @property
    def display_name(self) -> str:
        """Get a display-friendly name for the branch"""
        if self.is_session_branch:
            # Extract session ID and show abbreviated version
            session_id = self.branch_name.replace("forge/session/", "")
            return f"🤖 {session_id[:8]}"
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
        from ..vfs.work_in_progress import WorkInProgressVFS
        self._vfs = WorkInProgressVFS(self.repo, self.branch_name)
