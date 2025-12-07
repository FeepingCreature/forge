"""
Main window for Forge IDE
"""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QTabWidget, QMenuBar, QMenu, QStatusBar
)
from PySide6.QtCore import Qt
from .editor_widget import EditorWidget
from .ai_chat_widget import AIChatWidget


class MainWindow(QMainWindow):
    """Main application window with VSCode-like layout"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Forge")
        self.setGeometry(100, 100, 1400, 900)
        
        self._setup_ui()
        self._setup_menus()
        
    def _setup_ui(self):
        """Setup the main UI layout"""
        # Central widget with splitter
        central = QWidget()
        self.setCentralWidget(central)
        
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Main splitter: editor on left, AI sessions on right
        self.splitter = QSplitter(Qt.Horizontal)
        
        # Left side: file tabs with editors
        self.editor_tabs = QTabWidget()
        self.editor_tabs.setTabsClosable(True)
        self.editor_tabs.tabCloseRequested.connect(self._close_editor_tab)
        
        # Right side: AI session tabs
        self.ai_tabs = QTabWidget()
        self.ai_tabs.setTabsClosable(True)
        self.ai_tabs.tabCloseRequested.connect(self._close_ai_tab)
        
        self.splitter.addWidget(self.editor_tabs)
        self.splitter.addWidget(self.ai_tabs)
        self.splitter.setStretchFactor(0, 2)  # Editor gets more space
        self.splitter.setStretchFactor(1, 1)
        
        layout.addWidget(self.splitter)
        
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
        file_menu.addAction("E&xit", self.close)
        
        # Edit menu
        edit_menu = menubar.addMenu("&Edit")
        edit_menu.addAction("&Undo")
        edit_menu.addAction("&Redo")
        
        # View menu
        view_menu = menubar.addMenu("&View")
        view_menu.addAction("Toggle AI Panel", self._toggle_ai_panel)
        
        # Git menu
        git_menu = menubar.addMenu("&Git")
        git_menu.addAction("View Branches")
        git_menu.addAction("Commit History")
        
    def _open_file(self):
        """Open a file in a new editor tab"""
        # TODO: Implement file dialog and open
        self.status_bar.showMessage("Open file - not yet implemented")
        
    def _new_ai_session(self):
        """Create a new AI session tab"""
        session_widget = AIChatWidget()
        index = self.ai_tabs.addTab(session_widget, f"Session {self.ai_tabs.count() + 1}")
        self.ai_tabs.setCurrentIndex(index)
        self.status_bar.showMessage("New AI session created")
        
    def _toggle_ai_panel(self):
        """Toggle visibility of AI panel"""
        if self.ai_tabs.isVisible():
            self.ai_tabs.hide()
        else:
            self.ai_tabs.show()
            
    def _close_editor_tab(self, index):
        """Close an editor tab"""
        self.editor_tabs.removeTab(index)
        
    def _close_ai_tab(self, index):
        """Close an AI session tab"""
        # TODO: Cleanup git branch for this session
        self.ai_tabs.removeTab(index)
