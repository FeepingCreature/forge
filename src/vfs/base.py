"""
Abstract VFS interface for git-backed file operations
"""

import threading
from abc import ABC, abstractmethod


class VFS(ABC):
    """Abstract virtual filesystem interface with thread ownership.

    VFS instances are NOT thread-safe and must only be accessed from one thread
    at a time. Use claim_thread() before accessing from a new thread, and
    release_thread() when done. All operations assert the current thread owns
    the VFS.
    """

    def __init__(self) -> None:
        # Thread that currently owns this VFS (None = unclaimed)
        self._owner_thread_id: int | None = None

    def claim_thread(self) -> None:
        """Claim this VFS for the current thread.

        Must be called before accessing VFS from a background thread.
        Asserts that the VFS is not already claimed by another thread.
        """
        current = threading.get_ident()
        if self._owner_thread_id is not None and self._owner_thread_id != current:
            raise AssertionError(
                f"VFS already owned by thread {self._owner_thread_id}, "
                f"cannot claim from thread {current}"
            )
        self._owner_thread_id = current

    def release_thread(self) -> None:
        """Release thread ownership of this VFS.

        Must be called when done accessing VFS from a background thread.
        Asserts that the current thread is the owner.
        """
        current = threading.get_ident()
        if self._owner_thread_id != current:
            raise AssertionError(
                f"VFS owned by thread {self._owner_thread_id}, cannot release from thread {current}"
            )
        self._owner_thread_id = None

    def _assert_owner(self) -> None:
        """Assert that the current thread owns this VFS."""
        current = threading.get_ident()
        if self._owner_thread_id is not None and self._owner_thread_id != current:
            raise AssertionError(
                f"VFS owned by thread {self._owner_thread_id}, accessed from thread {current}"
            )

    @abstractmethod
    def read_file(self, path: str) -> str:
        """Read file content"""
        pass

    @abstractmethod
    def write_file(self, path: str, content: str) -> None:
        """Write file content"""
        pass

    @abstractmethod
    def list_files(self) -> list[str]:
        """List all files in the VFS"""
        pass

    @abstractmethod
    def file_exists(self, path: str) -> bool:
        """Check if file exists"""
        pass

    @abstractmethod
    def delete_file(self, path: str) -> None:
        """Delete a file"""
        pass
