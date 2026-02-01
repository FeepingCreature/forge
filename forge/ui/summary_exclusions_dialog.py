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
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from forge.ui.branch_workspace import BranchWorkspace

CONFIG_FILE = ".forge/config.json"


class SummaryExclusionsDialog(QDialog):
    """Dialog for editing summary exclusion patterns."""

    def __init__(self, workspace: "BranchWorkspace", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.setWindowTitle("Summary Exclusions")
        self.setMinimumWidth(450)
        self.setMinimumHeight(350)

        self._setup_ui()
        self._load_patterns()

    def _setup_ui(self) -> None:
        """Setup the dialog UI."""
        layout = QVBoxLayout(self)

        # Title and instructions
        title = QLabel("<b>Exclude from Summarization</b>")
        layout.addWidget(title)

        instructions = QLabel(
            "Enter patterns to exclude from AI file summaries (one per line):\n"
            "• <code>folder/</code> — exclude entire folder\n"
            "• <code>*.ext</code> — exclude extension everywhere\n"
            "• <code>folder/*.ext</code> — exclude extension in folder\n"
            "• <code>path/to/file.py</code> — exclude specific file"
        )
        instructions.setWordWrap(True)
        instructions.setTextFormat(Qt.TextFormat.RichText)
        instructions.setStyleSheet("color: #666; margin-bottom: 8px;")
        layout.addWidget(instructions)

        # Text area for patterns
        self.patterns_edit = QPlainTextEdit()
        self.patterns_edit.setPlaceholderText(
            "node_modules/\nvendor/\n*.min.js\n*.lock\ntests/__snapshots__/"
        )
        layout.addWidget(self.patterns_edit)

        # Note about when changes take effect
        note = QLabel(
            "<i>Changes apply to new summarization runs. "
            "Use Regenerate Summaries to apply immediately.</i>"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(note)

        # Buttons
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
        """Load current exclusion patterns into the text area."""
        config = self._load_config()
        patterns = config.get("summary_exclusions", [])
        self.patterns_edit.setPlainText("\n".join(patterns))

    def _save_and_close(self) -> None:
        """Save patterns and close the dialog."""
        # Parse patterns from text (one per line, strip whitespace, skip empty)
        text = self.patterns_edit.toPlainText()
        patterns = [line.strip() for line in text.split("\n") if line.strip()]

        # Load existing config, update exclusions, save
        config = self._load_config()
        config["summary_exclusions"] = patterns
        self._save_config(config)

        self.accept()


def load_summary_exclusions(vfs) -> list[str]:
    """
    Load summary exclusion patterns from repo config.

    Args:
        vfs: The VFS to read from

    Returns:
        List of exclusion patterns
    """
    try:
        if vfs.file_exists(CONFIG_FILE):
            content = vfs.read_file(CONFIG_FILE)
            config = json.loads(content)
            return config.get("summary_exclusions", [])
    except (json.JSONDecodeError, FileNotFoundError, KeyError):
        pass
    return []