"""
Quick Open - Fuzzy file finder (Ctrl+E)
"""

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QFocusEvent, QKeyEvent, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from forge.ui.branch_workspace import BranchWorkspace


def fuzzy_match(pattern: str, text: str) -> tuple[bool, int]:
    """
    Check if pattern fuzzy-matches text.

    Returns (matched, score) where score is lower for better matches.
    Score considers:
    - Position of first match (earlier is better)
    - Gaps between matched characters (fewer gaps is better)
    - Consecutive matches (bonus)
    """
    pattern = pattern.lower()
    text = text.lower()

    if not pattern:
        return True, 0

    # Try to match all pattern characters in order
    pattern_idx = 0
    text_idx = 0
    score = 0
    last_match_idx = -1
    first_match_idx = -1

    while pattern_idx < len(pattern) and text_idx < len(text):
        if pattern[pattern_idx] == text[text_idx]:
            if first_match_idx == -1:
                first_match_idx = text_idx

            # Bonus for consecutive matches
            if last_match_idx == text_idx - 1:
                score -= 5  # Consecutive bonus
            else:
                # Penalty for gaps
                if last_match_idx >= 0:
                    score += text_idx - last_match_idx - 1

            last_match_idx = text_idx
            pattern_idx += 1
        text_idx += 1

    if pattern_idx < len(pattern):
        # Not all pattern characters matched
        return False, 999999

    # Add penalty for late first match
    score += first_match_idx * 2

    # Bonus for matching at word boundaries (after / or _)
    if first_match_idx == 0 or (first_match_idx > 0 and text[first_match_idx - 1] in "/_"):
        score -= 10

    # Bonus for shorter filenames (prefer exact-ish matches)
    score += len(text) // 10

    return True, score


class QuickOpenWidget(QWidget):
    """
    Quick open popup for fuzzy file finding.

    Shows at the top of the parent widget as a pseudo-popup.
    """

    file_selected = Signal(str)  # Emitted when a file is selected
    cancelled = Signal()  # Emitted when cancelled (Escape)

    def __init__(self, workspace: "BranchWorkspace", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Make it a popup window so Qt doesn't try to lay it out with siblings
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        self.workspace = workspace
        self._all_files: list[str] = []

        self._setup_ui()
        self._load_files()

    def _setup_ui(self) -> None:
        """Setup the UI"""
        # Get font metrics for sizing
        line_height = self.fontMetrics().height()

        # Calculate sizes based on font
        input_padding = max(8, line_height // 2)
        item_height = int(line_height * 1.8)
        margin = max(8, line_height // 2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(4)

        # Style as a popup (light theme to match rest of UI)
        self.setStyleSheet(f"""
            QuickOpenWidget {{
                background-color: #ffffff;
                border: 1px solid #ccc;
                border-radius: 4px;
            }}
            QLineEdit {{
                background-color: #f5f5f5;
                color: #333;
                border: 1px solid #ccc;
                border-radius: 3px;
                padding: {input_padding}px;
            }}
            QListWidget {{
                background-color: #ffffff;
                color: #333;
                border: none;
            }}
            QListWidget::item {{
                padding: {item_height // 4}px {margin}px;
                min-height: {item_height}px;
            }}
            QListWidget::item:selected {{
                background-color: #0078d4;
                color: #ffffff;
            }}
            QListWidget::item:hover {{
                background-color: #e8e8e8;
            }}
        """)

        # Search input
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Type to search files...")
        self.search_input.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.search_input)

        # Results list - height based on showing ~10 items
        self.results_list = QListWidget()
        max_list_height = item_height * 12
        self.results_list.setMaximumHeight(max_list_height)
        self.results_list.itemActivated.connect(self._on_item_activated)
        layout.addWidget(self.results_list)

        # Install event filter for keyboard navigation
        self.search_input.installEventFilter(self)

    def _load_files(self) -> None:
        """Load all files from the workspace VFS"""
        self._all_files = sorted(self.workspace.vfs.list_files())
        self._update_results("")

    def _on_text_changed(self, text: str) -> None:
        """Handle search text change"""
        self._update_results(text)

    def _update_results(self, pattern: str) -> None:
        """Update the results list based on pattern"""
        self.results_list.clear()

        # Score and filter files
        scored_files: list[tuple[int, str]] = []
        for filepath in self._all_files:
            matched, score = fuzzy_match(pattern, filepath)
            if matched:
                scored_files.append((score, filepath))

        # Sort by score (lower is better)
        scored_files.sort(key=lambda x: x[0])

        # Show top results (limit to avoid sluggishness)
        for _score, filepath in scored_files[:50]:
            item = QListWidgetItem(filepath)
            self.results_list.addItem(item)

        # Select first item
        if self.results_list.count() > 0:
            self.results_list.setCurrentRow(0)

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        """Handle item selection"""
        filepath = item.text()
        self.file_selected.emit(filepath)
        self.hide()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        """Handle keyboard events for navigation"""
        if (
            obj == self.search_input
            and isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.KeyPress
        ):
            key = event.key()

            if key == Qt.Key.Key_Escape:
                self.cancelled.emit()
                self.hide()
                return True

            elif key == Qt.Key.Key_Return or key == Qt.Key.Key_Enter:
                # Select current item
                current = self.results_list.currentItem()
                if current:
                    self._on_item_activated(current)
                return True

            elif key == Qt.Key.Key_Down:
                # Move selection down
                current_row = self.results_list.currentRow()
                if current_row < self.results_list.count() - 1:
                    self.results_list.setCurrentRow(current_row + 1)
                return True

            elif key == Qt.Key.Key_Up:
                # Move selection up
                current_row = self.results_list.currentRow()
                if current_row > 0:
                    self.results_list.setCurrentRow(current_row - 1)
                return True

        return super().eventFilter(obj, event)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        """Handle show event - focus the search input"""
        super().showEvent(event)
        self.search_input.clear()
        self.search_input.setFocus()
        self._load_files()  # Refresh file list

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        """Hide when focus is lost"""
        # Don't hide if focus went to our child widgets
        focus_widget = QApplication.focusWidget()
        if focus_widget and self.isAncestorOf(focus_widget):
            return
        super().focusOutEvent(event)
