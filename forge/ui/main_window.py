"""
Main window for Forge IDE - Branch-first architecture
"""

import contextlib
import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from forge.config.settings import Settings
from forge.git_backend.commit_types import CommitType
from forge.git_backend.repository import ForgeRepository
from forge.llm.cost_tracker import COST_TRACKER
from forge.ui.actions import ActionRegistry
from forge.ui.ai_chat_widget import AIChatWidget
from forge.ui.branch_tab_widget import BranchTabWidget
from forge.ui.branch_workspace import BranchWorkspace
from forge.ui.git_graph_widget import GitGraphScrollArea
from forge.ui.global_search import GlobalSearchDialog
from forge.ui.settings_dialog import SettingsDialog


class MainWindow(QMainWindow):
    """Main application window with branch-first architecture"""

    def __init__(self, initial_files: list[str] | None = None) -> None:
        super().__init__()
        self.setGeometry(100, 100, 1400, 900)

        # Store initial files to open after UI setup
        self._initial_files = initial_files or []

        # Initialize settings
        self.settings = Settings()

        # Initialize git repository (required - Forge only works in git repos)
        self.repo = ForgeRepository()
        self.sessions_dir = Path(self.repo.repo.workdir) / ".forge" / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        # Set window title with folder name
        folder_name = Path(self.repo.repo.workdir).name
        self.setWindowTitle(f"Forge — {folder_name}")

        # Track branch workspaces
        self._workspaces: dict[str, BranchWorkspace] = {}
        self._branch_widgets: dict[str, BranchTabWidget] = {}

        self._setup_ui()
        self._setup_menus()
        self._setup_shortcuts()
        self._open_default_branch()

        # Open any files passed on command line
        self._open_initial_files()

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

        # Add git graph as first tab (not closable)
        self.git_graph = GitGraphScrollArea(self.repo)
        self.branch_tabs.addTab(self.git_graph, "📊 Git")
        # Make git graph tab not closable
        self.branch_tabs.tabBar().setTabButton(
            0, self.branch_tabs.tabBar().ButtonPosition.RightSide, None
        )

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

        # Context stats display (right side of status bar)
        self.context_label = QLabel("~0")
        self.context_label.setToolTip("Context token usage (updates after first AI message)")
        self.status_bar.addPermanentWidget(self.context_label)

        # Cost display (right side of status bar, bold)
        self.cost_label = QLabel("<b>$0.0000</b>")
        self.cost_label.setToolTip("Session API cost (OpenRouter)")
        self.status_bar.addPermanentWidget(self.cost_label)

        # Update cost display when cost changes
        COST_TRACKER.cost_updated.connect(self._update_cost_display)

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
        # Try to load existing session data from .forge/session.json
        session_data = self._load_session_data(branch_name)
        chat_widget = AIChatWidget(
            session_data=session_data,
            settings=self.settings,
            repo=self.repo,
            branch_name=branch_name,
        )

        # Set up unsaved changes check callback
        chat_widget.unsaved_changes_check = lambda bw=branch_widget: self._check_unsaved_before_ai(
            bw
        )

        # Add AI chat as first tab
        branch_widget.add_ai_chat_tab(chat_widget)

        # Connect signals
        branch_widget.file_saved.connect(self._on_file_saved)
        branch_widget.ai_turn_started.connect(self._on_ai_turn_started)
        branch_widget.ai_turn_finished.connect(self._on_ai_turn_finished)

        # Connect file open to AI context sync (opening adds to context)
        # Note: closing a tab does NOT remove from context - use file explorer to manage
        branch_widget.file_opened.connect(chat_widget.add_file_to_context)

        # Connect user typing signal to clear waiting indicator
        chat_widget.user_typing.connect(branch_widget.clear_waiting_indicator)

        # Connect file explorer context changes to chat widget
        branch_widget.context_file_added.connect(chat_widget.add_file_to_context)
        branch_widget.context_file_removed.connect(chat_widget.remove_file_from_context)

        # Connect chat widget context changes back to file explorer for visual update
        chat_widget.context_changed.connect(branch_widget.update_context_display)

        # Connect context stats for status bar updates and file tab tooltips
        chat_widget.context_stats_updated.connect(
            lambda stats, bw=branch_widget: self._on_context_stats_updated(stats, bw)
        )

        # Restore active files to AI context (but don't force open tabs)
        # The AI's context is restored, but user's UI state is not forced
        if session_data and "active_files" in session_data:
            for filepath in session_data["active_files"]:
                # Never restore session.json to context - it contains conversation history
                if filepath == ".forge/session.json":
                    continue
                # Just add to context, don't open tab
                with contextlib.suppress(FileNotFoundError):
                    chat_widget.add_file_to_context(filepath)

        # Restore open file tabs from XDG cache
        branch_widget.restore_open_files(str(self.repo.repo.workdir))

        # Store references
        self._workspaces[branch_name] = workspace
        self._branch_widgets[branch_name] = branch_widget

        # Add to tab widget - all branches get same treatment
        # Use 🤖 if branch has session data, 🌿 otherwise
        has_session = session_data is not None
        icon = "🤖" if has_session else "🌿"
        index = self.branch_tabs.addTab(branch_widget, f"{icon} {branch_name}")
        self.branch_tabs.setCurrentIndex(index)

        self.status_bar.showMessage(f"Opened branch: {branch_name}")

    def _load_session_data(self, branch_name: str) -> dict[str, Any] | None:
        """Load session data from .forge/session.json in the branch"""
        try:
            content = self.repo.get_file_content(".forge/session.json", branch_name)
            result: dict[str, Any] = json.loads(content)
            return result
        except (FileNotFoundError, KeyError):
            return None

    def _check_unsaved_before_ai(self, branch_widget: BranchTabWidget) -> bool:
        """
        Check for unsaved changes before AI turn.

        Returns True if OK to proceed, False to abort.
        """
        if not branch_widget.has_unsaved_changes():
            return True

        # Prompt user
        reply = QMessageBox.question(
            self,
            "Unsaved Changes",
            "There are unsaved changes. Save before AI turn?\n\n"
            "(AI needs committed state to work properly)",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )

        if reply == QMessageBox.StandardButton.Cancel:
            return False
        elif reply == QMessageBox.StandardButton.Save:
            branch_widget.save_all_files()
            return True
        else:
            # Discard - proceed anyway (user's choice)
            return True

    def _on_file_saved(self, filepath: str, commit_oid: str) -> None:
        """Handle file save notification"""
        self.status_bar.showMessage(f"Saved {filepath} → {commit_oid[:8]}")

    def _on_ai_turn_started(self) -> None:
        """Handle AI turn starting"""
        self.status_bar.showMessage("🤖 AI working...")

    def _on_ai_turn_finished(self, commit_oid: str) -> None:
        """Handle AI turn finishing"""
        if commit_oid:
            self.status_bar.showMessage(f"🤖 AI finished → {commit_oid[:8]}")
        else:
            self.status_bar.showMessage("🤖 AI finished (no changes)")

    def _on_branch_tab_changed(self, index: int) -> None:
        """Handle branch tab switch"""
        if index < 0:
            return
        # Guard against signal firing before status_bar is created
        if not hasattr(self, "status_bar"):
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
        branch_name = self._get_branch_name_from_tab(index)

        # Close action
        close_action = menu.addAction("Close")
        close_action.triggered.connect(lambda: self._close_branch_tab(index))

        menu.addSeparator()

        # Fork action
        fork_action = menu.addAction("Fork branch...")
        fork_action.triggered.connect(lambda: self._fork_branch(branch_name))

        # Delete action (with confirmation)
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
        from PySide6.QtWidgets import QPushButton

        menu = QMenu(self)

        # New AI Session
        session_action = menu.addAction("🤖 New AI Session")
        session_action.triggered.connect(self._new_ai_session)

        # New feature branch
        feature_action = menu.addAction("🌿 New Branch...")
        feature_action.triggered.connect(self._new_feature_branch)

        # Show menu at button position
        btn = self.sender()
        if isinstance(btn, QPushButton):
            menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _new_feature_branch(self) -> None:
        """Create a new feature branch"""
        import pygit2

        name, ok = QInputDialog.getText(self, "New Branch", "Branch name:", text="feature/")
        if ok and name:
            # Create branch from current HEAD
            try:
                head = self.repo.repo.head
                commit = head.peel(pygit2.Commit)
                self.repo.repo.branches.create(name, commit)
                self._open_branch(name)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to create branch: {e}")

    def _fork_branch(self, source_branch: str) -> None:
        """Fork an existing branch"""
        import pygit2

        from forge.ui.fork_branch_dialog import ForkBranchDialog

        dialog = ForkBranchDialog(source_branch, self)
        if dialog.exec() == ForkBranchDialog.DialogCode.Accepted:
            name = dialog.get_branch_name()
            include_session = dialog.should_include_session()

            if not name:
                return

            try:
                # Get source branch head and peel to Commit
                source_obj = self.repo.get_branch_head(source_branch)
                source_commit = source_obj.peel(pygit2.Commit)
                self.repo.repo.branches.create(name, source_commit)

                # Copy session if requested
                if include_session:
                    self._copy_session(source_branch, name)

                self._open_branch(name)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to fork branch: {e}")

    def _copy_session(self, source_branch: str, target_branch: str) -> None:
        """Copy session data from one branch to another"""
        from forge.vfs.work_in_progress import WorkInProgressVFS

        try:
            # Read session from source branch using the repo helper
            session_path = ".forge/session.json"
            session_data = self.repo.get_file_content(session_path, source_branch)

            # Write to target branch
            target_vfs = WorkInProgressVFS(self.repo, target_branch)
            target_vfs.write_file(session_path, session_data)
            target_vfs.commit(f"Copy session from {source_branch}")
        except Exception:
            # If session copy fails, just continue without it
            pass

    def _delete_branch(self, branch_name: str, tab_index: int) -> None:
        """Delete a branch (with confirmation)"""
        reply = QMessageBox.warning(
            self,
            "Delete Branch",
            f"Are you sure you want to delete branch '{branch_name}'?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
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
        # Note: File → Open removed - use the file explorer sidebar instead
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
        import pygit2
        from PySide6.QtWidgets import QInputDialog

        # Prompt for branch name
        name, ok = QInputDialog.getText(
            self, "New AI Session", "Branch name for AI session:", text="ai/"
        )
        if not ok or not name:
            return

        branch_name = name

        # Create git branch from current HEAD
        try:
            head = self.repo.repo.head
            commit = head.peel(pygit2.Commit)
            self.repo.repo.branches.create(branch_name, commit)
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.critical(self, "Error", f"Failed to create branch: {e}")
            return

        # Create initial session commit with .forge/session.json
        session_data: dict[str, Any] = {
            "messages": [],
            "active_files": [],
        }
        tree_oid = self.repo.create_tree_from_changes(
            branch_name, {".forge/session.json": json.dumps(session_data, indent=2)}
        )
        self.repo.commit_tree(
            tree_oid, "initialize session", branch_name, commit_type=CommitType.PREPARE
        )

        # Open the new session branch
        self._open_branch(branch_name)

        # Refresh the branches menu
        self._populate_branches_menu()

        self.status_bar.showMessage(f"New AI session created on branch: {branch_name}")

    def _populate_branches_menu(self) -> None:
        """Populate branches submenu with all branches"""
        self._branches_submenu.clear()

        # Get all branches
        all_branches = list(self.repo.repo.branches)

        # Add all branches - they're all equal now
        for branch_name in sorted(all_branches):
            # Check if branch has session data
            has_session = self._load_session_data(branch_name) is not None
            icon = "🤖" if has_session else "🌿"
            action = self._branches_submenu.addAction(f"{icon} {branch_name}")
            action.triggered.connect(lambda checked=False, b=branch_name: self._open_branch(b))

        if not all_branches:
            self._branches_submenu.addAction("(No branches)").setEnabled(False)

    def _close_branch_tab(self, index: int) -> None:
        """Close a branch tab"""
        # Don't close the git graph tab (index 0)
        if index == 0:
            return

        widget = self.branch_tabs.widget(index)

        # Check for unsaved changes
        if isinstance(widget, BranchTabWidget) and widget.has_unsaved_changes():
            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                "There are unsaved changes. Save before closing?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )

            if reply == QMessageBox.StandardButton.Cancel:
                return
            elif reply == QMessageBox.StandardButton.Save:
                widget.save_all_files()

        # Save open files to cache before closing
        if isinstance(widget, BranchTabWidget):
            widget.save_open_files_to_cache(str(self.repo.repo.workdir))

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

    def _setup_shortcuts(self) -> None:
        """Setup keyboard shortcuts via ActionRegistry"""
        # Create action registry
        self.action_registry = ActionRegistry(self)

        # Load custom shortcuts from settings
        custom_shortcuts = self.settings.get("keybindings", {})
        if isinstance(custom_shortcuts, dict):
            self.action_registry.load_custom_shortcuts(custom_shortcuts)

        # Register all actions with their default shortcuts
        # File actions
        self.action_registry.register(
            "file.save", "Save File", self._save_current_file, shortcut="Ctrl+S", category="File"
        )
        self.action_registry.register(
            "file.save_all",
            "Save All Files",
            self._save_all_files,
            shortcut="Ctrl+Shift+S",
            category="File",
        )
        self.action_registry.register(
            "file.close",
            "Close File Tab",
            self._close_current_file_tab,
            shortcut="Ctrl+W",
            category="File",
        )
        self.action_registry.register(
            "file.settings", "Open Settings", self._open_settings, category="File"
        )

        # Navigation actions
        self.action_registry.register(
            "nav.next_tab",
            "Next Branch Tab",
            self._next_branch_tab,
            shortcut="Ctrl+Tab",
            category="Navigation",
        )
        self.action_registry.register(
            "nav.prev_tab",
            "Previous Branch Tab",
            self._prev_branch_tab,
            shortcut="Ctrl+Shift+Tab",
            category="Navigation",
        )
        self.action_registry.register(
            "nav.quick_open",
            "Quick Open File",
            self._quick_open,
            shortcut="Ctrl+E",
            category="Navigation",
        )

        # Branch actions
        self.action_registry.register(
            "branch.new",
            "New Branch...",
            self._show_new_branch_dialog,
            shortcut="Ctrl+N",
            category="Branch",
        )
        self.action_registry.register(
            "branch.close",
            "Close Branch Tab",
            self._close_current_branch_tab,
            shortcut="Ctrl+Shift+W",
            category="Branch",
        )
        self.action_registry.register(
            "branch.new_session", "New AI Session", self._new_ai_session, category="Branch"
        )

        # Search actions
        self.action_registry.register(
            "search.global",
            "Search in Files",
            self._show_global_search,
            shortcut="Ctrl+Shift+F",
            category="Search",
        )
        self.action_registry.register(
            "search.ask_repo",
            "Ask About Repo",
            self._show_ask_repo,
            shortcut="Ctrl+Shift+A",
            category="Search",
        )

        # View actions
        self.action_registry.register(
            "view.command_palette",
            "Command Palette",
            self._show_command_palette,
            shortcut="Ctrl+Shift+P",
            category="View",
        )

    def _show_command_palette(self) -> None:
        """Show the command palette"""
        from forge.ui.command_palette import CommandPalette

        palette = CommandPalette(self.action_registry, self)
        palette.action_triggered.connect(self.action_registry.trigger)
        # Center on window
        palette.move(self.x() + (self.width() - palette.width()) // 2, self.y() + 100)
        palette.exec()

    def _quick_open(self) -> None:
        """Show quick open dialog for current branch"""
        current_widget = self.branch_tabs.currentWidget()
        if isinstance(current_widget, BranchTabWidget):
            current_widget.show_quick_open()

    def _show_global_search(self) -> None:
        """Show global search dialog"""
        workspace = self._get_current_workspace()
        if not workspace:
            self.status_bar.showMessage("No branch open for search")
            return

        dialog = GlobalSearchDialog(workspace, self)
        dialog.file_selected.connect(self._on_search_file_selected)
        dialog.exec()

    def _show_ask_repo(self) -> None:
        """Show 'Ask About Repo' dialog"""
        workspace = self._get_current_workspace()
        if not workspace:
            self.status_bar.showMessage("No branch open")
            return

        from forge.ui.ask_repo_dialog import AskRepoDialog

        dialog = AskRepoDialog(workspace, self)
        dialog.exec()

    def _on_search_file_selected(self, filepath: str, line_num: int) -> None:
        """Handle file selection from global search"""
        # Open the file
        self._open_file_by_path(filepath)

        # TODO: Scroll to line_num in the editor
        # For now, just show which line was found
        self.status_bar.showMessage(f"Opened {filepath}:{line_num}")

    def _next_branch_tab(self) -> None:
        """Switch to next branch tab"""
        count = self.branch_tabs.count()
        if count > 1:
            current = self.branch_tabs.currentIndex()
            self.branch_tabs.setCurrentIndex((current + 1) % count)

    def _prev_branch_tab(self) -> None:
        """Switch to previous branch tab"""
        count = self.branch_tabs.count()
        if count > 1:
            current = self.branch_tabs.currentIndex()
            self.branch_tabs.setCurrentIndex((current - 1) % count)

    def _close_current_file_tab(self) -> None:
        """Close the current file tab in the active branch"""
        current_widget = self.branch_tabs.currentWidget()
        if isinstance(current_widget, BranchTabWidget):
            file_tabs = current_widget.file_tabs
            current_index = file_tabs.currentIndex()
            # Don't close AI chat tab (index 0)
            if current_index > 0:
                file_tabs.tabCloseRequested.emit(current_index)

    def _close_current_branch_tab(self) -> None:
        """Close the current branch tab"""
        current_index = self.branch_tabs.currentIndex()
        if current_index >= 0:
            self._close_branch_tab(current_index)

    def _on_context_stats_updated(
        self, stats: dict[str, Any], branch_widget: BranchTabWidget
    ) -> None:
        """Handle context stats update from AI chat widget"""
        # Only update if this is the current branch
        if self.branch_tabs.currentWidget() != branch_widget:
            return

        # Update file tab tooltips with token counts
        for file_info in stats.get("active_files", []):
            filepath = file_info.get("filepath", "")
            tokens = file_info.get("tokens", 0)
            if filepath and "error" not in file_info:
                branch_widget.update_file_tab_tooltip(filepath, tokens)

        # Update context label (permanent widget on right side of status bar)
        file_tokens = stats.get("file_tokens", 0)
        summary_tokens = stats.get("summary_tokens", 0)
        conversation_tokens = stats.get("conversation_tokens", 0)
        total_tokens = stats.get("total_context_tokens", 0)
        file_count = stats.get("file_count", 0)

        # Get model context limit from settings for warning
        model = self.settings.get("llm.model", "anthropic/claude-3.5-sonnet")
        context_limit = self._get_model_context_limit(model)

        # Build display with breakdown: files | summaries | convo
        def fmt(n: int) -> str:
            if n >= 1000:
                return f"{n // 1000}k"
            return str(n)

        breakdown = f"files {fmt(file_tokens)} | summaries {fmt(summary_tokens)} | convo {fmt(conversation_tokens)}"

        if context_limit:
            percent = (total_tokens / context_limit) * 100
            if percent > 80:
                text = f"⚠️ {breakdown} ({percent:.0f}%)"
            else:
                text = f"{breakdown} ({percent:.0f}%)"
        else:
            text = breakdown

        # Set tooltip with more detail
        tooltip = (
            f"Context tokens: ~{total_tokens:,}\n"
            f"  Files: ~{file_tokens:,} ({file_count} files)\n"
            f"  Summaries: ~{summary_tokens:,}\n"
            f"  Conversation: ~{conversation_tokens:,}"
        )

        self.context_label.setText(text)
        self.context_label.setToolTip(tooltip)

    def _get_model_context_limit(self, model: str) -> int | None:
        """Get context limit for a model (returns None if unknown)"""
        # Known context limits for common models
        limits = {
            "anthropic/claude-3.5-sonnet": 200000,
            "anthropic/claude-3-sonnet": 200000,
            "anthropic/claude-3-opus": 200000,
            "anthropic/claude-3-haiku": 200000,
            "openai/gpt-4-turbo": 128000,
            "openai/gpt-4": 8192,
            "openai/gpt-3.5-turbo": 16385,
            "google/gemini-pro": 32000,
        }

        # Check exact match first
        if model in limits:
            return limits[model]

        # Check prefix match (e.g., "anthropic/claude" matches any claude model)
        for prefix, limit in limits.items():
            if model.startswith(prefix.split("-")[0]):
                return limit

        return None

    def _update_cost_display(self, cost: float) -> None:
        """Update the cost display label with current accumulated cost."""
        daily = COST_TRACKER.daily_cost
        if daily > cost:
            self.cost_label.setText(f"<b>${cost:.4f}</b> (${daily:.2f} today)")
        else:
            self.cost_label.setText(f"<b>${cost:.4f}</b>")

    def _open_initial_files(self) -> None:
        """Open files passed on command line"""
        if not self._initial_files:
            return

        for filepath in self._initial_files:
            # Normalize path (handle relative paths)
            path = Path(filepath)
            if not path.is_absolute():
                # Make relative to repo root
                path = Path(filepath)

            # Open in current branch
            self._open_file_by_path(str(path))

    def closeEvent(self, event: Any) -> None:  # noqa: N802
        """Handle window close - save open files cache for all branches"""
        repo_path = str(self.repo.repo.workdir)

        # Save open files for all open branches
        for _branch_name, branch_widget in self._branch_widgets.items():
            branch_widget.save_open_files_to_cache(repo_path)

        super().closeEvent(event)
