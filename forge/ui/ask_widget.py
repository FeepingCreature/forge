"""
Ask Widget - Query the codebase using the summary model.

Embeddable version of AskRepoDialog for use in side panel.
Uses file summaries to answer architecture and code questions quickly and cheaply.
"""

from typing import TYPE_CHECKING

import httpx
from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from forge.ui.branch_workspace import BranchWorkspace


class AskWorker(QObject):
    """Worker that queries the model about the codebase."""

    response_ready = Signal(str)
    error = Signal(str)
    chunk_received = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._question = ""
        self._summaries = ""
        self._api_key = ""

    def set_query(self, question: str, summaries: str, api_key: str) -> None:
        """Set the query to execute."""
        self._question = question
        self._summaries = summaries
        self._api_key = api_key

    def run(self) -> None:
        """Execute the query."""
        prompt = f"""You are a code assistant. Answer the user's question about this codebase based on the file summaries below.

Be concise but helpful. If you can identify specific files that are relevant, mention them.
If you're not sure, say so.

## File Summaries

{self._summaries}

## Question

{self._question}

## Answer"""

        try:
            if not self._api_key:
                self.error.emit("No API key configured")
                return

            # Use a fast, cheap model
            with httpx.stream(
                "POST",
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "HTTP-Referer": "https://github.com/anthropics/forge",
                },
                json={
                    "model": "anthropic/claude-3-haiku",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1000,
                    "temperature": 0.3,
                    "stream": True,
                },
                timeout=30.0,
            ) as response:
                if response.status_code != 200:
                    self.error.emit(f"API error: {response.status_code}")
                    return

                full_response = ""
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            import json

                            chunk = json.loads(data)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                full_response += content
                                self.chunk_received.emit(content)
                        except Exception:
                            pass

                self.response_ready.emit(full_response)

        except httpx.TimeoutException:
            self.error.emit("Request timed out")
        except Exception as e:
            self.error.emit(str(e))


class AskWidget(QWidget):
    """Widget for asking questions about the codebase."""

    def __init__(
        self, workspace: "BranchWorkspace", api_key: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self._api_key = api_key

        self._setup_ui()
        self._setup_worker()

    def _setup_ui(self) -> None:
        """Setup the widget UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Instructions
        label = QLabel("Ask about the codebase (uses Haiku):")
        label.setStyleSheet("font-size: 11px; color: #666;")
        layout.addWidget(label)

        # Question input
        self.question_input = QLineEdit()
        self.question_input.setPlaceholderText("How does X work?")
        self.question_input.returnPressed.connect(self._ask)
        layout.addWidget(self.question_input)

        # Ask button
        self.ask_button = QPushButton("Ask")
        self.ask_button.clicked.connect(self._ask)
        layout.addWidget(self.ask_button)

        # Response area
        self.response_area = QTextEdit()
        self.response_area.setReadOnly(True)
        self.response_area.setPlaceholderText("Response will appear here...")
        layout.addWidget(self.response_area)

    def _setup_worker(self) -> None:
        """Setup the background worker."""
        self._worker_thread = QThread()
        self._worker = AskWorker()
        self._worker.moveToThread(self._worker_thread)
        self._worker.chunk_received.connect(self._on_chunk)
        self._worker.response_ready.connect(self._on_response)
        self._worker.error.connect(self._on_error)
        self._worker_thread.started.connect(self._worker.run)

    def focus_input(self) -> None:
        """Focus the question input"""
        self.question_input.setFocus()
        self.question_input.selectAll()

    def _ask(self) -> None:
        """Submit the question."""
        question = self.question_input.text().strip()
        if not question:
            return

        # Disable input while processing
        self.question_input.setEnabled(False)
        self.ask_button.setEnabled(False)
        self.response_area.clear()
        self.response_area.setPlaceholderText("Thinking...")

        # Get file summaries from workspace
        summaries = self._get_summaries()

        # Setup and start worker
        self._worker.set_query(question, summaries, self._api_key)

        # Need to recreate thread if it was already run
        if self._worker_thread.isFinished():
            self._worker_thread = QThread()
            self._worker.moveToThread(self._worker_thread)
            self._worker_thread.started.connect(self._worker.run)

        self._worker_thread.start()

    def _get_summaries(self) -> str:
        """Get file summaries from the workspace."""
        vfs = self.workspace.vfs
        files = vfs.list_files()

        # Build a simple summary string
        lines = []
        for filepath in sorted(files):
            # Skip binary/non-code files
            if any(
                filepath.endswith(ext)
                for ext in [".png", ".jpg", ".gif", ".ico", ".pyc", ".so", ".whl"]
            ):
                continue
            if filepath.startswith(".git/"):
                continue

            lines.append(f"- {filepath}")

        return "\n".join(lines)

    def _on_chunk(self, chunk: str) -> None:
        """Handle streaming chunk."""
        cursor = self.response_area.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(chunk)
        self.response_area.setTextCursor(cursor)

    def _on_response(self, response: str) -> None:
        """Handle complete response."""
        self.question_input.setEnabled(True)
        self.ask_button.setEnabled(True)
        self._worker_thread.quit()

    def _on_error(self, error: str) -> None:
        """Handle error."""
        self.response_area.setPlainText(f"Error: {error}")
        self.question_input.setEnabled(True)
        self.ask_button.setEnabled(True)
        self._worker_thread.quit()
