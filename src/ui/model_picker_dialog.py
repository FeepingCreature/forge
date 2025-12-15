"""
Hierarchical model picker with Miller columns (adjacent list views)
"""

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QFocusEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)


@dataclass
class ItemData:
    """Data stored in each list item"""

    model_name: str | None  # Full model name if leaf, None if group
    children: dict[str, Any] | None  # Child hierarchy if group
    child_prefix: str  # Prefix to strip from children's display names


class ModelPickerPopup(QFrame):
    """
    Popup model picker with Miller column navigation.

    Shows as a dropdown/popup anchored to a button, with cascading columns.
    """

    model_selected = Signal(str)
    cancelled = Signal()

    # Tuning parameters
    MAX_ITEMS_BEFORE_SPLIT = 12
    IDEAL_GROUP_SIZE = 5
    MAX_GROUPS = 10
    PRIMARY_DELIMITER = "/"
    SECONDARY_DELIMITERS = ["-", ":", "_"]

    def __init__(
        self,
        models: list[str],
        current_selection: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.models = sorted(models)
        self.current_selection = current_selection
        self.selected_model: str | None = None
        self.columns: list[QListWidget] = []

        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        self.setStyleSheet(
            "ModelPickerPopup { background: white; border: 1px solid #888; border-radius: 4px; }"
        )

        self._setup_ui()
        self._populate_column(0, self._build_hierarchy(self.models), "")
        self._navigate_to_selection()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Type to filter...")
        self.filter_input.textChanged.connect(self._apply_filter)
        layout.addWidget(self.filter_input)

        self.columns_container = QWidget()
        self.columns_layout = QHBoxLayout(self.columns_container)
        self.columns_layout.setContentsMargins(0, 0, 0, 0)
        self.columns_layout.setSpacing(1)
        layout.addWidget(self.columns_container)

    def showAt(self, pos: QPoint) -> None:
        self.move(pos)
        self.show()
        self.filter_input.setFocus()
        # Adjust if off-screen
        if screen := QApplication.screenAt(pos):
            r = screen.availableGeometry()
            g = self.geometry()
            if g.right() > r.right():
                self.move(r.right() - g.width(), g.y())
            if g.bottom() > r.bottom():
                self.move(g.x(), pos.y() - g.height())

    def focusOutEvent(self, event: QFocusEvent) -> None:
        if not (focused := QApplication.focusWidget()) or not self.isAncestorOf(focused):
            self.cancelled.emit()
            self.close()

    def _ensure_column(self, index: int) -> QListWidget:
        """Get or create column at index"""
        while index >= len(self.columns):
            col = QListWidget()
            col.setMinimumWidth(200)
            col.setMaximumWidth(250)
            col.setFixedHeight(350)
            col.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            col.setStyleSheet("""
                QListWidget { border: none; border-right: 1px solid #ddd; background: white; font-size: 13px; }
                QListWidget::item { padding: 6px 10px; }
                QListWidget::item:selected { background: #0078d4; color: white; }
                QListWidget::item:hover:!selected { background: #e5f3ff; }
            """)
            col_idx = len(self.columns)
            col.currentItemChanged.connect(lambda c, _, i=col_idx: self._on_selection(i, c))
            col.itemClicked.connect(self._on_click)
            self.columns.append(col)
            self.columns_layout.addWidget(col)
        return self.columns[index]

    def _update_size(self) -> None:
        visible = 0
        for col in self.columns:
            if col.count() > 0:
                col.show()
                visible += 1
            else:
                col.hide()
        self.setFixedWidth(min(900, max(1, visible) * 220 + 10))

    def _build_hierarchy(
        self, items: list[str], depth: int = 0, prefix_so_far: str = ""
    ) -> dict[str, Any]:
        """Build nested dict: key -> full_model_name (leaf) or nested_dict (group)

        For leaves, value is the ORIGINAL full model name (not stripped).
        prefix_so_far tracks what we've stripped to reconstruct full names.
        """
        # Prevent infinite recursion (shouldn't happen with proper logic, but safety first)
        if depth > 20 or len(items) <= self.MAX_ITEMS_BEFORE_SPLIT:
            # Items here are stripped - reconstruct full names
            return {item: prefix_so_far + item for item in items}

        groups = self._split_on_delimiter(items, self.PRIMARY_DELIMITER)
        if not groups:
            groups = self._find_best_split(items)
        if not groups:
            return {item: item for item in items}

        result: dict[str, Any] = {}
        for prefix, group_items in groups.items():
            exact = [i for i in group_items if i == prefix]
            rest = [i for i in group_items if i != prefix]

            if len(rest) == 0:
                # Only exact matches - they are full model names at this level
                for i in exact:
                    result[i] = prefix_so_far + i
            elif len(rest) == 1 and not exact:
                # Single item, use as leaf with full name
                result[rest[0]] = prefix_so_far + rest[0]
            else:
                # Strip prefix from items for recursion so we make progress
                # Find the actual prefix to strip (prefix + delimiter)
                strip_prefix = self._find_strip_prefix(prefix, rest)
                stripped_rest = [
                    item[len(strip_prefix) :] if item.startswith(strip_prefix) else item
                    for item in rest
                ]

                # Only recurse if we actually stripped something (making progress)
                if stripped_rest != rest:
                    # Pass the accumulated prefix so leaves can reconstruct full names
                    new_prefix = prefix_so_far + strip_prefix

                    # If there are exact matches with the same name as the group key,
                    # include them IN the children to avoid key collision
                    if exact:
                        # Add exact matches to the children with a special display name
                        children = self._build_hierarchy(stripped_rest, depth + 1, new_prefix)
                        for i in exact:
                            # Use the model name itself as key, showing it's the base model
                            display_key = "(base)"
                            children[display_key] = prefix_so_far + i
                        result[prefix] = children
                    else:
                        result[prefix] = self._build_hierarchy(stripped_rest, depth + 1, new_prefix)
                else:
                    # No progress possible, list as leaves with full names
                    for i in rest:
                        result[i] = prefix_so_far + i
                    # Exact matches alongside - no collision since we didn't create a group
                    for i in exact:
                        result[i] = prefix_so_far + i
        return result

    def _find_strip_prefix(self, key: str, items: list[str]) -> str:
        """Find the prefix to strip from items (key + delimiter)"""
        # Try key + primary delimiter first
        candidate = key + self.PRIMARY_DELIMITER
        if all(item.startswith(candidate) for item in items):
            return candidate

        # Try secondary delimiters
        for d in self.SECONDARY_DELIMITERS:
            candidate = key + d
            if all(item.startswith(candidate) for item in items):
                return candidate

        # Just the key itself
        if all(item.startswith(key) for item in items):
            return key

        return ""

    def _split_on_delimiter(self, items: list[str], delim: str) -> dict[str, list[str]] | None:
        # Find common prefix to strip before splitting (e.g., "gpt-" from all gpt models)
        strip_prefix = self._common_prefix_for_stripping(items)

        # Split by first segment after stripping common prefix
        groups: dict[str, list[str]] = {}
        for item in items:
            stripped = item[len(strip_prefix) :] if strip_prefix else item
            segment = stripped.split(delim)[0] if delim in stripped else stripped
            groups.setdefault(segment, []).append(item)

        if len(groups) <= 1 or len(groups) >= len(items) * 0.8:
            return None

        # Refine: find actual common prefix for each group and use as key
        refined: dict[str, list[str]] = {}
        for _, group_items in groups.items():
            key = self._group_key_from_items(group_items)
            if key in refined:
                refined[key].extend(group_items)
            else:
                refined[key] = list(group_items)

        return refined

    def _common_prefix_for_stripping(self, items: list[str]) -> str:
        """Find common prefix to strip, must end at a delimiter (inclusive)"""
        if not items or len(items) == 1:
            return ""

        # Find character-by-character common prefix
        prefix = items[0]
        for item in items[1:]:
            i = 0
            while i < len(prefix) and i < len(item) and prefix[i] == item[i]:
                i += 1
            prefix = prefix[:i]
            if not prefix:
                return ""

        # Must end at a delimiter - find the last one
        all_delims = [self.PRIMARY_DELIMITER] + self.SECONDARY_DELIMITERS
        last_pos = -1
        for d in all_delims:
            pos = prefix.rfind(d)
            if pos > last_pos:
                last_pos = pos

        if last_pos >= 0:
            return prefix[: last_pos + 1]  # Include the delimiter
        return ""

    def _group_key_from_items(self, items: list[str]) -> str:
        """Find the appropriate group key (longest common prefix) from items.

        Group keys should not cross the primary delimiter '/'. So if all items
        start with 'anthropic/claude-...', the group key is 'anthropic', not
        'anthropic/claude'.
        """
        if len(items) == 1:
            return items[0]

        # Find common prefix character by character
        prefix = items[0]
        for item in items[1:]:
            i = 0
            while i < len(prefix) and i < len(item) and prefix[i] == item[i]:
                i += 1
            prefix = prefix[:i]
            if not prefix:
                return items[0]

        # If prefix exactly matches an item, use it
        if prefix in items:
            return prefix

        # Truncate at first primary delimiter - don't cross '/' boundary
        primary_pos = prefix.find(self.PRIMARY_DELIMITER)
        if primary_pos > 0:
            return prefix[:primary_pos]

        # No primary delimiter, try secondary delimiters (use last one)
        last_pos = -1
        for d in self.SECONDARY_DELIMITERS:
            pos = prefix.rfind(d)
            if pos > last_pos:
                last_pos = pos

        if last_pos > 0:
            return prefix[:last_pos]
        return prefix

    def _find_best_split(self, items: list[str]) -> dict[str, list[str]] | None:
        best, best_cost = None, float("inf")
        for d in self.SECONDARY_DELIMITERS:
            if split := self._split_on_delimiter(items, d):
                cost = self._split_cost(split)
                if cost < best_cost:
                    best, best_cost = split, cost
        return best

    def _split_cost(self, groups: dict[str, list[str]]) -> float:
        cost = max(0, len(groups) - self.MAX_GROUPS) ** 2 * 10
        for items in groups.values():
            n = len(items)
            cost += 5 if n == 1 else 0
            cost += max(0, n - self.MAX_ITEMS_BEFORE_SPLIT) * 2
            cost += abs(n - self.IDEAL_GROUP_SIZE) * 0.5
        return cost

    def _populate_column(self, col_idx: int, data: dict[str, Any], parent_prefix: str) -> None:
        col = self._ensure_column(col_idx)
        col.blockSignals(True)
        col.clear()

        for key in sorted(data.keys()):
            value = data[key]
            item = QListWidgetItem()

            if isinstance(value, str):
                # Leaf - value IS the full model name (preserved during hierarchy building)
                full_name = value
                display = key  # Key is the stripped display name
                item.setText(display)
                item.setToolTip(full_name)
                item.setData(Qt.ItemDataRole.UserRole, ItemData(full_name, None, ""))
            else:
                # Group - key is the group name (may be stripped)
                item.setText(f"📁 {key}")
                # Child prefix is empty since hierarchy already has stripped keys
                item.setData(Qt.ItemDataRole.UserRole, ItemData(None, value, ""))

            col.addItem(item)

        col.blockSignals(False)
        for i in range(col_idx + 1, len(self.columns)):
            self.columns[i].clear()
        self._update_size()

    def _on_selection(self, col_idx: int, item: QListWidgetItem | None) -> None:
        if not item:
            return
        data: ItemData = item.data(Qt.ItemDataRole.UserRole)
        if data.children:
            self._populate_column(col_idx + 1, data.children, data.child_prefix)
            self.selected_model = None
        else:
            for i in range(col_idx + 1, len(self.columns)):
                self.columns[i].clear()
            self.selected_model = data.model_name
            self._update_size()

    def _on_click(self, item: QListWidgetItem) -> None:
        data: ItemData = item.data(Qt.ItemDataRole.UserRole)
        if data.model_name:
            self.selected_model = data.model_name
            self.model_selected.emit(data.model_name)
            self.close()

    def _navigate_to_selection(self) -> None:
        if not self.current_selection:
            return
        self._navigate(self._build_hierarchy(self.models, 0), self.current_selection, 0, "")

    def _navigate(self, data: dict[str, Any], target: str, col_idx: int, prefix: str) -> bool:
        col = self.columns[col_idx] if col_idx < len(self.columns) else None
        if not col:
            return False
        for i in range(col.count()):
            item = col.item(i)
            if not item:
                continue
            d: ItemData = item.data(Qt.ItemDataRole.UserRole)
            if d.model_name == target:
                col.setCurrentItem(item)
                self.selected_model = target
                return True
            if d.children and self._hierarchy_contains_model(d.children, target):
                col.setCurrentItem(item)
                self._populate_column(col_idx + 1, d.children, d.child_prefix)
                return self._navigate(d.children, target, col_idx + 1, d.child_prefix)
        return False

    def _hierarchy_contains_model(self, data: dict[str, Any], target: str) -> bool:
        """Check if hierarchy contains target model (handles stripped keys)"""
        for key, value in data.items():
            if isinstance(value, str):
                # Leaf - check if this stripped key matches end of target
                if target == value or target.endswith(key) or target.endswith("/" + key):
                    return True
            elif isinstance(value, dict):
                if self._hierarchy_contains_model(value, target):
                    return True
        return False

    def _apply_filter(self, text: str) -> None:
        text = text.lower().strip()
        filtered = [m for m in self.models if text in m.lower()] if text else self.models
        self._populate_column(0, self._build_hierarchy(filtered, 0), "")
        if not text:
            self._navigate_to_selection()

    def get_selected_model(self) -> str | None:
        return self.selected_model
