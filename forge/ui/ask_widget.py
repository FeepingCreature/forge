"""
Ask Widget - Query the codebase using the summary model.

Embeddable version of AskRepoDialog for use in side panel.
Uses file summaries to answer architecture and code questions quickly and cheaply.
"""

import re
from typing import TYPE_CHECKING

import httpx
from PySide6.QtCore import QObject, QThread, QUrl, Signal
from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from forge.vfs.base import VFS

if TYPE_CHECKING:
    from forge.ui.branch_workspace import BranchWorkspace


class AskWorker(QObject):
    """Worker that queries the model about the codebase using a two-step process.

    Step 1: Given summaries, identify which files are relevant to the question
    Step 2: Fetch those files and answer the question with full context
    """

    response_ready = Signal(str)
    error = Signal(str)
    chunk_received = Signal(str)
    status_update = Signal(str)  # For showing "Identifying relevant files..." etc.

    def __init__(self) -> None:
        super().__init__()
        self._question = ""
        self._summaries = ""
        self._api_key = ""
        self._vfs: VFS | None = None

    def set_query(
        self, question: str, summaries: str, api_key: str, vfs: VFS | None = None
    ) -> None:
        """Set the query to execute."""
        self._question = question
        self._summaries = summaries
        self._api_key = api_key
        self._vfs = vfs

    def _call_llm(self, prompt: str, stream: bool = False) -> str:
        """Make a non-streaming LLM call."""
        response = httpx.post(
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
            },
            timeout=30.0,
        )
        if response.status_code != 200:
            raise Exception(f"API error: {response.status_code}")

        data = response.json()
        return str(data["choices"][0]["message"]["content"])

    def _stream_llm(self, prompt: str) -> None:
        """Make a streaming LLM call, emitting chunks."""
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
                "max_tokens": 2000,
                "temperature": 0.3,
                "stream": True,
            },
            timeout=60.0,
        ) as response:
            if response.status_code != 200:
                raise Exception(f"API error: {response.status_code}")

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

    def run(self) -> None:
        """Execute the two-step query process."""
        try:
            if not self._api_key:
                self.error.emit("No API key configured")
                return

            if not self._summaries or self._summaries.startswith("(No summaries"):
                self.error.emit(
                    "No file summaries available yet. Please wait for summary generation to complete."
                )
                return

            # Step 1: Identify relevant files
            self.status_update.emit("🔍 Identifying relevant files...")

            step1_prompt = f"""You are a code assistant. Given the user's question and file summaries, identify which files would need to be examined to answer the question.

## File Summaries

{self._summaries}

## Question

{self._question}

## Task

List the file paths that are most relevant to answering this question. Output ONLY a JSON array of file paths, nothing else. Example: ["forge/ui/main.py", "forge/tools/manager.py"]

If no files seem relevant, output an empty array: []"""

            files_response = self._call_llm(step1_prompt)

            # Parse the file list
            import json
            import re

            # Extract JSON array from response (handle markdown code blocks)
            json_match = re.search(r"\[.*?\]", files_response, re.DOTALL)
            if json_match:
                try:
                    relevant_files = json.loads(json_match.group())
                except json.JSONDecodeError:
                    relevant_files = []
            else:
                relevant_files = []

            # Step 2: Fetch file contents and answer
            file_contents = ""
            if relevant_files and self._vfs:
                self.status_update.emit(f"📂 Loading {len(relevant_files)} file(s)...")

                for filepath in relevant_files:
                    try:
                        content = self._vfs.read_file(filepath)
                        lines = content.split("\n")
                        # Add line numbers
                        numbered_lines = [f"{i + 1:4d} | {line}" for i, line in enumerate(lines)]
                        numbered_content = "\n".join(numbered_lines)
                        file_contents += f"\n## {filepath}\n```\n{numbered_content}\n```\n"
                    except Exception:
                        pass  # File doesn't exist or can't be read

            self.status_update.emit("💭 Generating answer...")

            # Step 3: Answer with full context
            if file_contents:
                step2_prompt = f"""You are a code assistant. Answer the user's question using the file contents below.

Guidelines:
- Be concise. Reference code by linking to line numbers instead of quoting it.
- In your prose (NOT in code blocks), use: `filepath:LINE` or `filepath:START-END`
- Example: "The main loop is in `forge/ui/main_window.py:42-58`"
- These become clickable links. Never put links inside code blocks.

## Relevant Files (with line numbers)

{file_contents}

## Question

{self._question}

## Answer"""
            else:
                # No files to fetch, answer from summaries only
                step2_prompt = f"""You are a code assistant. Answer the user's question based on the file summaries below.

Be concise but helpful. When referencing files, use the EXACT full path from the summaries (e.g., `forge/ui/main_window.py` not just `main_window.py`). These paths become clickable links.

## File Summaries

{self._summaries}

## Question

{self._question}

## Answer"""

            self._stream_llm(step2_prompt)

        except httpx.TimeoutException:
            self.error.emit("Request timed out")
        except Exception as e:
            self.error.emit(str(e))


