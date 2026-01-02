"""Virtual filesystem abstraction for git-backed operations"""

from forge.vfs.binary import BINARY_EXTENSIONS, is_binary_file

__all__ = ["BINARY_EXTENSIONS", "is_binary_file"]
