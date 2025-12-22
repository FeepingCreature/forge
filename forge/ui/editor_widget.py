"""
Code editor widget with custom syntax highlighting
"""

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.ui.code_completion import CompletionManager

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeySequence,
    QPainter,
    QShortcut,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextFormat,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class LineNumberArea(QWidget):
    """Widget for displaying line numbers"""

    def __init__(self, editor: "CodeEditor") -> None:
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
            "and",
            "as",
            "assert",
            "break",
            "class",
            "continue",
            "def",
            "del",
            "elif",
            "else",
            "except",
            "False",
            "finally",
            "for",
            "from",
            "global",
            "if",
            "import",
            "in",
            "is",
            "lambda",
            "None",
            "nonlocal",
            "not",
            "or",
            "pass",
            "raise",
            "return",
            "True",
            "try",
            "while",
            "with",
            "yield",
            "async",
            "await",
        ]

        for word in keywords:
            pattern = f"\\b{word}\\b"
            self.highlighting_rules.append((re.compile(pattern), keyword_format))

        # Functions
        self.highlighting_rules.append(
            (re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?=\()"), function_format)
        )

        # Numbers
        self.highlighting_rules.append((re.compile(r"\b[0-9]+\.?[0-9]*\b"), number_format))

        # Strings
        self.highlighting_rules.append((re.compile(r'"[^"\\]*(\\.[^"\\]*)*"'), string_format))
        self.highlighting_rules.append((re.compile(r"'[^'\\]*(\\.[^'\\]*)*'"), string_format))

        # Comments
        self.highlighting_rules.append((re.compile(r"#[^\n]*"), comment_format))

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
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)

        # Syntax highlighter (default to Python)
        self.highlighter = PythonHighlighter(self.document())

    def line_number_area_width(self) -> int:
        """Calculate width needed for line numbers"""
        digits = len(str(max(1, self.blockCount())))
        space = 10 + self.fontMetrics().horizontalAdvance("9") * digits
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
        self.line_number_area.setGeometry(
            QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height())
        )

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
                painter.drawText(
                    0,
                    int(top),
                    self.line_number_area.width() - 5,
                    self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight,
                    number,
                )

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


