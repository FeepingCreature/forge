"""
Per-repository summary exclusion patterns: config key, defaults, and the
gitignore-style pattern matcher.

The editing UI for these patterns now lives in RepositorySettingsDialog
(forge/ui/repository_settings_dialog.py). This module is helpers-only and is
imported by that dialog and by the summary generator.
"""

import fnmatch
import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forge.vfs.base import VFS

CONFIG_FILE = ".forge/config.json"

# Default exclusion patterns for new repositories
# Note: Files not tracked in git (via .gitignore) won't appear anyway.
# This list is for committed files you don't want summarized.
DEFAULT_EXCLUSIONS = [
    # Vendored/dependency directories (if committed)
    "vendor/",
    "third_party/",
    # Lock files (often committed)
    "*.lock",
    "package-lock.json",
    "yarn.lock",
    "poetry.lock",
    "pnpm-lock.yaml",
    # Minified files (if committed)
    "*.min.js",
    "*.min.css",
    # Test snapshots
    "__snapshots__/",
    "*.snap",
]


def matches_pattern(filepath: str, pattern: str) -> bool:
    """Check if a filepath matches a gitignore-style exclusion pattern.

    Gitignore pattern rules:
    - /folder/ → anchored to root, matches folder/ at top level only
    - folder/ → matches folder/ anywhere in the tree
    - /file.txt → anchored to root, matches file.txt at top level only
    - *.ext → matches *.ext anywhere (no slash = basename match)
    - folder/*.ext → matches that specific path pattern
    - **/ → matches zero or more directories
    - !pattern → negation (handled separately, not here)

    Returns True if the filepath matches the pattern.
    """
    if not pattern:
        return False

    # Handle negation marker (caller should handle this)
    if pattern.startswith("!"):
        return False

    # Check if pattern is anchored to root
    anchored = pattern.startswith("/")
    if anchored:
        pattern = pattern[1:]  # Remove leading slash

    # Check if pattern is for directories
    is_dir_pattern = pattern.endswith("/")
    if is_dir_pattern:
        pattern = pattern[:-1]  # Remove trailing slash for matching

    # If pattern contains no slash (after removing leading/trailing),
    # it matches basename anywhere (unless anchored)
    if "/" not in pattern and not anchored:
        # Match against any path component or the basename
        return _match_basename_anywhere(filepath, pattern, is_dir_pattern)

    # Pattern with slash - match against full path
    return _match_path(filepath, pattern, anchored, is_dir_pattern)


def _match_basename_anywhere(filepath: str, pattern: str, is_dir_pattern: bool) -> bool:
    """Match a pattern against basename anywhere in the path."""
    parts = filepath.split("/")

    if is_dir_pattern:
        # For directory patterns, check if any directory component matches
        # e.g., "node_modules/" should match "foo/node_modules/bar.js"
        # Check all directory components (excluding the filename)
        return any(fnmatch.fnmatch(parts[i], pattern) for i in range(len(parts) - 1))
    else:
        # For file patterns, match against basename
        basename = parts[-1]
        if fnmatch.fnmatch(basename, pattern):
            return True
        # Also try matching against each path component (for patterns like __pycache__)
        return any(fnmatch.fnmatch(part, pattern) for part in parts)


def _match_path(filepath: str, pattern: str, anchored: bool, is_dir_pattern: bool) -> bool:
    """Match a pattern against the full path."""
    # Convert gitignore glob to fnmatch pattern
    # ** matches any number of directories
    fnmatch_pattern = pattern.replace("**/", "[-STARSTAR-]")
    fnmatch_pattern = fnmatch_pattern.replace("**", "[-STARSTAR-]")

    if anchored:
        # Anchored patterns match from the start
        if is_dir_pattern:
            # Directory pattern: file must be under this directory
            if "[-STARSTAR-]" in fnmatch_pattern:
                # Has **, use regex
                regex = _glob_to_regex(pattern)
                return bool(re.match(regex, filepath))
            else:
                return filepath.startswith(pattern + "/") or filepath == pattern
        else:
            # File pattern
            if "[-STARSTAR-]" in fnmatch_pattern:
                regex = _glob_to_regex(pattern)
                return bool(re.match(regex, filepath))
            else:
                return fnmatch.fnmatch(filepath, pattern)
    else:
        # Non-anchored patterns can match anywhere
        if is_dir_pattern:
            # Match if this directory appears anywhere in path
            if filepath.startswith(pattern + "/"):
                return True
            return ("/" + pattern + "/") in ("/" + filepath)
        else:
            # Try matching at each position
            if fnmatch.fnmatch(filepath, pattern):
                return True
            if fnmatch.fnmatch(filepath, "**/" + pattern):
                return True
            # Try with ** expansion
            if "[-STARSTAR-]" in fnmatch_pattern:
                regex = _glob_to_regex("**/" + pattern)
                return bool(re.match(regex, filepath))
            return ("/" + pattern) in ("/" + filepath) or filepath.endswith("/" + pattern)


def _glob_to_regex(pattern: str) -> re.Pattern:
    """Convert a gitignore glob pattern to a regex."""
    # Escape regex special chars except * and ?
    result = ""
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                # ** matches anything including /
                if i + 2 < len(pattern) and pattern[i + 2] == "/":
                    result += "(?:.*/)?"
                    i += 3
                    continue
                else:
                    result += ".*"
                    i += 2
                    continue
            else:
                # * matches anything except /
                result += "[^/]*"
        elif c == "?":
            result += "[^/]"
        elif c in ".^$+{}[]|()":
            result += "\\" + c
        else:
            result += c
        i += 1
    return re.compile("^" + result + "$")


def load_summary_exclusions(vfs: "VFS", create_default: bool = True) -> list[str]:
    """
    Load summary exclusion patterns from repo config.

    Args:
        vfs: The VFS to read from
        create_default: If True and config doesn't exist, create with defaults

    Returns:
        List of exclusion patterns
    """
    try:
        if vfs.file_exists(CONFIG_FILE):
            content = vfs.read_file(CONFIG_FILE)
            config = json.loads(content)
            # Key exists - return it (even if empty, user may have cleared it)
            if "summary_exclusions" in config:
                exclusions: list[str] = config["summary_exclusions"]
                return exclusions
            # Config exists but no exclusions key - add defaults
            config["summary_exclusions"] = DEFAULT_EXCLUSIONS.copy()
            vfs.write_file(CONFIG_FILE, json.dumps(config, indent=2))
            return DEFAULT_EXCLUSIONS.copy()
        elif create_default:
            # No config file - create with defaults
            config = {"summary_exclusions": DEFAULT_EXCLUSIONS.copy()}
            vfs.write_file(CONFIG_FILE, json.dumps(config, indent=2))
            return DEFAULT_EXCLUSIONS.copy()
    except (json.JSONDecodeError, FileNotFoundError, KeyError):
        pass
    return []
