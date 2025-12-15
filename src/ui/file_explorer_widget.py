"""
File explorer widget that reads from VFS (git tree)
"""

from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from .branch_workspace import BranchWorkspace


class FileExplorerWidget(QWidget):
    """
    File explorer that shows files from VFS (git tree).

    Displays the branch's files in a tree structure.
    Double-clicking a file emits a signal to open it.
    Shows an eye icon (👁) for files in AI context.
    """

    # Emitted when user wants to open a file
    file_open_requested = Signal(str)  # filepath

    # Emitted when user toggles a file's context status
    context_toggle_requested = Signal(str, bool)  # filepath, add_to_context

    def __init__(self, workspace: "BranchWorkspace", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self._context_files: set[str] = set()  # Files currently in AI context
        self._setup_ui()
        self.refresh()

    def _setup_ui(self) -> None:
        """Setup the tree widget"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.tree.itemClicked.connect(self._on_item_clicked)

        # Add tooltip explaining the eye icon
        self.tree.setToolTip("Double-click to open file\nClick 👁 to toggle AI context")

        layout.addWidget(self.tree)

    def refresh(self) -> None:
        """Refresh the file tree from VFS"""
        self.tree.clear()

        # Get all files from VFS
        files = self.workspace.vfs.list_files()

        # Build tree structure
        root_items: dict[str, QTreeWidgetItem] = {}

        for filepath in sorted(files):
            # Skip .forge directory (internal)
            if filepath.startswith(".forge/"):
                continue

            path = PurePosixPath(filepath)
            parts = path.parts

            # Create/get parent items
            current_parent: QTreeWidgetItem | QTreeWidget = self.tree
            current_path = ""

            for part in parts[:-1]:  # All but the filename
                current_path = str(PurePosixPath(current_path) / part) if current_path else part

                if current_path not in root_items:
                    item = QTreeWidgetItem()
                    item.setText(0, f"📁 {part}")
                    item.setData(0, Qt.ItemDataRole.UserRole, current_path)  # Store path in data
                    item.setData(0, Qt.ItemDataRole.UserRole + 1, "dir")  # Mark as directory

                    if isinstance(current_parent, QTreeWidget):
                        current_parent.addTopLevelItem(item)
                    else:
                        current_parent.addChild(item)

                    root_items[current_path] = item

                current_parent = root_items[current_path]

            # Add the file itself
            filename = parts[-1]
            file_item = QTreeWidgetItem()
            # Show eye icon if file is in context
            if filepath in self._context_files:
                file_item.setText(0, f"👁 📄 {filename}")
            else:
                file_item.setText(0, f"    📄 {filename}")
            file_item.setData(0, Qt.ItemDataRole.UserRole, filepath)  # Store full path
            file_item.setData(0, Qt.ItemDataRole.UserRole + 1, "file")  # Mark as file
            file_item.setToolTip(0, f"{filepath}\nClick 👁 area to toggle AI context")

            if isinstance(current_parent, QTreeWidget):
                current_parent.addTopLevelItem(file_item)
            else:
                current_parent.addChild(file_item)

        # Expand top-level directories by default
        for i in range(self.tree.topLevelItemCount()):
            top_item = self.tree.topLevelItem(i)
            if top_item is not None and top_item.data(0, Qt.ItemDataRole.UserRole + 1) == "dir":
                top_item.setExpanded(True)

    def set_context_files(self, context_files: set[str]) -> None:
        """Update which files are shown as being in AI context"""
        self._context_files = context_files.copy()
        self._update_context_icons()

    def _update_context_icons(self) -> None:
        """Update the eye icons on all file items"""
        self._update_context_icons_recursive(None)

    def _update_context_icons_recursive(self, parent: QTreeWidgetItem | None) -> None:
        """Recursively update context icons"""
        if parent is None:
            # Top level items
            for i in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(i)
                if item:
                    self._update_item_icon(item)
                    self._update_context_icons_recursive(item)
        else:
            # Child items
            for i in range(parent.childCount()):
                child = parent.child(i)
                if child is not None:
                    self._update_item_icon(child)
                    self._update_context_icons_recursive(child)

    def _update_item_icon(self, item: QTreeWidgetItem) -> None:
        """Update a single item's icon based on context status"""
        item_type = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if item_type != "file":
            return

        filepath = item.data(0, Qt.ItemDataRole.UserRole)
        text = item.text(0)

        # Extract just the filename (remove any existing icons)
        # The format is either "👁 📄 filename" or "    📄 filename"
        filename = text.split("📄")[-1].strip() if "📄" in text else text.strip()

        # Set new text with appropriate icon
        if filepath in self._context_files:
            item.setText(0, f"👁 📄 {filename}")
        else:
            item.setText(0, f"    📄 {filename}")

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Handle single click on item - check if clicking on eye icon area"""
        item_type = item.data(0, Qt.ItemDataRole.UserRole + 1)

        if item_type == "file":
            filepath = item.data(0, Qt.ItemDataRole.UserRole)
            # Toggle context when clicking (single click toggles context)
            # The eye icon is at the start, so any click on the item toggles
            is_in_context = filepath in self._context_files
            self.context_toggle_requested.emit(filepath, not is_in_context)

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Handle double-click on item"""
        item_type = item.data(0, Qt.ItemDataRole.UserRole + 1)

        if item_type == "file":
            filepath = item.data(0, Qt.ItemDataRole.UserRole)
            self.file_open_requested.emit(filepath)
        elif item_type == "dir":
            # Toggle expansion
            item.setExpanded(not item.isExpanded())
