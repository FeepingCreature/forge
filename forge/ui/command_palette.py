"""
Command Palette for Forge.

A fuzzy-searchable list of all available actions, triggered by Ctrl+Shift+P.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from forge.ui.actions import Action, ActionRegistry


def fuzzy_match(pattern: str, text: str) -> tuple[bool, int]:
    """
    Check if pattern fuzzy-matches text.
    Returns (matched, score) where lower score is better.
    """
    pattern = pattern.lower()
    text_lower = text.lower()

    # Exact substring match gets best score
    if pattern in text_lower:
        return True, text_lower.index(pattern)

    # Fuzzy match: all pattern chars must appear in order
    pattern_idx = 0
    score = 0
    last_match = -1

    for i, char in enumerate(text_lower):
        if pattern_idx < len(pattern) and char == pattern[pattern_idx]:
            # Penalize gaps between matches
            if last_match >= 0:
                score += (i - last_match - 1) * 2
            last_match = i
            pattern_idx += 1

    if pattern_idx == len(pattern):
        return True, score
    return False, 999999


class CommandPaletteItem(QWidget):
    """Custom widget for command palette items."""

    def __init__(self, action: Action, shortcut: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.action = action

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        # Action name
        name_label = QLabel(action.name)
        name_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(name_label)

        # Category (dimmed)
        category_label = QLabel(f"[{action.category}]")
        category_label.setStyleSheet("color: #888;")
        layout.addWidget(category_label)

        layout.addStretch()

        # Shortcut (if any)
        if shortcut:
            shortcut_label = QLabel(shortcut)
            shortcut_label.setStyleSheet(
                "background: #444; color: #fff; padding: 2px 6px; border-radius: 3px; font-size: 11px;"
            )
            layout.addWidget(shortcut_label)


class CommandPalette(QDialog):
    """
    Command palette dialog.

    Shows all available actions with fuzzy search.
    Triggered by Ctrl+Shift+P.
    """

    action_triggered = Signal(str)  # Emits action_id when an action is selected

    def __init__(self, registry: ActionRegistry, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.registry = registry
        self.setWindowTitle("Command Palette")
        self.setMinimumSize(500, 400)
        self.resize(600, 450)
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)

        self._setup_ui()
        self._populate_actions()

    def _setup_ui(self) -> None:
        """Setup the dialog UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Search input
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Type to search commands...")
        self.search_input.setStyleSheet(
            """
            QLineEdit {
                padding: 12px;
                font-size: 14px;
                border: none;
                border-bottom: 1px solid #ccc;
                background: #f5f5f5;
            }
            """
        )
        self.search_input.textChanged.connect(self._filter_actions)
        layout.addWidget(self.search_input)

        # Action list
        self.action_list = QListWidget()
        self.action_list.setStyleSheet(
            """
            QListWidget {
                border: none;
                background: white;
            }
            QListWidget::item {
                padding: 4px;
                border-bottom: 1px solid #eee;
            }
            QListWidget::item:selected {
                background: #e3f2fd;
            }
            QListWidget::item:hover {
                background: #f5f5f5;
            }
            """
        )
        self.action_list.itemActivated.connect(self._on_item_activated)
        self.action_list.itemDoubleClicked.connect(self._on_item_activated)
        layout.addWidget(self.action_list)

    def _populate_actions(self) -> None:
        """Populate the action list."""
        self._all_actions = self.registry.get_all()
        self._update_list(self._all_actions)

    def _update_list(self, actions: list[Action]) -> None:
        """Update the list with given actions."""
        self.action_list.clear()

        for action in actions:
            item = QListWidgetItem(self.action_list)
            shortcut = self.registry.get_effective_shortcut(action.id)
            widget = CommandPaletteItem(action, shortcut)
            item.setSizeHint(widget.sizeHint())
            item.setData(Qt.ItemDataRole.UserRole, action.id)
            self.action_list.addItem(item)
            self.action_list.setItemWidget(item, widget)

        # Select first item
        if self.action_list.count() > 0:
            self.action_list.setCurrentRow(0)

    def _filter_actions(self, text: str) -> None:
        """Filter actions based on search text."""
        if not text:
            self._update_list(self._all_actions)
            return

        # Score and sort actions
        scored: list[tuple[int, Action]] = []
        for action in self._all_actions:
            # Match against name, category, and description
            name_match, name_score = fuzzy_match(text, action.name)
            cat_match, cat_score = fuzzy_match(text, action.category)
            desc_match, desc_score = fuzzy_match(text, action.description)
            id_match, id_score = fuzzy_match(text, action.id)

            if name_match or cat_match or desc_match or id_match:
                # Prefer name matches
                best_score = min(
                    name_score if name_match else 999999,
                    cat_score + 100 if cat_match else 999999,
                    desc_score + 200 if desc_match else 999999,
                    id_score + 50 if id_match else 999999,
                )
                scored.append((best_score, action))

        scored.sort(key=lambda x: x[0])
        self._update_list([action for _, action in scored])

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        """Handle item activation (Enter or double-click)."""
        action_id = item.data(Qt.ItemDataRole.UserRole)
        if action_id:
            self.action_triggered.emit(action_id)
            self.accept()

    def keyPressEvent(self, event: object) -> None:  # noqa: N802
        """Handle key events."""
        from PySide6.QtGui import QKeyEvent

        if not isinstance(event, QKeyEvent):
            return

        key = event.key()

        if key == Qt.Key.Key_Escape:
            self.reject()
        elif key == Qt.Key.Key_Return or key == Qt.Key.Key_Enter:
            current = self.action_list.currentItem()
            if current:
                self._on_item_activated(current)
        elif key == Qt.Key.Key_Up:
            row = self.action_list.currentRow()
            if row > 0:
                self.action_list.setCurrentRow(row - 1)
        elif key == Qt.Key.Key_Down:
            row = self.action_list.currentRow()
            if row < self.action_list.count() - 1:
                self.action_list.setCurrentRow(row + 1)
        else:
            # Forward to search input
            self.search_input.setFocus()
            self.search_input.keyPressEvent(event)

    def showEvent(self, event: object) -> None:  # noqa: N802
        """Focus search input when shown."""
        super().showEvent(event)  # type: ignore[arg-type]
        self.search_input.setFocus()
        self.search_input.selectAll()
