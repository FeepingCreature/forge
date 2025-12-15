"""
File explorer widget that reads from VFS (git tree)
"""

from enum import Enum
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


class ContextState(Enum):
    """Context state for files and folders"""

    NONE = "none"  # Not in context (grey eye)
    PARTIAL = "partial"  # Some children in context (half eye) - folders only
    FULL = "full"  # Fully in context (black eye)


# Eye icons for different states
ICON_NONE = "◯"  # Empty circle - not in context
ICON_PARTIAL = "◐"  # Half-filled circle - partial context
ICON_FULL = "●"  # Filled circle - full context


class FileExplorerWidget(QWidget):
    """
    File explorer that shows files from VFS (git tree).

    Displays the branch's files in a tree structure.
    Double-clicking a file emits a signal to open it.
    Shows context icons:
    - ◯ (empty) for files/folders not in context
    - ◐ (half) for folders with some files in context
    - ● (full) for files in context or folders fully in context
    """

    # Emitted when user wants to open a file
    file_open_requested = Signal(str)  # filepath

    # Emitted when user toggles a file's context status
    context_toggle_requested = Signal(str, bool)  # filepath, add_to_context

    def __init__(self, workspace: "BranchWorkspace", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self._context_files: set[str] = set()  # Files currently in AI context
        self._all_files: list[str] = []  # All files in the VFS (cached for context calculations)
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

        # Add tooltip explaining the context icons
        self.tree.setToolTip("Double-click to open file\nSingle-click to toggle AI context\n\n◯ = not in context\n◐ = some files in context\n● = in context")

        layout.addWidget(self.tree)

    def refresh(self) -> None:
        """Refresh the file tree from VFS"""
        self.tree.clear()

        # Get all files from VFS and cache them
        files = self.workspace.vfs.list_files()
        self._all_files = [f for f in files if not f.startswith(".forge/")]

        # Build tree structure
        root_items: dict[str, QTreeWidgetItem] = {}

        for filepath in sorted(self._all_files):
            path = PurePosixPath(filepath)
            parts = path.parts

            # Create/get parent items
            current_parent: QTreeWidgetItem | QTreeWidget = self.tree
            current_path = ""

            for part in parts[:-1]:  # All but the filename
                current_path = str(PurePosixPath(current_path) / part) if current_path else part

                if current_path not in root_items:
                    item = QTreeWidgetItem()
                    # Initial icon will be set by _update_all_context_icons
                    item.setText(0, f"{ICON_NONE} 📁 {part}")
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
            # Initial icon will be set by _update_all_context_icons
            file_item.setText(0, f"{ICON_NONE} 📄 {filename}")
            file_item.setData(0, Qt.ItemDataRole.UserRole, filepath)  # Store full path
            file_item.setData(0, Qt.ItemDataRole.UserRole + 1, "file")  # Mark as file
            file_item.setToolTip(0, f"{filepath}\nClick to toggle AI context")

            if isinstance(current_parent, QTreeWidget):
                current_parent.addTopLevelItem(file_item)
            else:
                current_parent.addChild(file_item)

        # Expand top-level directories by default
        for i in range(self.tree.topLevelItemCount()):
            top_item = self.tree.topLevelItem(i)
            if top_item is not None and top_item.data(0, Qt.ItemDataRole.UserRole + 1) == "dir":
                top_item.setExpanded(True)

        # Update all context icons
        self._update_all_context_icons()

    def set_context_files(self, context_files: set[str]) -> None:
        """Update which files are shown as being in AI context"""
        self._context_files = context_files.copy()
        self._update_all_context_icons()

    def _update_all_context_icons(self) -> None:
        """Update context icons on all items (files and folders)"""
        # Update from bottom up so folders can calculate state from children
        self._update_icons_recursive(None)

    def _update_icons_recursive(self, parent: QTreeWidgetItem | None) -> ContextState:
        """
        Recursively update context icons, returning the aggregate state.

        For files: FULL if in context, NONE otherwise
        For folders: FULL if all children FULL, PARTIAL if some, NONE if none
        """
        if parent is None:
            # Process top level items
            for i in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(i)
                if item:
                    self._update_icons_recursive(item)
            return ContextState.NONE  # Return value not used at top level

        item_type = parent.data(0, Qt.ItemDataRole.UserRole + 1)

        if item_type == "file":
            # File: simple in-context check
            filepath = parent.data(0, Qt.ItemDataRole.UserRole)
            state = ContextState.FULL if filepath in self._context_files else ContextState.NONE
            self._set_item_icon(parent, state)
            return state

        elif item_type == "dir":
            # Folder: aggregate children states
            child_states: list[ContextState] = []

            for i in range(parent.childCount()):
                child = parent.child(i)
                if child is not None:
                    child_state = self._update_icons_recursive(child)
                    child_states.append(child_state)

            # Calculate folder state
            if not child_states:
                state = ContextState.NONE
            elif all(s == ContextState.FULL for s in child_states):
                state = ContextState.FULL
            elif all(s == ContextState.NONE for s in child_states):
                state = ContextState.NONE
            else:
                state = ContextState.PARTIAL

            self._set_item_icon(parent, state)
            return state

        return ContextState.NONE

    def _set_item_icon(self, item: QTreeWidgetItem, state: ContextState) -> None:
        """Set the context icon for an item based on state"""
        item_type = item.data(0, Qt.ItemDataRole.UserRole + 1)
        text = item.text(0)

        # Extract the name (remove existing icon prefix)
        if item_type == "file":
            # Format: "ICON 📄 filename"
            name = text.split("📄")[-1].strip() if "📄" in text else text.strip()
            icon = {
                ContextState.NONE: ICON_NONE,
                ContextState.PARTIAL: ICON_PARTIAL,  # Shouldn't happen for files
                ContextState.FULL: ICON_FULL,
            }[state]
            item.setText(0, f"{icon} 📄 {name}")
        else:
            # Format: "ICON 📁 foldername"
            name = text.split("📁")[-1].strip() if "📁" in text else text.strip()
            icon = {
                ContextState.NONE: ICON_NONE,
                ContextState.PARTIAL: ICON_PARTIAL,
                ContextState.FULL: ICON_FULL,
            }[state]
            item.setText(0, f"{icon} 📁 {name}")

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Handle single click on item - toggle context"""
        item_type = item.data(0, Qt.ItemDataRole.UserRole + 1)

        if item_type == "file":
            filepath = item.data(0, Qt.ItemDataRole.UserRole)
            # Toggle context for file
            is_in_context = filepath in self._context_files
            self.context_toggle_requested.emit(filepath, not is_in_context)

        elif item_type == "dir":
            # Toggle context for all files in this folder
            folder_path = item.data(0, Qt.ItemDataRole.UserRole)
            folder_files = [f for f in self._all_files if f.startswith(folder_path + "/")]

            if not folder_files:
                return

            # If any file in folder is NOT in context, add all; otherwise remove all
            all_in_context = all(f in self._context_files for f in folder_files)

            for filepath in folder_files:
                self.context_toggle_requested.emit(filepath, not all_in_context)

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Handle double-click on item"""
        item_type = item.data(0, Qt.ItemDataRole.UserRole + 1)

        if item_type == "file":
            filepath = item.data(0, Qt.ItemDataRole.UserRole)
            self.file_open_requested.emit(filepath)
        elif item_type == "dir":
            # Toggle expansion
            item.setExpanded(not item.isExpanded())
