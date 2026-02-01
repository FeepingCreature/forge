"""
Dialog for configuring per-repository summary exclusion patterns.
"""

import fnmatch
import json
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
            "Patterns to exclude from AI file summaries:\n"
            "• <code>folder/</code> — exclude entire folder\n"
            "• <code>*.ext</code> — exclude extension everywhere\n"
            "• <code>folder/*.ext</code> — exclude extension in folder"
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
        note = QLabel(
            "<i>Changes take effect on the next AI request.</i>"
        )
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

    def _load_config(self) -> dict:
        """Load the repo config file."""
        try:
            if self.workspace.vfs.file_exists(CONFIG_FILE):
                content = self.workspace.vfs.read_file(CONFIG_FILE)
                return json.loads(content)
        except (json.JSONDecodeError, FileNotFoundError, KeyError):
            pass
        return {}

    def _save_config(self, config: dict) -> None:
        """Save the repo config file."""
        from forge.git_backend.commit_types import CommitType

        self.workspace.vfs.write_file(CONFIG_FILE, json.dumps(config, indent=2))
        # Commit as PREPARE so it merges into the next real commit
        self.workspace.vfs.commit("Update summary exclusion patterns", commit_type=CommitType.PREPARE)

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
    """Check if a filepath matches an exclusion pattern.
    
    Patterns can be:
    - folder/ → matches folder/**/* (entire directory)
    - *.ext → matches **/*.ext (extension anywhere)
    - folder/*.ext → matches folder/*.ext (specific folder + extension)
    - exact/path.py → matches exact path
    """
    if not pattern:
        return False

    # Directory pattern: "folder/" matches everything under folder
    if pattern.endswith("/"):
        dir_prefix = pattern
        return filepath.startswith(dir_prefix) or filepath + "/" == dir_prefix

    # Extension pattern without path: "*.ext" matches anywhere
    if pattern.startswith("*.") and "/" not in pattern:
        ext_pattern = pattern[1:]  # e.g., ".min.js"
        return filepath.endswith(ext_pattern)

    # General glob pattern: use fnmatch
    if fnmatch.fnmatch(filepath, pattern):
        return True

    # Also try with ** prefix for patterns that should match anywhere
    if "*" in pattern and not pattern.startswith("*"):
        if fnmatch.fnmatch(filepath, "**/" + pattern):
            return True

    return False


def load_summary_exclusions(vfs, create_default: bool = True) -> list[str]:
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
                return config["summary_exclusions"]
            # Config exists but no exclusions key - add defaults
            config["summary_exclusions"] = DEFAULT_EXCLUSIONS.copy()
            vfs.write_file(CONFIG_FILE, json.dumps(config, indent=2))
            return config["summary_exclusions"]
        elif create_default:
            # No config file - create with defaults
            config = {"summary_exclusions": DEFAULT_EXCLUSIONS.copy()}
            vfs.write_file(CONFIG_FILE, json.dumps(config, indent=2))
            return config["summary_exclusions"]
    except (json.JSONDecodeError, FileNotFoundError, KeyError):
        pass
    return []