class SearchBar(QWidget):
    """Search bar for finding text in the editor"""

    closed = Signal()
    find_next = Signal(str)
    find_prev = Signal(str)
    search_changed = Signal(str)  # Emitted when search text changes (for re-highlighting)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Search input
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Find...")
        self.search_input.returnPressed.connect(self._on_find_next)
        self.search_input.textChanged.connect(self._on_search_changed)
        layout.addWidget(self.search_input, 1)

        # Match count label
        self.match_label = QLabel("")
        self.match_label.setMinimumWidth(60)
        self.match_label.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.match_label)

        # Previous button
        self.prev_btn = QPushButton("▲")
        self.prev_btn.setFixedWidth(28)
        self.prev_btn.setToolTip("Previous match (Shift+Enter)")
        self.prev_btn.clicked.connect(self._on_find_prev)
        layout.addWidget(self.prev_btn)

        # Next button
        self.next_btn = QPushButton("▼")
        self.next_btn.setFixedWidth(28)
        self.next_btn.setToolTip("Next match (Enter)")
        self.next_btn.clicked.connect(self._on_find_next)
        layout.addWidget(self.next_btn)

        # Close button
        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedWidth(28)
        self.close_btn.setToolTip("Close (Escape)")
        self.close_btn.clicked.connect(self._on_close)
        layout.addWidget(self.close_btn)

        # Style
        self.setStyleSheet("""
            SearchBar {
                background: #f5f5f5;
                border-bottom: 1px solid #ddd;
            }
            QLineEdit {
                padding: 4px 8px;
                border: 1px solid #ccc;
                border-radius: 3px;
                background: white;
            }
            QLineEdit:focus {
                border-color: #0078d4;
            }
            QPushButton {
                padding: 4px;
                border: 1px solid #ccc;
                border-radius: 3px;
                background: white;
            }
            QPushButton:hover {
                background: #e8e8e8;
            }
            QPushButton:pressed {
                background: #d0d0d0;
            }
        """)

    def _on_search_changed(self, text: str) -> None:
        """Trigger search update when text changes"""
        self.search_changed.emit(text)

    def _on_find_next(self) -> None:
        text = self.search_input.text()
        if text:
            self.find_next.emit(text)

    def _on_find_prev(self) -> None:
        text = self.search_input.text()
        if text:
            self.find_prev.emit(text)

    def _on_close(self) -> None:
        self.closed.emit()

    def keyPressEvent(self, event: Any) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._on_close()
        elif (
            event.key() == Qt.Key.Key_Return
            and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self._on_find_prev()
        else:
            super().keyPressEvent(event)

    def set_match_info(self, current: int, total: int) -> None:
        """Update the match count display"""
        if total == 0:
            self.match_label.setText("No matches")
            self.match_label.setStyleSheet("color: #c00; font-size: 11px;")
        else:
            self.match_label.setText(f"{current} of {total}")
            self.match_label.setStyleSheet("color: #666; font-size: 11px;")

    def focus_input(self) -> None:
        """Focus the search input and select all text"""
        self.search_input.setFocus()
        self.search_input.selectAll()


class EditorWidget(QWidget):
    """Code editor widget with syntax highlighting and AI integration hooks"""

    def __init__(self, filepath: str | None = None) -> None:
        super().__init__()
        self.filepath = filepath
        self._match_positions: list[int] = []
        self._current_match_index = -1
        self._completion_manager: CompletionManager | None = None
        self._setup_ui()
        self._setup_completion()

    def _setup_ui(self) -> None:
        """Setup the editor"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Create custom code editor
        self.editor = CodeEditor()
        layout.addWidget(self.editor)

        # Search bar (hidden by default, at bottom)
        self.search_bar = SearchBar()
        self.search_bar.hide()
        self.search_bar.closed.connect(self._close_search)
        self.search_bar.find_next.connect(self._find_next)
        self.search_bar.find_prev.connect(self._find_prev)
        self.search_bar.search_changed.connect(self._on_search_changed)
        layout.addWidget(self.search_bar)

        # Keyboard shortcuts
        self._find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        self._find_shortcut.activated.connect(self._show_search)

    def _show_search(self) -> None:
        """Show the search bar"""
        self.search_bar.show()
        self.search_bar.focus_input()
        # If there's selected text, use it as the search term
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            self.search_bar.search_input.setText(cursor.selectedText())
            self.search_bar.search_input.selectAll()

    def _close_search(self) -> None:
        """Hide the search bar and clear highlights"""
        self.search_bar.hide()
        self._clear_search_highlights()
        self.editor.setFocus()

    def _on_search_changed(self, text: str) -> None:
        """Handle search text changes - update matches but stay on current if valid"""
        if not text:
            self._clear_search_highlights()
            self.search_bar.set_match_info(0, 0)
            return

        # Remember current cursor position
        cursor = self.editor.textCursor()
        cursor_pos = cursor.selectionStart()

        # Find all matches
        self._update_match_positions(text)

        if not self._match_positions:
            self._current_match_index = -1
            self.search_bar.set_match_info(0, 0)
            self._clear_search_highlights()
            return

        # Try to stay on the current match if cursor is still within a match
        found_current = False
        for i, pos in enumerate(self._match_positions):
            if pos <= cursor_pos < pos + len(text):
                self._current_match_index = i
                found_current = True
                break

        # If cursor not in a match, find the nearest match at or after cursor
        if not found_current:
            self._current_match_index = 0
            for i, pos in enumerate(self._match_positions):
                if pos >= cursor_pos:
                    self._current_match_index = i
                    break

        # Update display
        self.search_bar.set_match_info(self._current_match_index + 1, len(self._match_positions))
        self._highlight_matches(text)

        # Move cursor to current match
        match_pos = self._match_positions[self._current_match_index]
        cursor = self.editor.textCursor()
        cursor.setPosition(match_pos)
        cursor.setPosition(match_pos + len(text), cursor.MoveMode.KeepAnchor)
        self.editor.setTextCursor(cursor)
        self.editor.centerCursor()

    def _find_next(self, text: str) -> None:
        """Find and highlight the next occurrence"""
        self._do_find(text, forward=True)

    def _find_prev(self, text: str) -> None:
        """Find and highlight the previous occurrence"""
        self._do_find(text, forward=False)

    def _update_match_positions(self, text: str) -> None:
        """Update the list of match positions"""
        content = self.editor.document().toPlainText()
        self._match_positions = []
        search_text = text.lower()
        content_lower = content.lower()
        pos = 0
        while True:
            idx = content_lower.find(search_text, pos)
            if idx == -1:
                break
            self._match_positions.append(idx)
            pos = idx + 1

    def _do_find(self, text: str, forward: bool = True) -> None:
        """Perform the search operation - move to next/prev match"""
        if not text:
            return

        # Update matches first
        self._update_match_positions(text)

        if not self._match_positions:
            self._current_match_index = -1
            self.search_bar.set_match_info(0, 0)
            self._clear_search_highlights()
            return

        # Determine which match to select based on current position
        cursor = self.editor.textCursor()
        cursor_pos = cursor.selectionStart()

        if forward:
            # Find next match strictly after current position
            next_idx = -1
            for i, pos in enumerate(self._match_positions):
                if pos > cursor_pos:
                    next_idx = i
                    break
            if next_idx == -1:
                next_idx = 0  # Wrap around
            self._current_match_index = next_idx
        else:
            # Find previous match before current position
            prev_idx = -1
            for i in range(len(self._match_positions) - 1, -1, -1):
                if self._match_positions[i] < cursor_pos:
                    prev_idx = i
                    break
            if prev_idx == -1:
                prev_idx = len(self._match_positions) - 1  # Wrap around
            self._current_match_index = prev_idx

        # Update match info
        self.search_bar.set_match_info(self._current_match_index + 1, len(self._match_positions))

        # Highlight all matches and select current
        self._highlight_matches(text)

        # Move cursor to current match
        match_pos = self._match_positions[self._current_match_index]
        cursor = self.editor.textCursor()
        cursor.setPosition(match_pos)
        cursor.setPosition(match_pos + len(text), cursor.MoveMode.KeepAnchor)
        self.editor.setTextCursor(cursor)
        self.editor.centerCursor()

    def _highlight_matches(self, text: str) -> None:
        """Highlight all search matches"""
        extra_selections: list[Any] = []

        # Current line highlight (from CodeEditor)
        if not self.editor.isReadOnly():
            selection: Any = QTextEdit.ExtraSelection()
            line_color = QColor("#ffffcc")
            selection.format.setBackground(line_color)
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            selection.cursor = self.editor.textCursor()
            selection.cursor.clearSelection()
            extra_selections.append(selection)

        # Highlight all matches
        for i, pos in enumerate(self._match_positions):
            selection = QTextEdit.ExtraSelection()
            if i == self._current_match_index:
                # Current match - orange background
                selection.format.setBackground(QColor("#ff9632"))
            else:
                # Other matches - yellow background
                selection.format.setBackground(QColor("#ffff00"))
            cursor = self.editor.textCursor()
            cursor.setPosition(pos)
            cursor.setPosition(pos + len(text), cursor.MoveMode.KeepAnchor)
            selection.cursor = cursor
            extra_selections.append(selection)

        self.editor.setExtraSelections(extra_selections)

    def _clear_search_highlights(self) -> None:
        """Clear search highlights, keeping only the current line highlight"""
        self._match_positions = []
        self._current_match_index = -1
        self.editor.highlight_current_line()

    def _setup_completion(self) -> None:
        """Setup code completion if filepath is set."""
        if not self.filepath:
            return

        from forge.ui.code_completion import CompletionManager

        self._completion_manager = CompletionManager(self.editor, self.filepath)

        # Install event filter to intercept Tab key
        self.editor.installEventFilter(self)

    def eventFilter(self, obj: Any, event: Any) -> bool:  # noqa: N802
        """Filter events to intercept Tab key for completions."""
        from PySide6.QtCore import QEvent

        if obj == self.editor and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if (
                key == Qt.Key.Key_Tab
                and self._completion_manager
                and self._completion_manager.accept_completion()
            ):
                return True  # Completion accepted, consume event

            if key == Qt.Key.Key_Escape and self._completion_manager:
                self._completion_manager.dismiss_completion()

        return super().eventFilter(obj, event)

    def set_completion_enabled(self, enabled: bool) -> None:
        """Enable or disable code completion."""
        if self._completion_manager:
            self._completion_manager.set_enabled(enabled)

    def cleanup(self) -> None:
        """Clean up resources."""
        if self._completion_manager:
            self._completion_manager.cleanup()

    def get_text(self) -> str:
        """Get editor content"""
        return self.editor.toPlainText()

    def set_text(self, text: str) -> None:
        """Set editor content"""
        self.editor.setPlainText(text)
