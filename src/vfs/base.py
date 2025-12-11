"""
Abstract VFS interface for git-backed file operations
"""

from abc import ABC, abstractmethod


class VFS(ABC):
    """Abstract virtual filesystem interface"""

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
