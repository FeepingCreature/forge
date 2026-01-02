"""
Read-only VFS backed by a git commit
"""

import pygit2

from .base import VFS


class GitCommitVFS(VFS):
    """Read-only view of a git commit"""

    def __init__(self, repo: pygit2.Repository, commit: pygit2.Commit) -> None:
        self.repo = repo
        self.commit = commit
        self.tree = commit.tree

    def read_file_bytes(self, path: str) -> bytes:
        """Read file content as raw bytes from git tree"""
        try:
            entry = self.tree[path]
            blob = self.repo[entry.id]
            assert isinstance(blob, pygit2.Blob), f"Expected Blob, got {type(blob)}"
            return blob.data
        except KeyError as err:
            raise FileNotFoundError(f"File not found: {path}") from err

    def read_file(self, path: str) -> str:
        """Read file content as text (UTF-8 decoded)"""
        data = self.read_file_bytes(path)
        return data.decode("utf-8")

    def write_file(self, path: str, content: str) -> None:
        """Write operations not supported on read-only VFS"""
        raise NotImplementedError("GitCommitVFS is read-only")

    def list_all_files(self) -> list[str]:
        """List all files in the commit (including binary)"""
        files: list[str] = []

        def walk_tree(tree: pygit2.Tree, prefix: str = "") -> None:
            for entry in tree:
                assert entry.name is not None, "Tree entry name should never be None"
                entry_path = f"{prefix}/{entry.name}" if prefix else entry.name

                # Skip submodules - their filemode is GIT_FILEMODE_COMMIT (0o160000)
                # and their OIDs point to commits in other repositories
                if entry.filemode == pygit2.GIT_FILEMODE_COMMIT:
                    continue

                obj = self.repo[entry.id]
                assert isinstance(obj, (pygit2.Tree, pygit2.Blob)), (
                    f"Unexpected git object type: {type(obj)}"
                )
                if isinstance(obj, pygit2.Tree):
                    walk_tree(obj, entry_path)
                else:
                    files.append(entry_path)

        walk_tree(self.tree)
        return files

    def file_exists(self, path: str) -> bool:
        """Check if file exists in commit"""
        try:
            self.tree[path]
            return True
        except KeyError:
            return False

    def delete_file(self, path: str) -> None:
        """Delete operations not supported on read-only VFS"""
        raise NotImplementedError("GitCommitVFS is read-only")
