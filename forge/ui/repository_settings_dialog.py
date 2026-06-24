"""
Dialog for configuring per-repository settings stored in .forge/config.json.

This is the repository-scoped counterpart to the global SettingsDialog. It
edits configuration that lives in the repo itself (committed via the VFS as a
PREPARE commit), such as summary exclusion patterns and the test command used
by the run_tests tool.

All config keys share a single .forge/config.json file, so saving does a
read-modify-write to avoid clobbering keys this dialog doesn't manage (e.g.
"enabled_tools").
"""

import json
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from forge.ui.summary_exclusions_dialog import (
    CONFIG_FILE,
    load_summary_exclusions,
    matches_pattern,
)

if TYPE_CHECKING:
    from forge.ui.branch_workspace import BranchWorkspace


class RepositorySettingsDialog(QDialog):
    """Tabbed dialog for editing per-repository configuration.

    Tabs:
      - Summarization: summary exclusion patterns (gitignore syntax)
      - Testing: the test command used by the run_tests tool
      - Tools: optional built-in tools enabled for this repository
    """

    def __init__(self, workspace: "BranchWorkspace", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.setWindowTitle("Repository Settings")
        self.setMinimumWidth(560)
        self.setMinimumHeight(440)

        # Cache file list for pattern match counts.
        self._all_files: list[str] = []

        self._setup_ui()
        self._load()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_summarization_tab(), "Summarization")
        self.tabs.addTab(self._build_testing_tab(), "Testing")
        self.tabs.addTab(self._build_tools_tab(), "Tools")
        layout.addWidget(self.tabs)

        # Shared bottom button box
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

    def _build_summarization_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        title = QLabel("<b>Exclude from Summarization</b>")
        layout.addWidget(title)

        instructions = QLabel(
            "Exclude committed files from AI summaries (gitignore syntax):\n"
            "• <code>folder/</code> — exclude folder anywhere\n"
            "• <code>/folder/</code> — exclude folder at root only\n"
            "• <code>*.ext</code> — exclude extension everywhere\n"
            "<i>Note: Files in .gitignore are already excluded.</i>"
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

        # List and control buttons
        list_row = QHBoxLayout()
        self.pattern_list = QListWidget()
        self.pattern_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.pattern_list.itemSelectionChanged.connect(self._update_button_states)
        list_row.addWidget(self.pattern_list)

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

        note = QLabel("<i>Changes take effect on the next AI request.</i>")
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(note)

        self._update_button_states()
        return tab

    def _build_testing_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        title = QLabel("<b>Test Command</b>")
        layout.addWidget(title)

        instructions = QLabel(
            "Command used by the run_tests tool. When set, it overrides "
            "automatic test-command discovery and is run via the shell from "
            "the repository root, so it may include arguments and pipes "
            "(e.g. <code>pytest -q</code> or <code>npm test</code>).\n"
            "<i>Leave empty to auto-discover (Makefile, pytest, package.json).</i>"
        )
        instructions.setTextFormat(Qt.TextFormat.RichText)
        instructions.setWordWrap(True)
        instructions.setStyleSheet("color: #666; margin-bottom: 8px;")
        layout.addWidget(instructions)

        self.test_command_input = QLineEdit()
        self.test_command_input.setPlaceholderText("Auto-discover (e.g. pytest -q)")
        layout.addWidget(self.test_command_input)

        note = QLabel("<i>Changes take effect on the next run_tests call.</i>")
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(note)

        layout.addStretch()
        return tab

    def _build_tools_tab(self) -> QWidget:
        from forge.tools.manager import ToolManager

        tab = QWidget()
        layout = QVBoxLayout(tab)

        desc = QLabel(
            "Enable optional built-in tools for this repository.\n"
            "These tools are available to the AI when enabled."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("margin-bottom: 8px;")
        layout.addWidget(desc)

        # Human-readable descriptions for known conditional tools.
        tool_descriptions: dict[str, str] = {
            "web_search": "Search the web using DuckDuckGo",
            "web_read": "Fetch and extract content from web pages",
        }

        # Current enabled tools from the (already-loaded) config.
        enabled_tools = set(self._load_config().get("enabled_tools", []))

        self._tool_checkboxes: dict[str, QCheckBox] = {}
        conditional_tools = sorted(ToolManager.CONDITIONAL_TOOLS)

        if not conditional_tools:
            no_tools = QLabel("No optional tools available.")
            no_tools.setStyleSheet("color: #888;")
            layout.addWidget(no_tools)
        else:
            for tool_name in conditional_tools:
                description = tool_descriptions.get(tool_name, tool_name)
                checkbox = QCheckBox(f"{tool_name} \u2014 {description}")
                checkbox.setChecked(tool_name in enabled_tools)
                self._tool_checkboxes[tool_name] = checkbox
                layout.addWidget(checkbox)

        note = QLabel("<i>Changes take effect on the next AI turn.</i>")
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 10px; margin-top: 12px;")
        layout.addWidget(note)

        layout.addStretch()
        return tab

    # ------------------------------------------------------------------
    # Summarization tab behavior
    # ------------------------------------------------------------------
    def _update_button_states(self) -> None:
        selected = self.pattern_list.currentRow()
        has_selection = selected >= 0
        count = self.pattern_list.count()
        self.up_btn.setEnabled(has_selection and selected > 0)
        self.down_btn.setEnabled(has_selection and selected < count - 1)
        self.remove_btn.setEnabled(has_selection)

    def _add_pattern(self) -> None:
        pattern = self.pattern_input.text().strip()
        if not pattern:
            return
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
        row = self.pattern_list.currentRow()
        if row >= 0:
            self.pattern_list.takeItem(row)
            self._update_button_states()
            self._update_file_counts()

    def _move_up(self) -> None:
        row = self.pattern_list.currentRow()
        if row > 0:
            item = self.pattern_list.takeItem(row)
            self.pattern_list.insertItem(row - 1, item)
            self.pattern_list.setCurrentRow(row - 1)
            self._update_file_counts()

    def _move_down(self) -> None:
        row = self.pattern_list.currentRow()
        if row < self.pattern_list.count() - 1:
            item = self.pattern_list.takeItem(row)
            self.pattern_list.insertItem(row + 1, item)
            self.pattern_list.setCurrentRow(row + 1)
            self._update_file_counts()

    def _add_pattern_item(self, pattern: str) -> QListWidgetItem:
        item = QListWidgetItem(pattern)
        item.setData(Qt.ItemDataRole.UserRole, pattern)  # Store raw pattern
        self.pattern_list.addItem(item)
        return item

    def _update_file_counts(self) -> None:
        already_matched: set[str] = set()
        for i in range(self.pattern_list.count()):
            item = self.pattern_list.item(i)
            pattern = item.data(Qt.ItemDataRole.UserRole)

            matches_this = set()
            for filepath in self._all_files:
                if filepath not in already_matched and matches_pattern(filepath, pattern):
                    matches_this.add(filepath)

            new_matches = len(matches_this)
            total_would_match = sum(1 for f in self._all_files if matches_pattern(f, pattern))

            if new_matches == total_would_match:
                item.setText(f"{pattern}  ({new_matches} files)")
            else:
                item.setText(f"{pattern}  ({new_matches} new, {total_would_match} total)")

            already_matched.update(matches_this)

    # ------------------------------------------------------------------
    # Load / save (.forge/config.json)
    # ------------------------------------------------------------------
    def _load_config(self) -> dict:
        """Load the repo config file, returning {} when absent/invalid."""
        try:
            if self.workspace.vfs.file_exists(CONFIG_FILE):
                content = self.workspace.vfs.read_file(CONFIG_FILE)
                result: dict = json.loads(content)
                return result
        except (json.JSONDecodeError, FileNotFoundError, KeyError):
            pass
        return {}

    def _load(self) -> None:
        """Populate both tabs from current config."""
        self._all_files = self.workspace.vfs.list_files()

        # Summarization patterns (creates defaults if needed, same as before).
        patterns = load_summary_exclusions(self.workspace.vfs, create_default=True)
        for pattern in patterns:
            self._add_pattern_item(pattern)
        self._update_file_counts()

        # Test command.
        config = self._load_config()
        command = config.get("test_command", "")
        if isinstance(command, str):
            self.test_command_input.setText(command)

    def _save_and_close(self) -> None:
        from forge.git_backend.commit_types import CommitType

        # Read-modify-write so we preserve keys we don't manage here.
        config = self._load_config()

        patterns = [
            self.pattern_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.pattern_list.count())
        ]
        config["summary_exclusions"] = patterns
        config["test_command"] = self.test_command_input.text().strip()
        config["enabled_tools"] = sorted(
            name for name, checkbox in self._tool_checkboxes.items() if checkbox.isChecked()
        )

        self.workspace.vfs.write_file(CONFIG_FILE, json.dumps(config, indent=2))
        self.workspace.vfs.commit("Update repository settings", commit_type=CommitType.PREPARE)
        self.accept()
