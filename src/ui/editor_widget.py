"""
Code editor widget using QScintilla
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtGui import QFont, QColor
from PySide6.Qsci import QsciScintilla, QsciLexerPython


class EditorWidget(QWidget):
    """Code editor with syntax highlighting and AI integration hooks"""
    
    def __init__(self, filepath=None):
        super().__init__()
        self.filepath = filepath
        self._setup_ui()
        
    def _setup_ui(self):
        """Setup the editor"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Create QScintilla editor
        self.editor = QsciScintilla()
        
        # Font
        font = QFont("Monospace", 10)
        self.editor.setFont(font)
        
        # Margins
        self.editor.setMarginType(0, QsciScintilla.NumberMargin)
        self.editor.setMarginWidth(0, "00000")
        self.editor.setMarginsForegroundColor(QColor("#888888"))
        
        # Indentation
        self.editor.setIndentationsUseTabs(False)
        self.editor.setTabWidth(4)
        self.editor.setAutoIndent(True)
        
        # Current line highlighting
        self.editor.setCaretLineVisible(True)
        self.editor.setCaretLineBackgroundColor(QColor("#f0f0f0"))
        
        # Lexer for syntax highlighting (default to Python)
        lexer = QsciLexerPython()
        lexer.setFont(font)
        self.editor.setLexer(lexer)
        
        layout.addWidget(self.editor)
        
    def load_file(self, filepath):
        """Load a file into the editor"""
        self.filepath = filepath
        try:
            with open(filepath, 'r') as f:
                content = f.read()
                self.editor.setText(content)
        except Exception as e:
            print(f"Error loading file: {e}")
            
    def save_file(self):
        """Save the current content to file"""
        if not self.filepath:
            return False
            
        try:
            with open(self.filepath, 'w') as f:
                f.write(self.editor.text())
            return True
        except Exception as e:
            print(f"Error saving file: {e}")
            return False
            
    def get_text(self):
        """Get editor content"""
        return self.editor.text()
        
    def set_text(self, text):
        """Set editor content"""
        self.editor.setText(text)
