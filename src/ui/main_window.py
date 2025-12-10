"""
Main window for Forge IDE
"""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QTabWidget, QMenuBar, QMenu, QStatusBar,
    QFileDialog, QMessageBox
)
from PySide6.QtCore import Qt
from pathlib import Path
from .editor_widget import EditorWidget
from .ai_chat_widget import AIChatWidget
from .settings_dialog import SettingsDialog
from ..git_backend.repository import ForgeRepository
from ..config.settings import Settings


class MainWindow(QMainWindow):
    """Main application window with VSCode-like layout"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Forge")
        self.setGeometry(100, 100, 1400, 900)
        
        # Initialize settings
        self.settings = Settings()
        
        # Initialize git repository
        try:
            self.repo = ForgeRepository()
            self.sessions_dir = Path(self.repo.repo.workdir) / ".forge" / "sessions"
            self.sessions_dir.mkdir(parents=True, exist_ok=True)
        except ValueError:
            self.repo = None
            self.sessions_dir = Path(".forge") / "sessions"
            QMessageBox.warning(
                self,
                "Not a Git Repository",
                "Forge works best in a git repository. Some features may be limited."
            )
        
        self._setup_ui()
        self._setup_menus()
        self._load_existing_sessions()
        
    def _setup_ui(self):
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
        
    def _setup_menus(self):
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
        
        # Git menu
        git_menu = menubar.addMenu("&Git")
        git_menu.addAction("View Branches")
        git_menu.addAction("Commit History")
        
    def _open_file(self):
        """Open a file in a new editor tab"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open File",
            "",
            "All Files (*);;Python Files (*.py);;Text Files (*.txt)"
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
        
    def _new_ai_session(self):
        """Create a new AI session tab"""
        session_widget = AIChatWidget(settings=self.settings, repo=self.repo)
        session_widget.session_updated.connect(lambda: self._save_session(session_widget))
        
        # Create git branch for this session if we have a repo
        if self.repo:
            try:
                self.repo.create_session_branch(session_widget.session_id)
            except Exception as e:
                print(f"Error creating session branch: {e}")
        
        # Count existing AI sessions for naming
        ai_session_count = sum(1 for i in range(self.tabs.count()) 
                              if isinstance(self.tabs.widget(i), AIChatWidget))
        
        index = self.tabs.addTab(session_widget, f"🤖 Session {ai_session_count + 1}")
        self.tabs.setCurrentIndex(index)
        
        # Save initial session state
        self._save_session(session_widget)
        self.status_bar.showMessage("New AI session created")
    
    def _save_session(self, session_widget):
        """Save a session to disk"""
        try:
            session_widget.save_session(self.sessions_dir)
        except Exception as e:
            print(f"Error saving session: {e}")
    
    def _load_existing_sessions(self):
        """Load existing sessions from .forge/sessions/"""
        if not self.sessions_dir.exists():
            return
        
        for session_file in self.sessions_dir.glob("*.json"):
            try:
                session_widget = AIChatWidget.load_session(session_file, settings=self.settings, repo=self.repo)
                session_widget.session_updated.connect(lambda sw=session_widget: self._save_session(sw))
                
                # Use session ID for tab name
                tab_name = f"🤖 {session_widget.session_id[:8]}"
                self.tabs.addTab(session_widget, tab_name)
            except Exception as e:
                print(f"Error loading session {session_file}: {e}")
        
    def _close_tab(self, index):
        """Close a tab (editor or AI session)"""
        widget = self.tabs.widget(index)
        
        # Save AI session before closing
        if isinstance(widget, AIChatWidget):
            self._save_session(widget)
        
        # TODO: Check for unsaved changes in editor tabs
        
        self.tabs.removeTab(index)
    
    def _open_settings(self):
        """Open settings dialog"""
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            # Settings were saved, could reload/apply them here
            self.status_bar.showMessage("Settings saved")
