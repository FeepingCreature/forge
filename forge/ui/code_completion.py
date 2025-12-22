"""
Code completion using a small, fast LLM model.

Uses Haiku or similar cheap model for inline completions.
"""

import os
from typing import Any

import httpx
from PySide6.QtCore import QObject, QThread, QTimer, Signal


class CompletionWorker(QObject):
    """Worker that fetches completions from a small model."""

    completion_ready = Signal(str, int)  # completion text, cursor position when requested
    error = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._pending_request: dict[str, Any] | None = None

    def request_completion(
        self,
        prefix: str,
        suffix: str,
        cursor_pos: int,
        filepath: str,
    ) -> None:
        """Queue a completion request."""
        self._pending_request = {
            "prefix": prefix,
            "suffix": suffix,
            "cursor_pos": cursor_pos,
            "filepath": filepath,
        }
        self._process_request()

    def _process_request(self) -> None:
        """Process the pending request."""
        if not self._pending_request:
            return

        req = self._pending_request
        self._pending_request = None

        prefix = req["prefix"]
        suffix = req["suffix"]
        cursor_pos = req["cursor_pos"]
        filepath = req["filepath"]

        # Build the prompt
        file_ext = filepath.split(".")[-1] if "." in filepath else ""
        lang_hint = self._get_language_hint(file_ext)

        # Use a fill-in-the-middle style prompt
        prompt = f"""You are a code completion assistant. Complete the code at the cursor position.
Only output the completion text, nothing else. Keep it short (1-3 lines max).
If no completion makes sense, output nothing.

File: {filepath}
Language: {lang_hint}

```
{prefix[-1500:]}█{suffix[:500]}
```

Complete at █:"""

        try:
            api_key = os.environ.get("OPENROUTER_API_KEY", "")
            if not api_key:
                return

            # Use a fast, cheap model for completions
            response = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "HTTP-Referer": "https://github.com/forge-editor/forge",
                },
                json={
                    "model": "anthropic/claude-3-haiku",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 150,
                    "temperature": 0,
                },
                timeout=5.0,
            )

            if response.status_code == 200:
                data = response.json()
                completion = data["choices"][0]["message"]["content"].strip()

                # Clean up the completion
                completion = self._clean_completion(completion, prefix, suffix)

                if completion:
                    self.completion_ready.emit(completion, cursor_pos)
            else:
                self.error.emit(f"API error: {response.status_code}")

        except httpx.TimeoutException:
            pass  # Silently ignore timeouts
        except Exception as e:
            self.error.emit(str(e))

    def _get_language_hint(self, ext: str) -> str:
        """Get language name from file extension."""
        lang_map = {
            "py": "Python",
            "js": "JavaScript",
            "ts": "TypeScript",
            "jsx": "React JSX",
            "tsx": "React TSX",
            "rs": "Rust",
            "go": "Go",
            "java": "Java",
            "c": "C",
            "cpp": "C++",
            "h": "C/C++ Header",
            "rb": "Ruby",
            "php": "PHP",
            "swift": "Swift",
            "kt": "Kotlin",
            "scala": "Scala",
            "sh": "Shell",
            "bash": "Bash",
            "zsh": "Zsh",
            "sql": "SQL",
            "html": "HTML",
            "css": "CSS",
            "scss": "SCSS",
            "json": "JSON",
            "yaml": "YAML",
            "yml": "YAML",
            "toml": "TOML",
            "md": "Markdown",
            "txt": "Text",
        }
        return lang_map.get(ext, "Unknown")

    def _clean_completion(self, completion: str, prefix: str, suffix: str) -> str:
        """Clean up the completion text."""
        # Remove markdown code blocks if present
        if completion.startswith("```"):
            lines = completion.split("\n")
            # Find content between ``` markers
            start = 1
            end = len(lines)
            for i, line in enumerate(lines[1:], 1):
                if line.startswith("```"):
                    end = i
                    break
            completion = "\n".join(lines[start:end])

        # Remove leading/trailing whitespace only if it doesn't match context
        completion = completion.strip()

        # Don't return if it's just repeating what's already there
        if suffix.lstrip().startswith(completion):
            return ""

        return completion


