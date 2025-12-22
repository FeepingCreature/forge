"""
Global search widget for searching across all files in the repository.
"""

import re
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
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


class GlobalSearchDialog(QDialog):
    """
    Global search dialog for searching across all files.

    Shows matches with file path and line preview.
    Double-click or Enter opens the file at that line.
    """

    # Emitted when user selects a result (filepath, line_number)
    file_selected = Signal(str, int)

    def __init__(self, workspace: "BranchWorkspace", parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.setWindowTitle("Search in Files")
        self.setMinimumSize(600, 400)
        self.resize(700, 500)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the search UI"""
        layout = QVBoxLayout(self)

        # Search input row
        search_row = QHBoxLayout()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search pattern (regex supported)...")
        self.search_input.textChanged.connect(self._on_search_changed)
        self.search_input.returnPressed.connect(self._open_selected)
        search_row.addWidget(self.search_input)

        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self._do_search)
        search_row.addWidget(self.search_button)

        layout.addLayout(search_row)

        # Status label
        self.status_label = QLabel("Enter a search pattern")
        self.status_label.setStyleSheet("color: #666;")
        layout.addWidget(self.status_label)

        # Results list
        self.results_list = QListWidget()
        self.results_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.results_list.itemActivated.connect(self._on_item_activated)
        layout.addWidget(self.results_list)

        # Focus search input
        self.search_input.setFocus()

    def _on_search_changed(self, text: str) -> None:
        """Handle search text change - search after short delay or immediately on Enter"""
        # Could add debouncing here, but for now just update status
        if text:
            self.status_label.setText("Press Enter or click Search")
        else:
            self.status_label.setText("Enter a search pattern")

    def _do_search(self) -> None:
        """Execute the search"""
        pattern = self.search_input.text().strip()
        if not pattern:
            return

        self.results_list.clear()
        self.status_label.setText("Searching...")

        # Get all files from VFS
        files = self.workspace.vfs.list_files()
        # Filter out .forge/ files
        files = [f for f in files if not f.startswith(".forge/")]

        matches: list[tuple[str, int, str]] = []  # (filepath, line_num, line_content)
        total_matches = 0

        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            self.status_label.setText(f"Invalid regex: {e}")
            return

        for filepath in files:
            try:
                content = self.workspace.vfs.read_file(filepath)
                lines = content.split("\n")

                for line_num, line in enumerate(lines, 1):
                    if regex.search(line):
                        matches.append((filepath, line_num, line.strip()[:100]))
                        total_matches += 1

                        # Limit results to avoid UI slowdown
                        if total_matches >= 500:
                            break

            except Exception:
                continue  # Skip files we can't read

            if total_matches >= 500:
                break

        # Populate results
        for filepath, line_num, line_preview in matches:
            item = QListWidgetItem()
            item.setText(f"{filepath}:{line_num}  {line_preview}")
            item.setData(Qt.ItemDataRole.UserRole, (filepath, line_num))
            self.results_list.addItem(item)

        if total_matches >= 500:
            self.status_label.setText("Found 500+ matches (showing first 500)")
        else:
            self.status_label.setText(f"Found {total_matches} match(es)")

        # Select first result
        if self.results_list.count() > 0:
            self.results_list.setCurrentRow(0)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        """Handle double-click on result"""
        self._open_item(item)

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        """Handle Enter on result"""
        self._open_item(item)

    def _open_selected(self) -> None:
        """Open the currently selected result"""
        item = self.results_list.currentItem()
        if item:
            self._open_item(item)
        else:
            # No selection, do search first
            self._do_search()

    def _open_item(self, item: QListWidgetItem) -> None:
        """Open a result item"""
        data = item.data(Qt.ItemDataRole.UserRole)
        if data:
            filepath, line_num = data
            self.file_selected.emit(filepath, line_num)
            self.accept()
