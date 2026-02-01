"""
Dialog for configuring per-repository summary exclusion patterns.
"""

import fnmatch
import json
import re
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from forge.ui.branch_workspace import BranchWorkspace
    from forge.vfs.base import VFS

CONFIG_FILE = ".forge/config.json"

# Default exclusion patterns for new repositories
# These match the DEFAULT_EXCLUDE_DIRS used in grep tools, plus common generated files
DEFAULT_EXCLUSIONS = [
    # Directories (matching grep_open/grep_context defaults)
    ".git/",
    "__pycache__/",
    "node_modules/",
    ".venv/",
    "venv/",
    # Build/dist directories
    "dist/",
    "build/",
    ".next/",
    ".nuxt/",
    "coverage/",
    # IDE/editor directories
    ".idea/",
    ".vscode/",
    # Minified files
    "*.min.js",
    "*.min.css",
    # Lock files
    "*.lock",
    "package-lock.json",
    "yarn.lock",
    "poetry.lock",
    "pnpm-lock.yaml",
    # Compiled files
    "*.pyc",
    "*.pyo",
    # OS files
    ".DS_Store",
    "Thumbs.db",
]


class SummaryExclusionsDialog(QDialog):
    """Dialog for editing summary exclusion patterns with a list interface."""

    def __init__(self, workspace: "BranchWorkspace", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.setWindowTitle("Summary Exclusions")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)

        self._setup_ui()
        self._load_patterns()

    def _setup_ui(self) -> None:
        """Setup the dialog UI."""
        layout = QVBoxLayout(self)

        # Title and instructions
        title = QLabel("<b>Exclude from Summarization</b>")
        layout.addWidget(title)

        instructions = QLabel(
            "Patterns use gitignore syntax:\n"
            "• <code>folder/</code> — exclude folder anywhere\n"
            "• <code>/folder/</code> — exclude folder at root only\n"
            "• <code>*.ext</code> — exclude extension everywhere\n"
            "• <code>**/test/*.py</code> — glob with <code>**</code> wildcards"
        )
        instructions.setTextFormat(Qt.TextFormat.RichText)
        instructions.setStyleSheet("color: #666; margin-bottom: 8px;")
        layout.addWidget(instructions)

        # Input row for adding new patterns
        input_layout = QHBoxLayout()
        self.pattern_input = QLineEdit()
        self.pattern_input.setPlaceholderText("Enter pattern (e.g., node_modules/ or *.min.js)")
        self.pattern_input.returnPressed.connect(self._add_pattern)
        input_layout.addWidget(self.pattern_input)

        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add_pattern)
        add_btn.setFixedWidth(60)
        input_layout.addWidget(add_btn)

        layout.addLayout(input_layout)

        # List and control buttons in horizontal layout
        list_row = QHBoxLayout()

        # Pattern list (two columns: pattern and file count)
        self.pattern_list = QListWidget()
        self.pattern_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.pattern_list.itemSelectionChanged.connect(self._update_button_states)
        list_row.addWidget(self.pattern_list)

        # Cache all files for counting
        self._all_files: list[str] = []

        # Control buttons (vertical)
        btn_layout = QVBoxLayout()
        btn_layout.setSpacing(4)

        self.up_btn = QPushButton("▲")
        self.up_btn.setFixedWidth(32)
        self.up_btn.setToolTip("Move up")
        self.up_btn.clicked.connect(self._move_up)
        btn_layout.addWidget(self.up_btn)

        self.down_btn = QPushButton("▼")
        self.down_btn.setFixedWidth(32)
        self.down_btn.setToolTip("Move down")
        self.down_btn.clicked.connect(self._move_down)
        btn_layout.addWidget(self.down_btn)

        btn_layout.addSpacing(8)

        self.remove_btn = QPushButton("✕")
        self.remove_btn.setFixedWidth(32)
        self.remove_btn.setToolTip("Remove selected")
        self.remove_btn.clicked.connect(self._remove_pattern)
        btn_layout.addWidget(self.remove_btn)

        btn_layout.addStretch()
        list_row.addLayout(btn_layout)

        layout.addLayout(list_row)

        # Note about when changes take effect
        note = QLabel("<i>Changes take effect on the next AI request.</i>")
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(note)

        # Dialog buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save_and_close)
        save_btn.setDefault(True)

        button_layout.addWidget(cancel_btn)
        button_layout.addWidget(save_btn)

        layout.addLayout(button_layout)

        # Initial button states
        self._update_button_states()

    def _update_button_states(self) -> None:
        """Update enabled state of control buttons based on selection."""
        selected = self.pattern_list.currentRow()
        has_selection = selected >= 0
        count = self.pattern_list.count()

        self.up_btn.setEnabled(has_selection and selected > 0)
        self.down_btn.setEnabled(has_selection and selected < count - 1)
        self.remove_btn.setEnabled(has_selection)

    def _add_pattern(self) -> None:
        """Add the pattern from input field to the list."""
        pattern = self.pattern_input.text().strip()
        if not pattern:
            return

        # Check for duplicates
        for i in range(self.pattern_list.count()):
            if self.pattern_list.item(i).data(Qt.ItemDataRole.UserRole) == pattern:
                self.pattern_input.clear()
                self.pattern_list.setCurrentRow(i)
                return

        item = self._add_pattern_item(pattern)
        self.pattern_list.setCurrentItem(item)
        self.pattern_input.clear()
        self.pattern_input.setFocus()
        self._update_file_counts()

    def _remove_pattern(self) -> None:
        """Remove the selected pattern from the list."""
        row = self.pattern_list.currentRow()
        if row >= 0:
            self.pattern_list.takeItem(row)
            self._update_button_states()
            self._update_file_counts()

    def _move_up(self) -> None:
        """Move the selected pattern up in the list."""
        row = self.pattern_list.currentRow()
        if row > 0:
            item = self.pattern_list.takeItem(row)
            self.pattern_list.insertItem(row - 1, item)
            self.pattern_list.setCurrentRow(row - 1)
            self._update_file_counts()

    def _move_down(self) -> None:
        """Move the selected pattern down in the list."""
        row = self.pattern_list.currentRow()
        if row < self.pattern_list.count() - 1:
            item = self.pattern_list.takeItem(row)
            self.pattern_list.insertItem(row + 1, item)
            self.pattern_list.setCurrentRow(row + 1)
            self._update_file_counts()

    def _load_config(self) -> dict[str, list[str]]:
        """Load the repo config file."""
        try:
            if self.workspace.vfs.file_exists(CONFIG_FILE):
                content = self.workspace.vfs.read_file(CONFIG_FILE)
                result: dict[str, list[str]] = json.loads(content)
                return result
        except (json.JSONDecodeError, FileNotFoundError, KeyError):
            pass
        return {}

    def _save_config(self, config: dict) -> None:
        """Save the repo config file."""
        from forge.git_backend.commit_types import CommitType

        self.workspace.vfs.write_file(CONFIG_FILE, json.dumps(config, indent=2))
        # Commit as PREPARE so it merges into the next real commit
        self.workspace.vfs.commit(
            "Update summary exclusion patterns", commit_type=CommitType.PREPARE
        )

    def _load_patterns(self) -> None:
        """Load current exclusion patterns into the list.

        Uses load_summary_exclusions() which creates defaults if needed.
        """
        # Cache file list for counting
        self._all_files = self.workspace.vfs.list_files()

        patterns = load_summary_exclusions(self.workspace.vfs, create_default=True)
        for pattern in patterns:
            self._add_pattern_item(pattern)

        self._update_file_counts()

    def _add_pattern_item(self, pattern: str) -> QListWidgetItem:
        """Add a pattern to the list with placeholder count."""
        item = QListWidgetItem(pattern)
        item.setData(Qt.ItemDataRole.UserRole, pattern)  # Store raw pattern
        self.pattern_list.addItem(item)
        return item

    def _update_file_counts(self) -> None:
        """Update file counts for all patterns, showing incremental matches."""
        already_matched: set[str] = set()

        for i in range(self.pattern_list.count()):
            item = self.pattern_list.item(i)
            pattern = item.data(Qt.ItemDataRole.UserRole)

            # Count files matching this pattern
            matches_this = set()
            for filepath in self._all_files:
                if filepath not in already_matched and matches_pattern(filepath, pattern):
                    matches_this.add(filepath)

            # Update display text
            new_matches = len(matches_this)
            total_would_match = sum(1 for f in self._all_files if matches_pattern(f, pattern))

            if new_matches == total_would_match:
                item.setText(f"{pattern}  ({new_matches} files)")
            else:
                item.setText(f"{pattern}  ({new_matches} new, {total_would_match} total)")

            already_matched.update(matches_this)

    def _save_and_close(self) -> None:
        """Save patterns and close the dialog."""
        # Collect patterns from list (use stored raw pattern, not display text)
        patterns = []
        for i in range(self.pattern_list.count()):
            patterns.append(self.pattern_list.item(i).data(Qt.ItemDataRole.UserRole))

        # Load existing config, update exclusions, save
        config = self._load_config()
        config["summary_exclusions"] = patterns
        self._save_config(config)

        self.accept()


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
