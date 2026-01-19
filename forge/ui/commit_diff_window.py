"""
Commit diff window - displays the diff for a single commit.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from forge.git_backend.repository import ForgeRepository


class CommitDiffWindow(QMainWindow):
    """Window showing the diff for a specific commit."""

    def __init__(
        self,
        repo: ForgeRepository,
        commit_oid: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.repo = repo
        self.commit_oid = commit_oid

        self._setup_ui()
        self._load_commit()

    def _setup_ui(self) -> None:
        """Setup the window UI."""
        self.setWindowTitle("Commit Diff")
        self.resize(900, 700)

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)

        # Header with commit info
        self._header = QLabel()
        self._header.setWordWrap(True)
        self._header.setStyleSheet("""
            QLabel {
                background: #f5f5f5;
                border: 1px solid #ddd;
                border-radius: 4px;
                padding: 8px;
                font-size: 12px;
            }
        """)
        layout.addWidget(self._header)

        # Splitter: file list on left, diff on right
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        # File list
        self._file_tree = QTreeWidget()
        self._file_tree.setHeaderLabels(["Files Changed"])
        self._file_tree.setMinimumWidth(200)
        self._file_tree.setMaximumWidth(300)
        self._file_tree.itemClicked.connect(self._on_file_clicked)
        splitter.addWidget(self._file_tree)

        # Diff view
        diff_container = QWidget()
        diff_layout = QVBoxLayout(diff_container)
        diff_layout.setContentsMargins(0, 0, 0, 0)

        self._diff_label = QLabel("Select a file to view diff")
        self._diff_label.setStyleSheet("color: #666; padding: 8px;")
        diff_layout.addWidget(self._diff_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._diff_view = QTextEdit()
        self._diff_view.setReadOnly(True)
        self._diff_view.setFont(QFont("monospace", 10))
        self._diff_view.setStyleSheet("""
            QTextEdit {
                background: #fafafa;
                border: 1px solid #ddd;
            }
        """)
        scroll.setWidget(self._diff_view)
        diff_layout.addWidget(scroll, 1)

        splitter.addWidget(diff_container)
        splitter.setSizes([250, 650])

        # Store diffs by filepath
        self._file_diffs: dict[str, str] = {}

    def _load_commit(self) -> None:
        """Load and display the commit."""
        import pygit2

        commit = self.repo.repo.revparse_single(self.commit_oid)
        assert isinstance(commit, pygit2.Commit)

        # Set window title
        self.setWindowTitle(
            f"Diff: {self.commit_oid[:7]} - {commit.message.split(chr(10))[0][:50]}"
        )

        # Header info
        author = commit.author
        from datetime import datetime

        commit_time = datetime.fromtimestamp(commit.commit_time)
        header_text = (
            f"<b>{self.commit_oid[:12]}</b><br>"
            f"<b>Author:</b> {author.name} &lt;{author.email}&gt;<br>"
            f"<b>Date:</b> {commit_time.strftime('%Y-%m-%d %H:%M:%S')}<br>"
            f"<b>Message:</b> {commit.message.strip()}"
        )
        self._header.setText(header_text)

        # Get diff
        if commit.parents:
            parent = commit.parents[0]
            diff = self.repo.repo.diff(parent, commit)
        else:
            # Initial commit - diff against empty tree
            diff = commit.tree.diff_to_tree()

        # Populate file list and store diffs
        self._file_tree.clear()
        self._file_diffs = {}

        for patch in diff:
            filepath = patch.delta.new_file.path or patch.delta.old_file.path

            # Determine status
            status = patch.delta.status
            status_char = {
                pygit2.enums.DeltaStatus.ADDED: "A",
                pygit2.enums.DeltaStatus.DELETED: "D",
                pygit2.enums.DeltaStatus.MODIFIED: "M",
                pygit2.enums.DeltaStatus.RENAMED: "R",
                pygit2.enums.DeltaStatus.COPIED: "C",
            }.get(status, "?")

            # Create tree item
            item = QTreeWidgetItem([f"[{status_char}] {filepath}"])
            item.setData(0, Qt.ItemDataRole.UserRole, filepath)

            # Color by status
            color_map = {
                "A": "#2e7d32",  # Green
                "D": "#c62828",  # Red
                "M": "#1565c0",  # Blue
                "R": "#7b1fa2",  # Purple
            }
            if status_char in color_map:
                item.setForeground(0, QColor(color_map[status_char]))

            self._file_tree.addTopLevelItem(item)

            # Store diff text
            self._file_diffs[filepath] = patch.text or ""

        # Show full diff initially
        if diff:
            full_diff = diff.patch or ""
            self._show_diff(full_diff, "Full Diff")

    def _on_file_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Handle file selection."""
        filepath = item.data(0, Qt.ItemDataRole.UserRole)
        if filepath and filepath in self._file_diffs:
            self._show_diff(self._file_diffs[filepath], filepath)

    def _show_diff(self, diff_text: str, title: str) -> None:
        """Display a diff with syntax highlighting."""
        self._diff_label.setText(title)

        # Apply simple syntax highlighting
        html_lines = []
        for line in diff_text.split("\n"):
            escaped = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

            if line.startswith("+") and not line.startswith("+++"):
                html_lines.append(
                    f'<span style="background:#e6ffe6;color:#2e7d32">{escaped}</span>'
                )
            elif line.startswith("-") and not line.startswith("---"):
                html_lines.append(
                    f'<span style="background:#ffe6e6;color:#c62828">{escaped}</span>'
                )
            elif line.startswith("@@"):
                html_lines.append(f'<span style="color:#7b1fa2;font-weight:bold">{escaped}</span>')
            elif line.startswith("diff ") or line.startswith("index "):
                html_lines.append(f'<span style="color:#666">{escaped}</span>')
            else:
                html_lines.append(escaped)

        html = f'<pre style="margin:0;padding:8px;font-family:monospace;">{"<br>".join(html_lines)}</pre>'
        self._diff_view.setHtml(html)
