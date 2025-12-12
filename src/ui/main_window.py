"""
Main window for Forge IDE
"""

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .ai_chat_widget import AIChatWidget

from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMenu,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config.settings import Settings
from ..git_backend.commit_types import CommitType
from ..git_backend.repository import ForgeRepository
from .ai_chat_widget import AIChatWidget
from .editor_widget import EditorWidget
from .settings_dialog import SettingsDialog
from .welcome_widget import WelcomeWidget


class MainWindow(QMainWindow):
    """Main application window with VSCode-like layout"""

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

        self._setup_ui()
        self._setup_menus()
        self._add_welcome_tab()
        self._load_existing_sessions()

    def _setup_ui(self) -> None:
        """Setup the main UI layout"""
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        # Main tab widget for both editors and AI sessions
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.setMovable(True)

        layout.addWidget(self.tabs)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

    def _add_welcome_tab(self) -> None:
        """Add welcome tab as the first tab"""
        welcome = WelcomeWidget(self.repo)
        welcome.new_session_requested.connect(self._new_ai_session)
        welcome.open_file_requested.connect(self._open_file_by_path)
        welcome.open_session_requested.connect(self._open_existing_session)

        self.tabs.addTab(welcome, "🏠 Welcome")

    def _open_file_by_path(self, filepath: str) -> None:
        """Open a file by its path (used by welcome widget)"""
        # Check if file is already open
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            if isinstance(widget, EditorWidget) and widget.filepath == filepath:
                self.tabs.setCurrentIndex(i)
                self.status_bar.showMessage(f"File already open: {filepath}")
                return

        # Create new editor tab
        editor = EditorWidget(filepath=filepath)

        # Load from git, not filesystem
        content = self.repo.get_file_content(filepath)
        editor.set_text(content)

        # Use just the filename for the tab label
        filename = Path(filepath).name
        index = self.tabs.addTab(editor, f"📄 {filename}")
        self.tabs.setCurrentIndex(index)

        self.status_bar.showMessage(f"Opened: {filepath}")

    def _setup_menus(self) -> None:
        """Setup menu bar"""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&File")
        file_menu.addAction("&Open File...", self._open_file)
        file_menu.addAction("&New AI Session", self._new_ai_session)
        file_menu.addSeparator()
        file_menu.addAction("&Settings...", self._open_settings)
        file_menu.addSeparator()
        file_menu.addAction("E&xit", self.close)

        # Edit menu
        edit_menu = menubar.addMenu("&Edit")
        edit_menu.addAction("&Undo")
        edit_menu.addAction("&Redo")

        # Sessions menu
        sessions_menu = menubar.addMenu("&Sessions")
        sessions_menu.addAction("&New AI Session", self._new_ai_session)
        sessions_menu.addSeparator()

        # Add existing sessions to menu
        self._populate_sessions_menu(sessions_menu)

        # Git menu
        git_menu = menubar.addMenu("&Git")
        git_menu.addAction("View Branches")
        git_menu.addAction("Commit History")

    def _open_file(self) -> None:
        """Open a file in a new editor tab"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Open File", "", "All Files (*);;Python Files (*.py);;Text Files (*.txt)"
        )

        if file_path:
            # Check if file is already open
            for i in range(self.tabs.count()):
                widget = self.tabs.widget(i)
                if isinstance(widget, EditorWidget) and widget.filepath == file_path:
                    self.tabs.setCurrentIndex(i)
                    self.status_bar.showMessage(f"File already open: {file_path}")
                    return

            # Create new editor tab
            editor = EditorWidget(filepath=file_path)
            editor.load_file(file_path)

            # Use just the filename for the tab label
            filename = Path(file_path).name
            index = self.tabs.addTab(editor, f"📄 {filename}")
            self.tabs.setCurrentIndex(index)

            self.status_bar.showMessage(f"Opened: {file_path}")

    def _new_ai_session(self) -> None:
        """Create a new AI session tab"""
        # Generate session ID first
        import uuid

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

        # Now create widget with the session_id
        session_widget = AIChatWidget(session_id=session_id, settings=self.settings, repo=self.repo)

        # Count existing AI sessions for naming
        ai_session_count = sum(
            1 for i in range(self.tabs.count()) if isinstance(self.tabs.widget(i), AIChatWidget)
        )

        index = self.tabs.addTab(session_widget, f"🤖 Session {ai_session_count + 1}")
        self.tabs.setCurrentIndex(index)

        # Refresh the Sessions menu to include the new session
        self._refresh_sessions_menu()

        self.status_bar.showMessage("New AI session created")

    def _populate_sessions_menu(self, menu: Any) -> None:
        """Populate sessions menu with existing sessions"""
        session_branches = [
            name for name in self.repo.repo.branches if name.startswith("forge/session/")
        ]

        if session_branches:
            for branch_name in sorted(session_branches):
                session_id = branch_name.replace("forge/session/", "")
                action = menu.addAction(f"📋 {session_id[:8]}...")
                action.triggered.connect(
                    lambda checked=False, sid=session_id: self._open_existing_session(sid)
                )
        else:
            menu.addAction("(No existing sessions)").setEnabled(False)

    def _refresh_sessions_menu(self) -> None:
        """Refresh the Sessions menu with current sessions"""
        # Find the Sessions menu
        menubar = self.menuBar()
        for action in menubar.actions():
            if action.text() == "&Sessions":
                menu_obj = action.menu()
                assert isinstance(menu_obj, QMenu), "Sessions menu must be a QMenu"
                # Clear existing items
                menu_obj.clear()
                # Re-add "New AI Session" action
                menu_obj.addAction("&New AI Session", self._new_ai_session)
                menu_obj.addSeparator()
                # Re-populate with current sessions
                self._populate_sessions_menu(menu_obj)
                break

    def _open_existing_session(self, session_id: str) -> None:
        """Open an existing session by ID"""
        # Check if session is already open
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            if isinstance(widget, AIChatWidget) and widget.session_id == session_id:
                self.tabs.setCurrentIndex(i)
                self.status_bar.showMessage(f"Session already open: {session_id[:8]}")
                return

        # Load session data from git
        branch_name = f"forge/session/{session_id}"
        session_file = f".forge/sessions/{session_id}.json"

        content = self.repo.get_file_content(session_file, branch_name)
        session_data = json.loads(content)

        # Create session widget from data
        session_widget = AIChatWidget(
            session_id=session_id,
            session_data=session_data,
            settings=self.settings,
            repo=self.repo,
        )

        # Use session ID for tab name
        tab_name = f"🤖 {session_id[:8]}"
        index = self.tabs.addTab(session_widget, tab_name)
        self.tabs.setCurrentIndex(index)

        self.status_bar.showMessage(f"Opened session: {session_id[:8]}")

    def _load_existing_sessions(self) -> None:
        """Load existing sessions from git branches (called on startup)"""
        # Don't auto-load sessions on startup - let user open them from welcome screen or menu
        pass

    def _close_tab(self, index: int) -> None:
        """Close a tab (editor or AI session)"""
        # Sessions are persisted in git commits, not on close
        # TODO: Check for unsaved changes in editor tabs
        self.tabs.removeTab(index)

    def _open_settings(self) -> None:
        """Open settings dialog"""
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            # Settings were saved, could reload/apply them here
            self.status_bar.showMessage("Settings saved")