class CompletionManager(QObject):
    """
    Manages code completion for an editor.

    Triggers completion after a delay when the user stops typing.
    Shows ghost text that can be accepted with Tab.
    """

    def __init__(self, editor: Any, filepath: str) -> None:
        super().__init__()
        self.editor = editor
        self.filepath = filepath
        self._enabled = True
        self._ghost_text = ""
        self._ghost_cursor_pos = -1

        # Debounce timer - wait for user to stop typing
        self._debounce_timer = QTimer()
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(500)  # 500ms delay
        self._debounce_timer.timeout.connect(self._request_completion)

        # Worker thread for API calls
        self._worker_thread = QThread()
        self._worker = CompletionWorker()
        self._worker.moveToThread(self._worker_thread)
        self._worker.completion_ready.connect(self._on_completion_ready)
        self._worker_thread.start()

        # Connect to editor signals
        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.cursorPositionChanged.connect(self._on_cursor_moved)

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable completions."""
        self._enabled = enabled
        if not enabled:
            self._clear_ghost_text()

    def accept_completion(self) -> bool:
        """
        Accept the current ghost text completion.
        Returns True if there was a completion to accept.
        """
        if not self._ghost_text:
            return False

        cursor = self.editor.textCursor()
        cursor.insertText(self._ghost_text)
        self._clear_ghost_text()
        return True

    def dismiss_completion(self) -> None:
        """Dismiss the current completion."""
        self._clear_ghost_text()

    def cleanup(self) -> None:
        """Clean up resources."""
        self._worker_thread.quit()
        self._worker_thread.wait()

    def _on_text_changed(self) -> None:
        """Handle text changes - trigger debounced completion request."""
        self._clear_ghost_text()
        if self._enabled:
            self._debounce_timer.start()

    def _on_cursor_moved(self) -> None:
        """Handle cursor movement - clear ghost text if cursor moved away."""
        if self._ghost_text and self.editor.textCursor().position() != self._ghost_cursor_pos:
            self._clear_ghost_text()

    def _request_completion(self) -> None:
        """Request a completion from the worker."""
        if not self._enabled:
            return

        cursor = self.editor.textCursor()
        cursor_pos = cursor.position()
        text = self.editor.toPlainText()

        prefix = text[:cursor_pos]
        suffix = text[cursor_pos:]

        # Don't request if cursor is at start or line is empty
        if not prefix or prefix.endswith("\n\n"):
            return

        self._worker.request_completion(prefix, suffix, cursor_pos, self.filepath)

    def _on_completion_ready(self, completion: str, cursor_pos: int) -> None:
        """Handle completion from worker."""
        # Only show if cursor hasn't moved
        if self.editor.textCursor().position() != cursor_pos:
            return

        self._ghost_text = completion
        self._ghost_cursor_pos = cursor_pos
        self._show_ghost_text()

    def _show_ghost_text(self) -> None:
        """Display ghost text as a tooltip near the cursor."""
        if not self._ghost_text:
            return

        # Get cursor rectangle in editor coordinates
        cursor = self.editor.textCursor()
        cursor_rect = self.editor.cursorRect(cursor)

        # Create/update tooltip
        from PySide6.QtWidgets import QToolTip

        # Show completion as tooltip below cursor
        global_pos = self.editor.mapToGlobal(cursor_rect.bottomLeft())

        # Format the completion text (show first line + indicator if multiline)
        lines = self._ghost_text.split("\n")
        display_text = lines[0]
        if len(lines) > 1:
            display_text += f" (+{len(lines) - 1} lines)"

        tooltip_text = (
            f"<span style='color: #666; font-family: monospace;'>Tab: {display_text}</span>"
        )
        QToolTip.showText(global_pos, tooltip_text, self.editor)

    def _clear_ghost_text(self) -> None:
        """Clear ghost text from the editor."""
        self._ghost_text = ""
        self._ghost_cursor_pos = -1

        from PySide6.QtWidgets import QToolTip

        QToolTip.hideText()
