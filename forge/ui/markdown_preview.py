"""
Markdown preview widget for rendering .md files.

Provides a toggle between source editing and rendered preview,
reusing the chat's Mermaid/MathJax/markdown rendering infrastructure.
"""

import html
import re
import tempfile
from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from forge.ui.js_cache import get_script_src, get_script_tag

# Persistent temp directory for preview HTML files (survives within process lifetime)
_PREVIEW_DIR = Path(tempfile.mkdtemp(prefix="forge_preview_"))


class _ExternalLinkPage(QWebEnginePage):
    """Page that opens links in external browser."""

    def acceptNavigationRequest(  # noqa: N802 - Qt override
        self, url: QUrl | str, nav_type: QWebEnginePage.NavigationType, is_main_frame: bool
    ) -> bool:
        if nav_type == QWebEnginePage.NavigationType.NavigationTypeTyped:
            return True
        if nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            if isinstance(url, str):
                url = QUrl(url)
            QDesktopServices.openUrl(url)
            return False
        return True


def _markdown_to_html(text: str) -> str:
    """Convert markdown to HTML.

    Simple but functional converter that handles the common cases.
    Fenced code blocks (including mermaid) are preserved for JS rendering.
    """
    lines = text.split("\n")
    html_parts: list[str] = []
    in_code_block = False
    code_lang = ""
    code_lines: list[str] = []
    in_list = False
    in_table = False
    table_alignments: list[str] = []

    def _flush_list() -> None:
        nonlocal in_list
        if in_list:
            html_parts.append("</ul>")
            in_list = False

    def _flush_table() -> None:
        nonlocal in_table, table_alignments
        if in_table:
            html_parts.append("</tbody></table>")
            in_table = False
            table_alignments = []

    def _inline(t: str) -> str:
        """Process inline markdown formatting."""
        t = html.escape(t)
        # Images before links (![alt](url))
        t = re.sub(
            r"!\[([^\]]*)\]\(([^)]+)\)", r'<img src="\2" alt="\1" style="max-width:100%">', t
        )
        # Links [text](url)
        t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', t)
        # Bold + italic
        t = re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", t)
        # Bold
        t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
        # Italic
        t = re.sub(r"\*(.+?)\*", r"<em>\1</em>", t)
        # Inline code
        t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
        # Strikethrough
        t = re.sub(r"~~(.+?)~~", r"<del>\1</del>", t)
        return t

    def _parse_table_row(line: str) -> list[str]:
        """Parse a table row into cells."""
        # Strip leading/trailing pipes and split
        stripped = line.strip()
        if stripped.startswith("|"):
            stripped = stripped[1:]
        if stripped.endswith("|"):
            stripped = stripped[:-1]
        return [cell.strip() for cell in stripped.split("|")]

    def _is_separator_row(line: str) -> bool:
        """Check if a line is a table separator row (e.g., |---|---|)."""
        stripped = line.strip()
        if not stripped.startswith("|") and "|" not in stripped:
            return False
        cells = _parse_table_row(stripped)
        return all(re.match(r"^:?-+:?$", cell.strip()) for cell in cells if cell.strip())

    def _parse_alignments(line: str) -> list[str]:
        """Parse alignment from separator row."""
        cells = _parse_table_row(line)
        alignments = []
        for cell in cells:
            cell = cell.strip()
            if cell.startswith(":") and cell.endswith(":"):
                alignments.append("center")
            elif cell.endswith(":"):
                alignments.append("right")
            else:
                alignments.append("left")
        return alignments

    i = 0
    while i < len(lines):
        line = lines[i]

        # Fenced code blocks
        if line.strip().startswith("```"):
            if not in_code_block:
                _flush_list()
                _flush_table()
                in_code_block = True
                code_lang = line.strip()[3:].strip()
                code_lines = []
            else:
                code_content = html.escape("\n".join(code_lines))
                lang_class = f' class="language-{code_lang}"' if code_lang else ""
                html_parts.append(f"<pre><code{lang_class}>{code_content}</code></pre>")
                in_code_block = False
                code_lang = ""
                code_lines = []
            i += 1
            continue

        if in_code_block:
            code_lines.append(line)
            i += 1
            continue

        # Blank line
        if not line.strip():
            _flush_list()
            _flush_table()
            i += 1
            continue

        # Table: detect header + separator pattern
        if not in_table and "|" in line and i + 1 < len(lines) and _is_separator_row(lines[i + 1]):
            _flush_list()
            cells = _parse_table_row(line)
            table_alignments = _parse_alignments(lines[i + 1])
            html_parts.append("<table><thead><tr>")
            for j, cell in enumerate(cells):
                align = table_alignments[j] if j < len(table_alignments) else "left"
                html_parts.append(f'<th style="text-align:{align}">{_inline(cell)}</th>')
            html_parts.append("</tr></thead><tbody>")
            in_table = True
            i += 2  # skip header + separator
            continue

        # Table body rows
        if in_table and "|" in line:
            cells = _parse_table_row(line)
            html_parts.append("<tr>")
            for j, cell in enumerate(cells):
                align = table_alignments[j] if j < len(table_alignments) else "left"
                html_parts.append(f'<td style="text-align:{align}">{_inline(cell)}</td>')
            html_parts.append("</tr>")
            i += 1
            continue

        if in_table:
            _flush_table()
            # Fall through to process this line normally

        # Headings
        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m:
            _flush_list()
            level = len(m.group(1))
            html_parts.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            i += 1
            continue

        # Horizontal rule
        if re.match(r"^(\*{3,}|-{3,}|_{3,})\s*$", line.strip()):
            _flush_list()
            html_parts.append("<hr>")
            i += 1
            continue

        # Unordered list items
        m = re.match(r"^[\s]*[-*+]\s+(.+)$", line)
        if m:
            if not in_list:
                in_list = True
                html_parts.append("<ul>")
            html_parts.append(f"<li>{_inline(m.group(1))}</li>")
            i += 1
            continue

        # Blockquote
        if line.strip().startswith("> "):
            _flush_list()
            html_parts.append(f"<blockquote>{_inline(line.strip()[2:])}</blockquote>")
            i += 1
            continue

        # Regular paragraph
        _flush_list()
        html_parts.append(f"<p>{_inline(line)}</p>")
        i += 1

    # Flush any open blocks
    _flush_list()
    _flush_table()

    if in_code_block:
        code_content = html.escape("\n".join(code_lines))
        lang_class = f' class="language-{code_lang}"' if code_lang else ""
        html_parts.append(f"<pre><code{lang_class}>{code_content}</code></pre>")

    return "\n".join(html_parts)


