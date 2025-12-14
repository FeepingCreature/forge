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
    """
    
    # Emitted when user wants to open a file
    file_open_requested = Signal(str)  # filepath
    
    def __init__(self, workspace: "BranchWorkspace", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self._setup_ui()
        self.refresh()
    
    def _setup_ui(self) -> None:
        """Setup the tree widget"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        
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
            
            for i, part in enumerate(parts[:-1]):  # All but the filename
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
            file_item.setText(0, f"📄 {filename}")
            file_item.setData(0, Qt.ItemDataRole.UserRole, filepath)  # Store full path
            file_item.setData(0, Qt.ItemDataRole.UserRole + 1, "file")  # Mark as file
            
            if isinstance(current_parent, QTreeWidget):
                current_parent.addTopLevelItem(file_item)
            else:
                current_parent.addChild(file_item)
        
        # Expand top-level directories by default
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item and item.data(0, Qt.ItemDataRole.UserRole + 1) == "dir":
                item.setExpanded(True)
    
    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Handle double-click on item"""
        item_type = item.data(0, Qt.ItemDataRole.UserRole + 1)
        
        if item_type == "file":
            filepath = item.data(0, Qt.ItemDataRole.UserRole)
            self.file_open_requested.emit(filepath)
        elif item_type == "dir":
            # Toggle expansion
            item.setExpanded(not item.isExpanded())
