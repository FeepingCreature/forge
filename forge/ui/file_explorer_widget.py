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
    from forge.ui.branch_workspace import BranchWorkspace


class ContextState(Enum):
    """Context state for files and folders"""

    NONE = "none"  # Not in context (grey eye)
    PARTIAL = "partial"  # Some children in context (half eye) - folders only
    FULL = "full"  # Fully in context (black eye)


# Eye icons for different states
ICON_NONE = "◯"  # Empty circle - not in context
ICON_PARTIAL = "◐"  # Half-filled circle - partial context
ICON_FULL = "●"  # Filled circle - full context
ICON_WARNING = "⚠️"  # Warning icon for large files

# Threshold for "large file" warning (in characters)
LARGE_FILE_THRESHOLD = 10000

# Column indices
COL_NAME = 0
COL_CONTEXT = 1


class FileExplorerWidget(QWidget):
    """
    File explorer that shows files from VFS (git tree).

    Displays the branch's files in a tree structure.
    Double-clicking a file emits a signal to open it.
    Shows context icons:
    - ◯ (empty) for files/folders not in context
    - ◐ (half) for folders with some files in context
    - ● (full) for files in context or folders fully in context
    - ⚠️ (warning) shown next to large files (10k+ chars) in context
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
        self._large_files: set[str] = set()  # Files that are large (10k+ chars)
        self._file_sizes: dict[str, int] = {}  # File sizes in characters
        self._root_item: QTreeWidgetItem | None = None  # The <root> item
        self._setup_ui()
        self.refresh()

    def _setup_ui(self) -> None:
        """Setup the tree widget"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.tree.itemClicked.connect(self._on_item_clicked)

        # Make the context column narrow
        self.tree.setColumnWidth(COL_CONTEXT, 24)
        # Stretch the name column
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(COL_NAME, self.tree.header().ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(COL_CONTEXT, self.tree.header().ResizeMode.Fixed)

        # Add tooltip explaining the context icons
        self.tree.setToolTip(
            "Double-click to open file\nClick ◯/● to toggle AI context\n\n◯ = not in context\n◐ = some files in context\n● = in context\n⚠️ = large file (10k+ chars)"
        )

        layout.addWidget(self.tree)

    def refresh(self) -> None:
        """Refresh the file tree from VFS"""
        self.tree.clear()
        self._large_files.clear()
        self._file_sizes.clear()

        # Get all files from VFS and cache them
        files = self.workspace.vfs.list_files()
        self._all_files = [f for f in files if not f.startswith(".forge/")]

        # Check file sizes and track large files
        for filepath in self._all_files:
            try:
                content = self.workspace.vfs.read_file(filepath)
                self._file_sizes[filepath] = len(content)
                if len(content) >= LARGE_FILE_THRESHOLD:
                    self._large_files.add(filepath)
            except Exception:
                pass  # Skip files we can't read

        # Create the root item that contains everything
        self._root_item = QTreeWidgetItem()
        self._root_item.setText(COL_NAME, "📦 <root>")
        self._root_item.setText(COL_CONTEXT, ICON_NONE)
        self._root_item.setData(COL_NAME, Qt.ItemDataRole.UserRole, "")  # Empty path = root
        self._root_item.setData(COL_NAME, Qt.ItemDataRole.UserRole + 1, "root")  # Mark as root
        self._root_item.setToolTip(COL_NAME, "All files in repository")
        self._root_item.setToolTip(COL_CONTEXT, "Click to toggle all files in/out of context")
        self.tree.addTopLevelItem(self._root_item)

        # Build tree structure under root
        root_items: dict[str, QTreeWidgetItem] = {}

        for filepath in sorted(self._all_files):
            path = PurePosixPath(filepath)
            parts = path.parts

            # Create/get parent items
            current_parent: QTreeWidgetItem = self._root_item
            current_path = ""

            for part in parts[:-1]:  # All but the filename
                current_path = str(PurePosixPath(current_path) / part) if current_path else part

                if current_path not in root_items:
                    item = QTreeWidgetItem()
                    item.setText(COL_NAME, f"📁 {part}")
                    item.setText(COL_CONTEXT, ICON_NONE)  # Context icon in separate column
                    item.setData(COL_NAME, Qt.ItemDataRole.UserRole, current_path)  # Store path
                    item.setData(COL_NAME, Qt.ItemDataRole.UserRole + 1, "dir")  # Mark as directory

                    current_parent.addChild(item)
                    root_items[current_path] = item

                current_parent = root_items[current_path]

            # Add the file itself
            filename = parts[-1]
            file_item = QTreeWidgetItem()
            file_item.setText(COL_NAME, f"📄 {filename}")
            file_item.setText(COL_CONTEXT, ICON_NONE)  # Context icon in separate column
            file_item.setData(COL_NAME, Qt.ItemDataRole.UserRole, filepath)  # Store full path
            file_item.setData(COL_NAME, Qt.ItemDataRole.UserRole + 1, "file")  # Mark as file

            # Build tooltip with file path and size info
            tooltip_parts = [filepath]
            if filepath in self._file_sizes:
                size = self._file_sizes[filepath]
                tokens = size // 3  # Rough estimate
                tooltip_parts.append(f"Size: {size:,} chars (~{tokens:,} tokens)")
                if filepath in self._large_files:
                    tooltip_parts.append("⚠️ Large file - may consume significant context")
            file_item.setToolTip(COL_NAME, "\n".join(tooltip_parts))
            file_item.setToolTip(COL_CONTEXT, "Click to toggle AI context")

            current_parent.addChild(file_item)

        # Expand root and top-level directories by default
        self._root_item.setExpanded(True)
        for i in range(self._root_item.childCount()):
            child = self._root_item.child(i)
            if child is not None and child.data(0, Qt.ItemDataRole.UserRole + 1) == "dir":
                child.setExpanded(True)

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

    def _update_icons_recursive(self, parent: QTreeWidgetItem | None) -> tuple[ContextState, bool]:
        """
        Recursively update context icons, returning the aggregate state and whether any child has warning.

        For files: FULL if in context, NONE otherwise
        For folders/root: FULL if all children FULL, PARTIAL if some, NONE if none

        Returns:
            Tuple of (context_state, has_large_file_warning)
        """
        if parent is None:
            # Process top level items
            for i in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(i)
                if item:
                    self._update_icons_recursive(item)
            return ContextState.NONE, False  # Return value not used at top level

        item_type = parent.data(0, Qt.ItemDataRole.UserRole + 1)

        if item_type == "file":
            # File: simple in-context check
            filepath = parent.data(0, Qt.ItemDataRole.UserRole)
            state = ContextState.FULL if filepath in self._context_files else ContextState.NONE
            is_large = filepath in self._large_files
            has_warning = is_large and state == ContextState.FULL
            self._set_item_icon(parent, state, is_large=is_large)
            return state, has_warning

        elif item_type in ("dir", "root"):
            # Folder/root: aggregate children states
            child_states: list[ContextState] = []
            any_child_has_warning = False

            for i in range(parent.childCount()):
                child = parent.child(i)
                if child is not None:
                    child_state, child_warning = self._update_icons_recursive(child)
                    child_states.append(child_state)
                    if child_warning:
                        any_child_has_warning = True

            # Calculate folder state
            if not child_states:
                state = ContextState.NONE
            elif all(s == ContextState.FULL for s in child_states):
                state = ContextState.FULL
            elif all(s == ContextState.NONE for s in child_states):
                state = ContextState.NONE
            else:
                state = ContextState.PARTIAL

            # Show warning on folder if any child has a large file in context
            self._set_item_icon(parent, state, is_large=any_child_has_warning)
            return state, any_child_has_warning

        return ContextState.NONE, False

    def _set_item_icon(
        self, item: QTreeWidgetItem, state: ContextState, is_large: bool = False
    ) -> None:
        """Set the context icon for an item based on state"""
        icon = {
            ContextState.NONE: ICON_NONE,
            ContextState.PARTIAL: ICON_PARTIAL,
            ContextState.FULL: ICON_FULL,
        }[state]
        # Add warning icon for large files in context (for files: FULL only, for folders: FULL or PARTIAL)
        if is_large and state != ContextState.NONE:
            icon = f"{icon}{ICON_WARNING}"
        item.setText(COL_CONTEXT, icon)

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Handle single click on item - toggle context only if clicking the icon column"""
        # Only toggle context when clicking the context icon column
        if column != COL_CONTEXT:
            return

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

        elif item_type == "root":
            # Toggle context for ALL files
            if not self._all_files:
                return

            # If any file is NOT in context, add all; otherwise remove all
            all_in_context = all(f in self._context_files for f in self._all_files)

            for filepath in self._all_files:
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
