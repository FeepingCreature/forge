"""
Writable VFS that accumulates changes on top of a git commit
"""

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from forge.git_backend.commit_types import CommitType
from forge.vfs.base import VFS
from forge.vfs.git_commit import GitCommitVFS

if TYPE_CHECKING:
    from forge.git_backend.repository import ForgeRepository


class WorkInProgressVFS(VFS):
    """
    Writable VFS layer on top of a git commit.

    Accumulates changes in memory during an AI turn, then commits atomically.
    Each tool sees: base commit + all previous tool changes in this turn.

    Thread safety: This VFS uses thread ownership assertions. Call claim_thread()
    before accessing from a background thread, and release_thread() when done.
    """

    def __init__(self, repo: "ForgeRepository", branch_name: str) -> None:
        super().__init__()  # Initialize thread ownership
        self.repo = repo
        self.branch_name = branch_name

        # Get base commit
        commit = repo.get_branch_head(branch_name)
        self.base_vfs = GitCommitVFS(repo.repo, commit)

        # Pending changes: filepath -> new_content
        self.pending_changes: dict[str, str] = {}

        # Deleted files
        self.deleted_files: set[str] = set()

    def read_file(self, path: str) -> str:
        """Read file - checks pending changes first, then base commit"""
        self._assert_owner()
        if path in self.deleted_files:
            raise FileNotFoundError(f"File deleted: {path}")

        if path in self.pending_changes:
            return self.pending_changes[path]

        return self.base_vfs.read_file(path)

    def write_file(self, path: str, content: str) -> None:
        """Write file - accumulates in pending changes"""
        self._assert_owner()
        # Remove from deleted set if it was deleted
        self.deleted_files.discard(path)

        # Add to pending changes
        self.pending_changes[path] = content

    def list_files(self) -> list[str]:
        """List all files - base files + new files - deleted files"""
        self._assert_owner()
        files = set(self.base_vfs.list_files())

        # Add new files from pending changes
        files.update(self.pending_changes.keys())

        # Remove deleted files
        files -= self.deleted_files

        return sorted(files)

    def file_exists(self, path: str) -> bool:
        """Check if file exists - considers pending changes and deletions"""
        self._assert_owner()
        if path in self.deleted_files:
            return False

        if path in self.pending_changes:
            return True

        return self.base_vfs.file_exists(path)

    def delete_file(self, path: str) -> None:
        """Delete a file - marks for deletion"""
        self._assert_owner()
        if not self.file_exists(path):
            raise FileNotFoundError(f"File not found: {path}")

        # Remove from pending changes if it was added this turn
        self.pending_changes.pop(path, None)

        # Mark as deleted
        self.deleted_files.add(path)

    def get_pending_changes(self) -> dict[str, str]:
        """Get all pending changes"""
        self._assert_owner()
        return self.pending_changes.copy()

    def get_deleted_files(self) -> set[str]:
        """Get all deleted files"""
        self._assert_owner()
        return self.deleted_files.copy()

    def clear_pending_changes(self) -> None:
        """Clear all pending changes (after commit)"""
        self._assert_owner()
        self.pending_changes.clear()
        self.deleted_files.clear()

    def commit(
        self,
        message: str,
        author_name: str = "Forge AI",
        author_email: str = "ai@forge.dev",
        commit_type: CommitType = CommitType.MAJOR,
    ) -> str:
        """
        Create a git commit with all pending changes.

        If committing to the currently checked-out branch, also updates
        the working directory to match.

        Args:
            message: Commit message (without type prefix)
            author_name: Author name
            author_email: Author email
            commit_type: Type of commit for smart amending

        Returns:
            Commit OID as string
        """
        self._assert_owner()
        if not self.pending_changes and not self.deleted_files:
            raise ValueError("No changes to commit")

        # Check if we're committing to the checked-out branch BEFORE making changes
        # and whether the working directory is clean (so we can safely update it)
        checked_out = self.repo.get_checked_out_branch()
        is_checked_out_branch = checked_out == self.branch_name
        workdir_is_clean = self.repo.is_workdir_clean() if is_checked_out_branch else False

        # Build changes dict for create_tree_from_changes
        changes = self.pending_changes.copy()

        # Create tree with changes and deletions
        tree_oid = self.repo.create_tree_from_changes(self.branch_name, changes, self.deleted_files)

        # Create commit with type
        commit_oid = self.repo.commit_tree(
            tree_oid, message, self.branch_name, author_name, author_email, commit_type
        )

        # If we committed to the checked-out branch and workdir was clean, sync it
        if is_checked_out_branch and workdir_is_clean:
            self.repo.checkout_branch_head(self.branch_name)

        # Clear pending changes
        self.clear_pending_changes()

        return str(commit_oid)

    def materialize_to_tempdir(self) -> Path:
        """
        Create a temporary directory with the current VFS state.

        Useful for running tests or commands that need actual files.

        Returns:
            Path to temporary directory
        """
        self._assert_owner()
        tmpdir = Path(tempfile.mkdtemp(prefix="forge_vfs_"))

        # Write all files
        for filepath in self.list_files():
            full_path = tmpdir / filepath
            full_path.parent.mkdir(parents=True, exist_ok=True)

            content = self.read_file(filepath)
            full_path.write_text(content, encoding="utf-8")

        return tmpdir
