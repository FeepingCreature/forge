"""
Cache for remembering open files per repository.

Stores open file tabs in XDG cache so they can be restored on restart.
"""

import json
import os
from pathlib import Path


def _get_cache_file() -> Path:
    """Get the path to the open files cache file"""
    xdg_cache = os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    cache_dir = Path(xdg_cache) / "forge"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "open_files.json"


def _get_repo_key(repo_path: str) -> str:
    """Get a stable key for a repository path"""
    # Use absolute path as key
    return str(Path(repo_path).resolve())


def get_open_files(repo_path: str, branch_name: str) -> list[str]:
    """
    Get the list of open files for a repo+branch.

    Args:
        repo_path: Path to the repository
        branch_name: Name of the branch

    Returns:
        List of file paths that were open
    """
    cache_file = _get_cache_file()
    if not cache_file.exists():
        return []

    try:
        data = json.loads(cache_file.read_text())
        repo_key = _get_repo_key(repo_path)
        result = data.get(repo_key, {}).get(branch_name, [])
        if isinstance(result, list):
            return [str(f) for f in result]
        return []
    except (json.JSONDecodeError, KeyError):
        return []


def save_open_files(repo_path: str, branch_name: str, files: list[str]) -> None:
    """
    Save the list of open files for a repo+branch.

    Args:
        repo_path: Path to the repository
        branch_name: Name of the branch
        files: List of file paths that are open
    """
    cache_file = _get_cache_file()

    # Load existing data
    data: dict[str, dict[str, list[str]]] = {}
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text())
        except json.JSONDecodeError:
            data = {}

    # Update with new files
    repo_key = _get_repo_key(repo_path)
    if repo_key not in data:
        data[repo_key] = {}
    data[repo_key][branch_name] = files

    # Save
    cache_file.write_text(json.dumps(data, indent=2))


def clear_open_files(repo_path: str, branch_name: str) -> None:
    """
    Clear the open files cache for a repo+branch.

    Args:
        repo_path: Path to the repository
        branch_name: Name of the branch
    """
    save_open_files(repo_path, branch_name, [])
