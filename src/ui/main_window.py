"""
Main window for Forge IDE - Branch-first architecture
"""

import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFileDialog,
    QInputDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStatusBar,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config.settings import Settings
from ..git_backend.commit_types import CommitType
from ..git_backend.repository import ForgeRepository
from .ai_chat_widget import AIChatWidget
from .branch_tab_widget import BranchTabWidget
from .branch_workspace import BranchWorkspace
from .settings_dialog import SettingsDialog
from .welcome_widget import WelcomeWidget


class MainWindow(QMainWindow):
    """Main application window with branch-first architecture"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Forge")
        self.setGeometry(100, 100, 1400, 900)

        # Initialize settings
        self.settings = Settings()

        # Initialize git repository (required - Forge only works in git repos)
        self.repo = ForgeRepository()
        self.sessions_dir = Path(self.repo.repo.workdir) / ".forge" / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        # Track branch workspaces
        self._workspaces: dict[str, BranchWorkspace] = {}
        self._branch_widgets: dict[str, BranchTabWidget] = {}

        self._setup_ui()
        self._setup_menus()
        self._open_default_branch()

    def _setup_ui(self) -> None:
        """Setup the main UI layout with branch tabs"""
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        # Branch tabs (top-level)
        self.branch_tabs = QTabWidget()
        self.branch_tabs.setTabsClosable(True)
        self.branch_tabs.tabCloseRequested.connect(self._close_branch_tab)
        self.branch_tabs.setMovable(True)
        self.branch_tabs.currentChanged.connect(self._on_branch_tab_changed)
        
        # Add "+" button for new branch
        self.branch_tabs.setCornerWidget(self._create_new_branch_button(), Qt.Corner.TopRightCorner)
        
        # Enable context menu on tab bar
        tab_bar = self.branch_tabs.tabBar()
        tab_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tab_bar.customContextMenuRequested.connect(self._show_branch_context_menu)

        layout.addWidget(self.branch_tabs)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

    def _create_new_branch_button(self) -> QWidget:
        """Create the '+' button for new branches"""
        from PySide6.QtWidgets import QPushButton
        
        btn = QPushButton("+")
        btn.setFixedSize(24, 24)
        btn.setToolTip("New branch...")
        btn.clicked.connect(self._show_new_branch_dialog)
        return btn

    def _open_default_branch(self) -> None:
        """Open the default branch (usually main or master)"""
        # Try to get current branch
        try:
            current_branch = self.repo.repo.head.shorthand
            self._open_branch(current_branch)
        except Exception:
            # Fallback to 'main' or first available branch
            branches = list(self.repo.repo.branches)
            if branches:
                self._open_branch(branches[0])
            else:
                self.status_bar.showMessage("No branches found")

    def _open_branch(self, branch_name: str) -> None:
        """Open a branch in a new tab (or focus existing)"""
        # Check if already open
        if branch_name in self._branch_widgets:
            # Find and focus the tab
            for i in range(self.branch_tabs.count()):
                if self.branch_tabs.tabText(i).replace("🤖 ", "").replace("🌿 ", "") == branch_name:
                    self.branch_tabs.setCurrentIndex(i)
                    return
        
        # Create workspace and widget
        workspace = BranchWorkspace(branch_name=branch_name, repo=self.repo)
        branch_widget = BranchTabWidget(workspace, self.settings)
        
        # Create AI chat for this branch
        is_session = branch_name.startswith("forge/session/")
        if is_session:
            session_id = branch_name.replace("forge/session/", "")
            # Try to load existing session data
            session_data = self._load_session_data(session_id, branch_name)
            chat_widget = AIChatWidget(
                session_id=session_id,
                session_data=session_data,
                settings=self.settings,
                repo=self.repo,
                branch_name=branch_name,
            )
        else:
            # Non-session branch gets a fresh AI chat that operates on this branch
            chat_widget = AIChatWidget(
                session_id=str(uuid.uuid4()),
                settings=self.settings,
                repo=self.repo,
                branch_name=branch_name,
            )
        
        # Add AI chat as first tab
        branch_widget.add_ai_chat_tab(chat_widget)
        
        # Connect signals
        branch_widget.file_saved.connect(self._on_file_saved)
        
        # Store references
        self._workspaces[branch_name] = workspace
        self._branch_widgets[branch_name] = branch_widget
        
        # Add to tab widget
        icon = "🤖" if is_session else "🌿"
        display_name = workspace.display_name if is_session else branch_name
        index = self.branch_tabs.addTab(branch_widget, f"{icon} {display_name}")
        self.branch_tabs.setCurrentIndex(index)
        
        self.status_bar.showMessage(f"Opened branch: {branch_name}")

    def _load_session_data(self, session_id: str, branch_name: str) -> dict[str, Any] | None:
        """Load session data from git if it exists"""
        try:
            session_file = f".forge/sessions/{session_id}.json"
            content = self.repo.get_file_content(session_file, branch_name)
            return json.loads(content)
        except (FileNotFoundError, KeyError):
            return None

    def _on_file_saved(self, filepath: str, commit_oid: str) -> None:
        """Handle file save notification"""
        self.status_bar.showMessage(f"Saved {filepath} → {commit_oid[:8]}")

    def _on_branch_tab_changed(self, index: int) -> None:
        """Handle branch tab switch"""
        if index < 0:
            return
        
        # Update status bar with current branch
        tab_text = self.branch_tabs.tabText(index)
        # Strip emoji prefix
        branch_display = tab_text.replace("🤖 ", "").replace("🌿 ", "")
        self.status_bar.showMessage(f"Branch: {branch_display}")

    def _show_branch_context_menu(self, pos: Any) -> None:
        """Show context menu for branch tab"""
        tab_bar = self.branch_tabs.tabBar()
        index = tab_bar.tabAt(pos)
        if index < 0:
            return
        
        menu = QMenu(self)
        
        # Get branch name from tab
        tab_text = self.branch_tabs.tabText(index)
        branch_name = self._get_branch_name_from_tab(index)
        
        # Close action
        close_action = menu.addAction("Close")
        close_action.triggered.connect(lambda: self._close_branch_tab(index))
        
        menu.addSeparator()
        
        # Fork action
        fork_action = menu.addAction("Fork branch...")
        fork_action.triggered.connect(lambda: self._fork_branch(branch_name))
        
        # Delete action (only for session branches, with confirmation)
        if branch_name.startswith("forge/session/"):
            menu.addSeparator()
            delete_action = menu.addAction("Delete branch...")
            delete_action.triggered.connect(lambda: self._delete_branch(branch_name, index))
        
        menu.exec(tab_bar.mapToGlobal(pos))

    def _get_branch_name_from_tab(self, index: int) -> str:
        """Get the actual branch name from a tab index"""
        # Find the branch name by looking up in our widgets dict
        widget = self.branch_tabs.widget(index)
        for name, w in self._branch_widgets.items():
            if w == widget:
                return name
        return ""

    def _show_new_branch_dialog(self) -> None:
        """Show dialog to create a new branch"""
        menu = QMenu(self)
        
        # New AI Session
        session_action = menu.addAction("🤖 New AI Session")
        session_action.triggered.connect(self._new_ai_session)
        
        # New feature branch
        feature_action = menu.addAction("🌿 New Branch...")
        feature_action.triggered.connect(self._new_feature_branch)
        
        # Show menu at button position
        btn = self.sender()
        if btn:
            menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _new_feature_branch(self) -> None:
        """Create a new feature branch"""
        name, ok = QInputDialog.getText(
            self,
            "New Branch",
            "Branch name:",
            text="feature/"
        )
        if ok and name:
            # Create branch from current HEAD
            try:
                head = self.repo.repo.head
                commit = head.peel()
                self.repo.repo.branches.create(name, commit)
                self._open_branch(name)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to create branch: {e}")

    def _fork_branch(self, source_branch: str) -> None:
        """Fork an existing branch"""
        name, ok = QInputDialog.getText(
            self,
            "Fork Branch",
            f"New branch name (forking from {source_branch}):",
            text=f"{source_branch}-fork"
        )
        if ok and name:
            try:
                # Get source branch head
                source_commit = self.repo.get_branch_head(source_branch)
                self.repo.repo.branches.create(name, source_commit)
                self._open_branch(name)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to fork branch: {e}")

    def _delete_branch(self, branch_name: str, tab_index: int) -> None:
        """Delete a branch (with confirmation)"""
        reply = QMessageBox.warning(
            self,
            "Delete Branch",
            f"Are you sure you want to delete branch '{branch_name}'?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                # Close the tab first
                self._close_branch_tab(tab_index)
                
                # Delete the branch
                branch = self.repo.repo.branches[branch_name]
                branch.delete()
                
                self.status_bar.showMessage(f"Deleted branch: {branch_name}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete branch: {e}")

    def _open_file_by_path(self, filepath: str) -> None:
        """Open a file in the current branch's workspace"""
        # Get current branch widget
        current_widget = self.branch_tabs.currentWidget()
        if isinstance(current_widget, BranchTabWidget):
            current_widget.open_file(filepath)
            self.status_bar.showMessage(f"Opened: {filepath}")
        else:
            self.status_bar.showMessage("No branch open to view file")

    def _setup_menus(self) -> None:
        """Setup menu bar"""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&File")
        file_menu.addAction("&Open File...", self._open_file)
        file_menu.addAction("&Save", self._save_current_file).setShortcut("Ctrl+S")
        file_menu.addAction("Save &All", self._save_all_files).setShortcut("Ctrl+Shift+S")
        file_menu.addSeparator()
        file_menu.addAction("&Settings...", self._open_settings)
        file_menu.addSeparator()
        file_menu.addAction("E&xit", self.close)

        # Edit menu
        edit_menu = menubar.addMenu("&Edit")
        edit_menu.addAction("&Undo")
        edit_menu.addAction("&Redo")

        # Branch menu
        branch_menu = menubar.addMenu("&Branch")
        branch_menu.addAction("&New AI Session", self._new_ai_session)
        branch_menu.addAction("New &Branch...", self._new_feature_branch)
        branch_menu.addSeparator()
        
        # Add existing branches submenu
        self._branches_submenu = branch_menu.addMenu("Open Branch")
        self._populate_branches_menu()

        # Git menu
        git_menu = menubar.addMenu("&Git")
        git_menu.addAction("View Branches")
        git_menu.addAction("Commit History")

    def _open_file(self) -> None:
        """Open a file in the current branch"""
        # Get repo root for file dialog
        repo_root = Path(self.repo.repo.workdir)
        
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Open File", str(repo_root), 
            "All Files (*);;Python Files (*.py);;Text Files (*.txt)"
        )

        if file_path:
            # Convert to relative path
            try:
                rel_path = Path(file_path).relative_to(repo_root)
                self._open_file_by_path(str(rel_path))
            except ValueError:
                # File is outside repo
                QMessageBox.warning(
                    self, "Warning", 
                    "File is outside the repository. Cannot open."
                )

    def _save_current_file(self) -> None:
        """Save the current file (Ctrl+S)"""
        current_widget = self.branch_tabs.currentWidget()
        if isinstance(current_widget, BranchTabWidget):
            result = current_widget.save_current_file()
            if result:
                self.status_bar.showMessage(f"Saved → {result[:8]}")

    def _save_all_files(self) -> None:
        """Save all modified files (Ctrl+Shift+S)"""
        current_widget = self.branch_tabs.currentWidget()
        if isinstance(current_widget, BranchTabWidget):
            result = current_widget.save_all_files()
            if result:
                self.status_bar.showMessage(f"Saved all → {result[:8]}")

    def _new_ai_session(self) -> None:
        """Create a new AI session as a branch"""
        session_id = str(uuid.uuid4())
        branch_name = f"forge/session/{session_id}"

        # Create git branch for this session
        self.repo.create_session_branch(session_id)

        # Create initial session commit with stub session.json
        session_data = {
            "session_id": session_id,
            "branch_name": branch_name,
            "messages": [],
            "active_files": [],
            "repo_summaries": {},
        }
        session_file_path = f".forge/sessions/{session_id}.json"
        tree_oid = self.repo.create_tree_from_changes(
            branch_name, {session_file_path: json.dumps(session_data, indent=2)}
        )
        self.repo.commit_tree(
            tree_oid, "initialize session", branch_name, commit_type=CommitType.PREPARE
        )

        # Open the new session branch
        self._open_branch(branch_name)
        
        # Refresh the branches menu
        self._populate_branches_menu()

        self.status_bar.showMessage("New AI session created")

    def _populate_branches_menu(self) -> None:
        """Populate branches submenu with all branches"""
        self._branches_submenu.clear()
        
        # Get all branches
        all_branches = list(self.repo.repo.branches)
        
        # Separate into regular branches and session branches
        regular_branches = [b for b in all_branches if not b.startswith("forge/session/")]
        session_branches = [b for b in all_branches if b.startswith("forge/session/")]
        
        # Add regular branches
        for branch_name in sorted(regular_branches):
            action = self._branches_submenu.addAction(f"🌿 {branch_name}")
            action.triggered.connect(
                lambda checked=False, b=branch_name: self._open_branch(b)
            )
        
        if regular_branches and session_branches:
            self._branches_submenu.addSeparator()
        
        # Add session branches
        for branch_name in sorted(session_branches):
            session_id = branch_name.replace("forge/session/", "")
            action = self._branches_submenu.addAction(f"🤖 {session_id[:8]}...")
            action.triggered.connect(
                lambda checked=False, b=branch_name: self._open_branch(b)
            )
        
        if not all_branches:
            self._branches_submenu.addAction("(No branches)").setEnabled(False)

    def _close_branch_tab(self, index: int) -> None:
        """Close a branch tab"""
        widget = self.branch_tabs.widget(index)
        
        # Check for unsaved changes
        if isinstance(widget, BranchTabWidget) and widget.has_unsaved_changes():
            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                "There are unsaved changes. Save before closing?",
                QMessageBox.StandardButton.Save | 
                QMessageBox.StandardButton.Discard | 
                QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save
            )
            
            if reply == QMessageBox.StandardButton.Cancel:
                return
            elif reply == QMessageBox.StandardButton.Save:
                widget.save_all_files()
        
        # Remove from tracking
        branch_name = self._get_branch_name_from_tab(index)
        if branch_name in self._workspaces:
            del self._workspaces[branch_name]
        if branch_name in self._branch_widgets:
            del self._branch_widgets[branch_name]
        
        # Remove tab
        self.branch_tabs.removeTab(index)

    def _open_settings(self) -> None:
        """Open settings dialog"""
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            # Settings were saved, could reload/apply them here
            self.status_bar.showMessage("Settings saved")

    def _get_current_workspace(self) -> BranchWorkspace | None:
        """Get the current branch's workspace"""
        current_widget = self.branch_tabs.currentWidget()
        if isinstance(current_widget, BranchTabWidget):
            return current_widget.workspace
        return None