def _build_preview_html(markdown_text: str) -> str:
    """Build a complete HTML page for markdown preview."""
    body_html = _markdown_to_html(markdown_text)

    mathjax_tag = get_script_tag("mathjax")

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            padding: 24px 32px;
            background: #ffffff;
            margin: 0;
            line-height: 1.6;
            color: #24292e;
            max-width: 900px;
        }}
        h1, h2, h3, h4, h5, h6 {{
            margin-top: 24px;
            margin-bottom: 16px;
            font-weight: 600;
            line-height: 1.25;
            border-bottom: none;
        }}
        h1 {{ font-size: 2em; border-bottom: 1px solid #eaecef; padding-bottom: 0.3em; }}
        h2 {{ font-size: 1.5em; border-bottom: 1px solid #eaecef; padding-bottom: 0.3em; }}
        h3 {{ font-size: 1.25em; }}
        p {{ margin: 0 0 16px 0; }}
        a {{ color: #0366d6; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        code {{
            background: #f6f8fa;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: "SFMono-Regular", Consolas, "Courier New", monospace;
            font-size: 85%;
        }}
        pre {{
            background: #f6f8fa;
            padding: 16px;
            border-radius: 6px;
            overflow-x: auto;
            line-height: 1.45;
        }}
        pre code {{
            background: none;
            padding: 0;
            font-size: 85%;
        }}
        blockquote {{
            margin: 0 0 16px 0;
            padding: 0 16px;
            color: #6a737d;
            border-left: 4px solid #dfe2e5;
        }}
        ul, ol {{
            margin: 0 0 16px 0;
            padding-left: 2em;
        }}
        li {{ margin: 4px 0; }}
        table {{
            border-collapse: collapse;
            margin: 16px 0;
            width: 100%;
        }}
        th, td {{
            border: 1px solid #dfe2e5;
            padding: 8px 13px;
            text-align: left;
        }}
        th {{
            background: #f6f8fa;
            font-weight: 600;
        }}
        tr:nth-child(even) td {{
            background: #fafbfc;
        }}
        hr {{
            border: none;
            border-top: 1px solid #eaecef;
            margin: 24px 0;
        }}
        img {{ max-width: 100%; }}
        /* Mermaid diagrams */
        .mermaid-container {{
            margin: 16px 0;
            padding: 16px;
            background: #fafafa;
            border-radius: 8px;
            overflow-x: auto;
            text-align: center;
        }}
        .mermaid-container svg {{ max-width: 100%; height: auto; }}
        .mermaid-error {{
            color: #d32f2f;
            font-size: 12px;
            padding: 8px;
            background: #ffebee;
            border-radius: 4px;
            margin-bottom: 8px;
        }}
    </style>
    {mathjax_tag}
    <script>
        // Hidden sandbox for mermaid rendering (prevents error nodes in visible DOM)
        var _mermaidSandbox = null;
        function _getMermaidSandbox() {{
            if (!_mermaidSandbox) {{
                _mermaidSandbox = document.createElement('div');
                _mermaidSandbox.id = 'mermaid-sandbox';
                _mermaidSandbox.style.cssText = 'position:fixed;left:-9999px;top:-9999px;width:1px;height:1px;overflow:hidden;';
                document.body.appendChild(_mermaidSandbox);
            }}
            _mermaidSandbox.innerHTML = '';
            return _mermaidSandbox;
        }}

        function renderMermaidDiagrams() {{
            if (typeof mermaid === 'undefined') return;
            var codeBlocks = document.querySelectorAll('pre > code.language-mermaid');
            codeBlocks.forEach(function(codeBlock, index) {{
                var pre = codeBlock.parentElement;
                if (pre.dataset.mermaidProcessed) return;
                pre.dataset.mermaidProcessed = 'true';
                var diagramText = codeBlock.textContent;
                var diagramId = 'mermaid-' + Date.now() + '-' + index;
                var container = document.createElement('div');
                container.className = 'mermaid-container';
                var sandbox = _getMermaidSandbox();
                try {{
                    mermaid.render(diagramId, diagramText, sandbox).then(function(result) {{
                        container.innerHTML = result.svg;
                        pre.replaceWith(container);
                        sandbox.innerHTML = '';
                    }}).catch(function(err) {{
                        sandbox.innerHTML = '';
                        container.innerHTML = '<div class="mermaid-error">⚠️ Diagram error: ' +
                            (err.message || String(err)) + '</div>';
                        container.appendChild(pre.cloneNode(true));
                        pre.replaceWith(container);
                    }});
                }} catch (err) {{
                    console.error('Mermaid render error:', err);
                    if (_mermaidSandbox) _mermaidSandbox.innerHTML = '';
                }}
            }});
        }}
    </script>
</head>
<body>
{body_html}
<script>
    // Load mermaid dynamically after all functions are defined
    (function() {{
        var script = document.createElement('script');
        script.src = '{get_script_src("mermaid")}';
        script.onload = function() {{
            mermaid.initialize({{ startOnLoad: false, theme: 'default', suppressErrorRendering: true }});
            renderMermaidDiagrams();
        }};
        document.head.appendChild(script);
    }})();
</script>
</body>
</html>"""


class MarkdownPreviewWidget(QWidget):
    """
    Widget that wraps a code editor with a rendered markdown preview.

    Provides two clearly labeled toggle buttons to switch between
    editing (source) and preview (rendered) modes.
    """

    # Re-emit from inner editor for compatibility
    text_changed = Signal()

    def __init__(self, editor_widget: "QWidget", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.editor_widget = editor_widget
        self._preview_dirty = True

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Toggle bar
        toggle_bar = QWidget()
        toggle_bar.setFixedHeight(32)
        toggle_bar.setStyleSheet("""
            QWidget {
                background: #f0f0f0;
                border-bottom: 1px solid #d0d0d0;
            }
        """)
        toggle_layout = QHBoxLayout(toggle_bar)
        toggle_layout.setContentsMargins(8, 2, 8, 2)
        toggle_layout.setSpacing(0)

        self._edit_btn = QPushButton("✏️ Edit")
        self._preview_btn = QPushButton("👁 Preview")

        for btn in (self._edit_btn, self._preview_btn):
            btn.setFixedHeight(26)
            btn.setCursor(btn.cursor())
            btn.setStyleSheet(self._button_style(active=False))

        self._edit_btn.setStyleSheet(self._button_style(active=True))

        self._edit_btn.clicked.connect(self._show_editor)
        self._preview_btn.clicked.connect(self._show_preview)

        toggle_layout.addWidget(self._edit_btn)
        toggle_layout.addWidget(self._preview_btn)
        toggle_layout.addStretch()

        layout.addWidget(toggle_bar)

        # Stacked widget for editor / preview
        self._stack = QStackedWidget()
        self._stack.addWidget(self.editor_widget)

        # Preview web view
        self._web_view = QWebEngineView()
        self._web_page = _ExternalLinkPage(self._web_view)
        self._web_page.javaScriptConsoleMessage = self._on_js_console  # type: ignore[assignment]
        self._web_view.setPage(self._web_page)
        self._stack.addWidget(self._web_view)

        self._stack.setCurrentIndex(0)
        layout.addWidget(self._stack)

        # Keyboard shortcut: Ctrl+Shift+P to toggle
        self._toggle_shortcut = QShortcut(QKeySequence("Ctrl+Shift+P"), self)
        self._toggle_shortcut.activated.connect(self._toggle_view)

    def _button_style(self, active: bool) -> str:
        if active:
            return """
                QPushButton {
                    background: #ffffff;
                    border: 1px solid #d0d0d0;
                    border-bottom: 1px solid #ffffff;
                    border-radius: 4px 4px 0 0;
                    padding: 2px 14px;
                    font-size: 13px;
                    font-weight: bold;
                    color: #24292e;
                }
            """
        return """
            QPushButton {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 4px 4px 0 0;
                padding: 2px 14px;
                font-size: 13px;
                color: #586069;
            }
            QPushButton:hover {
                background: #e1e4e8;
            }
        """

    def _connect_signals(self) -> None:
        """Track when editor content changes to know preview is stale."""
        from forge.ui.editor_widget import EditorWidget

        if isinstance(self.editor_widget, EditorWidget):
            self.editor_widget.editor.textChanged.connect(self._on_text_changed)

    def _on_text_changed(self) -> None:
        self._preview_dirty = True
        self.text_changed.emit()

    def _show_editor(self) -> None:
        self._stack.setCurrentIndex(0)
        self._edit_btn.setStyleSheet(self._button_style(active=True))
        self._preview_btn.setStyleSheet(self._button_style(active=False))

    def _show_preview(self) -> None:
        if self._preview_dirty:
            self._refresh_preview()
            self._preview_dirty = False
        self._stack.setCurrentIndex(1)
        self._preview_btn.setStyleSheet(self._button_style(active=True))
        self._edit_btn.setStyleSheet(self._button_style(active=False))

    def _toggle_view(self) -> None:
        if self._stack.currentIndex() == 0:
            self._show_preview()
        else:
            self._show_editor()

    def _refresh_preview(self) -> None:
        from forge.ui.editor_widget import EditorWidget

        text = self.editor_widget.get_text() if isinstance(self.editor_widget, EditorWidget) else ""

        html_content = _build_preview_html(text)

        # Write to temp file and load via setUrl() — avoids QWebEngine's ~2MB setHtml() limit
        preview_file = _PREVIEW_DIR / "preview.html"
        preview_file.write_text(html_content, encoding="utf-8")
        self._web_view.setUrl(QUrl.fromLocalFile(str(preview_file)))

    @staticmethod
    def _on_js_console(level: int, message: str, line: int, source: str) -> None:
        """Log JS console messages for debugging (only errors)."""
        if level >= 2:  # Only log errors
            print(f"[MD Preview] JS error: {message} (line {line})")

    def is_preview_visible(self) -> bool:
        return self._stack.currentIndex() == 1
