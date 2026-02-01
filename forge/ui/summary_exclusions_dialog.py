"""
Dialog for configuring per-repository summary exclusion patterns.
"""

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
DEFAULT_EXCLUSIONS = [
    "node_modules/",
    "__pycache__/",
    ".venv/",
    "venv/",
    ".git/",
    "*.min.js",
    "*.min.css",
    "*.lock",
    "package-lock.json",
    "yarn.lock",
    "poetry.lock",
    "*.pyc",
    ".DS_Store",
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

        # Pattern list
        self.pattern_list = QListWidget()
        self.pattern_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.pattern_list.itemSelectionChanged.connect(self._update_button_states)
        list_row.addWidget(self.pattern_list)

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
            "<i>Changes apply to new summarization runs. "
            "Use Regenerate Summaries to apply immediately.</i>"
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
            if self.pattern_list.item(i).text() == pattern:
                self.pattern_input.clear()
                self.pattern_list.setCurrentRow(i)
                return

        item = QListWidgetItem(pattern)
        self.pattern_list.addItem(item)
        self.pattern_list.setCurrentItem(item)
        self.pattern_input.clear()
        self.pattern_input.setFocus()

    def _remove_pattern(self) -> None:
        """Remove the selected pattern from the list."""
        row = self.pattern_list.currentRow()
        if row >= 0:
            self.pattern_list.takeItem(row)
            self._update_button_states()

    def _move_up(self) -> None:
        """Move the selected pattern up in the list."""
        row = self.pattern_list.currentRow()
        if row > 0:
            item = self.pattern_list.takeItem(row)
            self.pattern_list.insertItem(row - 1, item)
            self.pattern_list.setCurrentRow(row - 1)

    def _move_down(self) -> None:
        """Move the selected pattern down in the list."""
        row = self.pattern_list.currentRow()
        if row < self.pattern_list.count() - 1:
            item = self.pattern_list.takeItem(row)
            self.pattern_list.insertItem(row + 1, item)
            self.pattern_list.setCurrentRow(row + 1)

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
        self.workspace.vfs.write_file(CONFIG_FILE, json.dumps(config, indent=2))
        # Commit the config change
        self.workspace.commit("Update summary exclusion patterns")

    def _load_patterns(self) -> None:
        """Load current exclusion patterns into the list."""
        config = self._load_config()
        patterns = config.get("summary_exclusions", [])
        for pattern in patterns:
            self.pattern_list.addItem(QListWidgetItem(pattern))

    def _save_and_close(self) -> None:
        """Save patterns and close the dialog."""
        # Collect patterns from list
        patterns = []
        for i in range(self.pattern_list.count()):
            patterns.append(self.pattern_list.item(i).text())

        # Load existing config, update exclusions, save
        config = self._load_config()
        config["summary_exclusions"] = patterns
        self._save_config(config)

        self.accept()


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