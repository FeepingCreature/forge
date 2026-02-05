"""
Shared utilities for grep tools.
"""

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.vfs.base import VFS

# Default directories to exclude from grep
DEFAULT_EXCLUDE_DIRS = [".git", ".forge", "__pycache__", "node_modules", ".venv", "venv"]


def compile_pattern(pattern: str) -> re.Pattern[str] | dict[str, Any]:
    """
    Compile a regex pattern.

    Returns the compiled pattern, or an error dict if invalid.
    """
    try:
        return re.compile(pattern)
    except re.error as e:
        return {"success": False, "error": f"Invalid regex pattern: {e}"}


def should_exclude_file(
    filepath: str,
    exclude_dirs: list[str],
    include_extensions: list[str],
) -> bool:
    """
    Check if a file should be excluded from grep.

    Args:
        filepath: Path to check
        exclude_dirs: Directory names to exclude
        include_extensions: Only include files with these extensions (empty = all)

    Returns:
        True if file should be excluded, False if it should be included
    """
    # Check exclusions
    for exclude_dir in exclude_dirs:
        if f"/{exclude_dir}/" in f"/{filepath}" or filepath.startswith(f"{exclude_dir}/"):
            return True

    # Check extensions
    if include_extensions:
        return all(not filepath.endswith(ext) for ext in include_extensions)

    return False


def get_files_to_search(
    vfs: "VFS",
    exclude_dirs: list[str],
    include_extensions: list[str],
) -> list[str]:
    """
    Get list of files to search, applying exclusions and extension filters.

    Args:
        vfs: Virtual filesystem to list files from
        exclude_dirs: Directory names to exclude
        include_extensions: Only include files with these extensions (empty = all)

    Returns:
        List of filepaths to search
    """
    all_files = vfs.list_files()
    return [f for f in all_files if not should_exclude_file(f, exclude_dirs, include_extensions)]
