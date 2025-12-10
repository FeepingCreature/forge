"""
Code editor widget with custom syntax highlighting
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QPlainTextEdit, QTextEdit
from PySide6.QtGui import (
    QFont, QColor, QPainter, QTextFormat, QSyntaxHighlighter,
    QTextCharFormat, QPalette
)
from PySide6.QtCore import Qt, QRect, QSize
from typing import Any, Optional
import re


class LineNumberArea(QWidget):
    """Widget for displaying line numbers"""
    
    def __init__(self, editor: 'CodeEditor') -> None:
        super().__init__(editor)
        self.editor = editor
        
    def sizeHint(self) -> QSize:
        return QSize(self.editor.line_number_area_width(), 0)
        
    def paintEvent(self, event: Any) -> None:
        self.editor.line_number_area_paint_event(event)


class PythonHighlighter(QSyntaxHighlighter):
    """Syntax highlighter for Python code"""
    
    def __init__(self, document: Any) -> None:
        super().__init__(document)
        
        # Define formats
        keyword_format = QTextCharFormat()
        keyword_format.setForeground(QColor("#0000FF"))
        keyword_format.setFontWeight(QFont.Weight.Bold)
        
        string_format = QTextCharFormat()
        string_format.setForeground(QColor("#008000"))
        
        comment_format = QTextCharFormat()
        comment_format.setForeground(QColor("#808080"))
        comment_format.setFontItalic(True)
        
        function_format = QTextCharFormat()
        function_format.setForeground(QColor("#795E26"))
        
        number_format = QTextCharFormat()
        number_format.setForeground(QColor("#098658"))
        
        # Define rules
        self.highlighting_rules = []
        
        # Keywords
        keywords = [
            'and', 'as', 'assert', 'break', 'class', 'continue', 'def',
            'del', 'elif', 'else', 'except', 'False', 'finally', 'for',
            'from', 'global', 'if', 'import', 'in', 'is', 'lambda', 'None',
            'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'True',
            'try', 'while', 'with', 'yield', 'async', 'await'
        ]
        
        for word in keywords:
            pattern = f'\\b{word}\\b'
            self.highlighting_rules.append((re.compile(pattern), keyword_format))
            
        # Functions
        self.highlighting_rules.append((re.compile(r'\b[A-Za-z_][A-Za-z0-9_]*(?=\()'), function_format))
        
        # Numbers
        self.highlighting_rules.append((re.compile(r'\b[0-9]+\.?[0-9]*\b'), number_format))
        
        # Strings
        self.highlighting_rules.append((re.compile(r'"[^"\\]*(\\.[^"\\]*)*"'), string_format))
        self.highlighting_rules.append((re.compile(r"'[^'\\]*(\\.[^'\\]*)*'"), string_format))
        
        # Comments
        self.highlighting_rules.append((re.compile(r'#[^\n]*'), comment_format))
        
    def highlightBlock(self, text: str) -> None:
        """Apply syntax highlighting to a block of text"""
        for pattern, format in self.highlighting_rules:
            for match in pattern.finditer(text):
                start = match.start()
                length = match.end() - start
                self.setFormat(start, length, format)


class CodeEditor(QPlainTextEdit):
    """Custom code editor with line numbers and syntax highlighting"""
    
    def __init__(self) -> None:
        super().__init__()
        
        # Setup font
        font = QFont("Monospace", 10)
        font.setStyleHint(QFont.StyleHint.TypeWriter)
        self.setFont(font)
        
        # Line number area
        self.line_number_area = LineNumberArea(self)
        
        # Connect signals
        self.blockCountChanged.connect(self.update_line_number_area_width)
        self.updateRequest.connect(self.update_line_number_area)
        self.cursorPositionChanged.connect(self.highlight_current_line)
        
        # Initial setup
        self.update_line_number_area_width(0)
        self.highlight_current_line()
        
        # Tab settings
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(' ') * 4)
        
        # Syntax highlighter (default to Python)
        self.highlighter = PythonHighlighter(self.document())
        
    def line_number_area_width(self) -> int:
        """Calculate width needed for line numbers"""
        digits = len(str(max(1, self.blockCount())))
        space = 10 + self.fontMetrics().horizontalAdvance('9') * digits
        return space
        
    def update_line_number_area_width(self, _: int) -> None:
        """Update the width of the line number area"""
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)
        
    def update_line_number_area(self, rect: QRect, dy: int) -> None:
        """Update the line number area when scrolling"""
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
            
        if rect.contains(self.viewport().rect()):
            self.update_line_number_area_width(0)
            
    def resizeEvent(self, event: Any) -> None:
        """Handle resize events"""
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height()))
        
    def line_number_area_paint_event(self, event: Any) -> None:
        """Paint line numbers"""
        painter = QPainter(self.line_number_area)
        painter.fillRect(event.rect(), QColor("#f0f0f0"))
        
        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        bottom = top + self.blockBoundingRect(block).height()
        
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(block_number + 1)
                painter.setPen(QColor("#888888"))
                painter.drawText(0, int(top), self.line_number_area.width() - 5, 
                               self.fontMetrics().height(), Qt.AlignmentFlag.AlignRight, number)
                               
            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            block_number += 1
            
    def highlight_current_line(self) -> None:
        """Highlight the current line"""
        extra_selections: list[Any] = []
        
        if not self.isReadOnly():
            selection: Any = QTextEdit.ExtraSelection()
            line_color = QColor("#ffffcc")
            selection.format.setBackground(line_color)
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            extra_selections.append(selection)
            
        self.setExtraSelections(extra_selections)


class EditorWidget(QWidget):
    """Code editor widget with syntax highlighting and AI integration hooks"""
    
    def __init__(self, filepath: Optional[str] = None) -> None:
        super().__init__()
        self.filepath = filepath
        self._setup_ui()
        
    def _setup_ui(self) -> None:
        """Setup the editor"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Create custom code editor
        self.editor = CodeEditor()
        
        layout.addWidget(self.editor)
        
    def load_file(self, filepath: str) -> None:
        """Load a file into the editor"""
        self.filepath = filepath
        try:
            with open(filepath, 'r') as f:
                content = f.read()
                self.editor.setPlainText(content)
        except Exception as e:
            print(f"Error loading file: {e}")
            
    def save_file(self) -> bool:
        """Save the current content to file"""
        if not self.filepath:
            return False
            
        try:
            with open(self.filepath, 'w') as f:
                f.write(self.editor.toPlainText())
            return True
        except Exception as e:
            print(f"Error saving file: {e}")
            return False
            
    def get_text(self) -> str:
        """Get editor content"""
        return self.editor.toPlainText()
        
    def set_text(self, text: str) -> None:
        """Set editor content"""
        self.editor.setPlainText(text)
