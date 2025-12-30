"""Branch list overlay widget for git graph."""

import pygit2
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from forge.git_backend.repository import ForgeRepository
from forge.ui.git_graph.types import LANE_COLORS, get_lane_color


class BranchItemWidget(QWidget):
    """Custom widget for a branch list item with delete button."""

    delete_clicked = Signal(str)  # branch_name

    def __init__(
        self,
        branch_name: str,
        color: QColor,
        is_current: bool,
        is_default: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.branch_name = branch_name
        self._is_current = is_current
        self._is_default = is_default

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        # Branch name label
        self._label = QLabel(branch_name)
        self._label.setStyleSheet(f"color: {color.darker(120).name()}; font-size: 11px;")
        layout.addWidget(self._label, 1)

        # Current branch indicator
        if is_current:
            current_label = QLabel("●")
            current_label.setStyleSheet("color: #4CAF50; font-size: 10px;")
            current_label.setToolTip("Current branch")
            layout.addWidget(current_label)

        # Delete button (hidden for default and current branches)
        self._delete_btn = QPushButton("×")
        self._delete_btn.setFixedSize(16, 16)
        self._delete_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: #999;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                color: #F44336;
                background: #FFEBEE;
                border-radius: 8px;
            }
        """)
        self._delete_btn.setToolTip("Delete branch")
        self._delete_btn.clicked.connect(lambda: self.delete_clicked.emit(self.branch_name))

        # Hide delete for current or default branch
        if is_current or is_default:
            self._delete_btn.hide()

        layout.addWidget(self._delete_btn)


class BranchListWidget(QWidget):
    """Overlay widget listing branches for quick navigation."""

    branch_clicked = Signal(str)  # branch name
    branch_deleted = Signal(str)  # branch name - emitted after deletion

    def __init__(self, repo: ForgeRepository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repo = repo
        self._default_branch: str | None = None
        self._current_branch: str | None = None

        # Semi-transparent background
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(self.backgroundRole(), QColor(255, 255, 255, 230))
        self.setPalette(palette)

        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)

        # Branch list
        self._list = QListWidget()
        self._list.setStyleSheet("""
            QListWidget {
                border: 1px solid #ddd;
                border-radius: 4px;
                background: transparent;
                font-size: 11px;
            }
            QListWidget::item {
                padding: 0px;
                border-radius: 3px;
            }
            QListWidget::item:hover {
                background: #E3F2FD;
            }
            QListWidget::item:selected {
                background: #2196F3;
            }
        """)
        self._list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._list)

        self._load_branches()
        self.setFixedWidth(180)
        self.adjustSize()

    def _load_branches(self) -> None:
        """Load branches into the list, ordered by last commit time (newest first)."""
        self._list.clear()

        # Get default and current branch
        try:
            self._default_branch = self.repo.get_default_branch()
        except ValueError:
            self._default_branch = None
        self._current_branch = self.repo.get_checked_out_branch()

        # Get local branch names with their tip commit times (skip remotes)
        branch_times: list[tuple[str, int]] = []
        for branch_name in self.repo.repo.branches.local:
            branch = self.repo.repo.branches[branch_name]
            commit = branch.peel(pygit2.Commit)
            branch_times.append((branch_name, commit.commit_time))

        # Sort by commit time descending (newest first)
        branch_times.sort(key=lambda x: -x[1])

        for branch_name, _ in branch_times:
            # Color code by lane
            color = get_lane_color(hash(branch_name) % len(LANE_COLORS))

            # Create custom widget for item
            is_current = branch_name == self._current_branch
            is_default = branch_name == self._default_branch
            item_widget = BranchItemWidget(branch_name, color, is_current, is_default)
            item_widget.delete_clicked.connect(self._on_delete_clicked)

            # Create list item and set widget
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, branch_name)
            item.setSizeHint(item_widget.sizeHint())
            self._list.addItem(item)
            self._list.setItemWidget(item, item_widget)

        # Adjust height to fit all items up to 10 (no scrolling unless > 10)
        item_height = 28
        visible_items = min(self._list.count(), 10)
        list_height = max(visible_items * item_height + 10, 50)
        self._list.setFixedHeight(list_height)
        self.setFixedHeight(list_height + 12)  # Account for margins

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        """Handle branch click."""
        branch_name = item.data(Qt.ItemDataRole.UserRole)
        if branch_name:
            self.branch_clicked.emit(branch_name)

    def _on_delete_clicked(self, branch_name: str) -> None:
        """Handle delete button click - show confirmation dialog."""
        # Safety dialog
        result = QMessageBox.question(
            self,
            "Delete Branch",
            f"Are you sure you want to delete branch '{branch_name}'?\n\n"
            "This action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if result != QMessageBox.StandardButton.Yes:
            return

        try:
            self.repo.delete_branch(branch_name)
            self.branch_deleted.emit(branch_name)  # Signal triggers full refresh
        except ValueError as e:
            QMessageBox.warning(self, "Cannot Delete Branch", str(e))