class AskWidget(QWidget):
    """Widget for asking questions about the codebase."""

    # Emitted when user clicks a file link (filepath, start_line, end_line)
    # For single line links, start_line == end_line
    file_selected = Signal(str, int, int)

    def __init__(
        self, workspace: "BranchWorkspace", api_key: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self._api_key = api_key
        self._raw_response = ""  # Store raw response for link conversion
        self._all_files: set[str] = set()  # Cache of all files for link detection
        self._repo_summaries: dict[str, str] = {}  # Set by parent when available

        self._setup_ui()
        self._setup_worker()

    def set_summaries(self, summaries: dict[str, str]) -> None:
        """Set the repository summaries (called by parent when available)."""
        self._repo_summaries = summaries

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

        # Response area - use QTextBrowser for clickable links
        self.response_area = QTextBrowser()
        self.response_area.setReadOnly(True)
        self.response_area.setOpenLinks(False)  # Handle links ourselves
        self.response_area.anchorClicked.connect(self._on_link_clicked)
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
        self._worker.status_update.connect(self._on_status)
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
        self._raw_response = ""

        # Cache file list for link detection
        self._all_files = set(self.workspace.vfs.list_files())

        # Get file summaries from workspace
        summaries = self._get_summaries()

        # Setup and start worker (pass VFS for file content fetching)
        self._worker.set_query(question, summaries, self._api_key, self.workspace.vfs)

        # Need to recreate thread if it was already run
        if self._worker_thread.isFinished():
            self._worker_thread = QThread()
            self._worker.moveToThread(self._worker_thread)
            self._worker_thread.started.connect(self._worker.run)

        self._worker_thread.start()

    def _get_summaries(self) -> str:
        """Get file summaries (set by parent via set_summaries)."""
        if not self._repo_summaries:
            return "(No summaries available yet - please wait for summary generation to complete)"

        lines = []
        for filepath in sorted(self._repo_summaries.keys()):
            summary = self._repo_summaries[filepath]
            # Format: ## filepath\nsummary
            lines.append(f"## {filepath}\n{summary}")

        return "\n".join(lines)

    def _on_chunk(self, chunk: str) -> None:
        """Handle streaming chunk."""
        self._raw_response += chunk
        # During streaming, just show plain text
        self.response_area.setPlainText(self._raw_response)
        # Scroll to bottom
        scrollbar = self.response_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _on_response(self, response: str) -> None:
        """Handle complete response."""
        self._raw_response = response
        # Convert file references to clickable links
        html = self._convert_to_linked_html(response)
        self.response_area.setHtml(html)

        self.question_input.setEnabled(True)
        self.ask_button.setEnabled(True)
        self._worker_thread.quit()

    def _convert_to_linked_html(self, text: str) -> str:
        """Convert file references in text to clickable HTML links."""
        import html

        # Escape HTML first
        escaped = html.escape(text)

        # Pattern to match file paths with optional line numbers
        # Matches: `filepath:line`, `filepath`, or backtick-wrapped versions
        # Sort files by length (longest first) to avoid partial matches
        sorted_files = sorted(self._all_files, key=len, reverse=True)

        for filepath in sorted_files:
            escaped_path = html.escape(filepath)
            # Match filepath with optional line or range (with optional backticks)
            # Patterns: `filepath:42`, `filepath:42-58`, `filepath`, or without backticks
            pattern = re.compile(
                r"`?" + re.escape(escaped_path) + r"(?::(\d+)(?:-(\d+))?)?`?", re.IGNORECASE
            )

            # Capture filepath in closure properly
            def make_link(match: re.Match[str], fp: str = filepath) -> str:
                start_line = match.group(1) or "1"
                end_line = match.group(2)  # May be None
                if end_line:
                    display = f"{fp}:{start_line}-{end_line}"
                elif match.group(1):
                    display = f"{fp}:{start_line}"
                else:
                    display = fp
                # Use forge: scheme with query params for line range
                # Format: forge:filepath?line=N or forge:filepath?line=N&end=M
                if end_line:
                    url = f"forge:{fp}?line={start_line}&end={end_line}"
                else:
                    url = f"forge:{fp}?line={start_line}"
                return f'<a href="{url}" style="color: #0066cc;">{display}</a>'

            escaped = pattern.sub(make_link, escaped)

        # Convert newlines to <br> and wrap in basic styling
        escaped = escaped.replace("\n", "<br>")
        return f'<div style="font-family: sans-serif; font-size: 13px; line-height: 1.4;">{escaped}</div>'

    def _on_link_clicked(self, url: QUrl) -> None:
        """Handle click on a file link."""
        if url.scheme() == "forge":
            # Custom scheme: forge:filepath?line=N or forge:filepath?line=N&end=M
            # path() contains the filepath, query params have line numbers
            filepath = url.path()
            # Remove leading slash if present
            if filepath.startswith("/"):
                filepath = filepath[1:]

            # Get line range from query parameters
            from PySide6.QtCore import QUrlQuery

            query = QUrlQuery(url)
            start_str = query.queryItemValue("line")
            end_str = query.queryItemValue("end")
            start_line = int(start_str) if start_str else 1
            end_line = int(end_str) if end_str else start_line

            self.file_selected.emit(filepath, start_line, end_line)

    def _on_status(self, status: str) -> None:
        """Handle status update from worker."""
        self.response_area.setPlainText(status)

    def _on_error(self, error: str) -> None:
        """Handle error."""
        self.response_area.setPlainText(f"Error: {error}")
        self.question_input.setEnabled(True)
        self.ask_button.setEnabled(True)
        self._worker_thread.quit()
