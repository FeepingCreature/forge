"""
Debug window for inspecting LLM API requests and responses.

Shows all requests for the current session with:
- JSON pretty-printing with syntax highlighting
- Cost estimation with caching models
- Diff view showing common prefix with previous request
- Actual vs predicted cost comparison
"""

import json  # noqa: I001
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from forge.llm.request_log import REQUEST_LOG, RequestLogEntry


# Pricing per million tokens (input, output, cached_input)
# These are approximate - actual prices may vary
PRICING_MODELS: dict[str, dict[str, tuple[float, float, float]]] = {
    "No Caching": {
        "anthropic/claude-sonnet-4": (3.0, 15.0, 3.0),  # No discount
        "anthropic/claude-3.5-sonnet": (3.0, 15.0, 3.0),
        "anthropic/claude-3-haiku": (0.25, 1.25, 0.25),
        "openai/gpt-4-turbo": (10.0, 30.0, 10.0),
        "openai/gpt-4o": (2.5, 10.0, 2.5),
        "default": (3.0, 15.0, 3.0),
    },
    "Anthropic Caching": {
        "anthropic/claude-sonnet-4": (3.0, 15.0, 0.30),  # 90% discount on cached
        "anthropic/claude-3.5-sonnet": (3.0, 15.0, 0.30),
        "anthropic/claude-3-haiku": (0.25, 1.25, 0.025),
        "default": (3.0, 15.0, 0.30),
    },
    "OpenAI Caching": {
        "openai/gpt-4-turbo": (10.0, 30.0, 5.0),  # 50% discount
        "openai/gpt-4o": (2.5, 10.0, 1.25),
        "default": (10.0, 30.0, 5.0),
    },
}


class JsonHighlighter(QSyntaxHighlighter):
    """Simple JSON syntax highlighter."""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)

        # Formats
        self.key_format = QTextCharFormat()
        self.key_format.setForeground(QColor("#0550ae"))  # Blue for keys
        self.key_format.setFontWeight(QFont.Weight.Bold)

        self.string_format = QTextCharFormat()
        self.string_format.setForeground(QColor("#0a3069"))  # Dark blue for strings

        self.number_format = QTextCharFormat()
        self.number_format.setForeground(QColor("#0550ae"))  # Blue for numbers

        self.bool_null_format = QTextCharFormat()
        self.bool_null_format.setForeground(QColor("#cf222e"))  # Red for bool/null

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        """Apply highlighting to a block of text."""
        # Keys (before colon)
        for match in re.finditer(r'"([^"]+)"(?=\s*:)', text):
            self.setFormat(match.start(), match.end() - match.start(), self.key_format)

        # Strings (after colon or in arrays)
        for match in re.finditer(r':\s*"([^"]*)"', text):
            start = match.start() + text[match.start() :].index('"')
            end = match.end()
            self.setFormat(start, end - start, self.string_format)

        # Numbers
        for match in re.finditer(r":\s*(-?\d+\.?\d*)", text):
            num_start = match.start(1)
            self.setFormat(num_start, match.end(1) - num_start, self.number_format)

        # Booleans and null
        for match in re.finditer(r"\b(true|false|null)\b", text):
            self.setFormat(match.start(), match.end() - match.start(), self.bool_null_format)


class MessageContentWidget(QWidget):
    """Widget showing a single message with role header and content."""

    def __init__(self, message: dict[str, Any], is_cached: bool = False) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        # Role header
        role = message.get("role", "unknown")
        header = QLabel(f"<b>{role.upper()}</b>")

        # Color code by role
        colors = {
            "system": "#6e7781",
            "user": "#0550ae",
            "assistant": "#1a7f37",
            "tool": "#8250df",
        }
        color = colors.get(role, "#000000")
        bg_color = "#f0f8ff" if is_cached else "#ffffff"
        header.setStyleSheet(f"color: {color}; background-color: {bg_color}; padding: 3px;")
        layout.addWidget(header)

        # Content
        content = message.get("content", "")
        if isinstance(content, list):
            # Multi-part content (e.g., tool results)
            content = json.dumps(content, indent=2)
        elif not isinstance(content, str):
            content = json.dumps(content, indent=2)

        # Truncate very long content for display
        max_len = 5000
        if len(content) > max_len:
            content = content[:max_len] + f"\n... ({len(content) - max_len} more chars)"

        text_edit = QPlainTextEdit()
        text_edit.setPlainText(content)
        text_edit.setReadOnly(True)
        text_edit.setMaximumHeight(200)

        if is_cached:
            text_edit.setStyleSheet("background-color: #f0fff0;")  # Light green for cached

        layout.addWidget(text_edit)

        # Tool calls if present
        tool_calls = message.get("tool_calls", [])
        if tool_calls:
            tools_label = QLabel(f"<b>Tool calls: {len(tool_calls)}</b>")
            layout.addWidget(tools_label)
            for tc in tool_calls[:5]:  # Show max 5
                func = tc.get("function", {})
                name = func.get("name", "?")
                args_preview = func.get("arguments", "")[:100]
                tc_label = QLabel(f"  • {name}: {args_preview}...")
                tc_label.setStyleSheet("color: #8250df;")
                layout.addWidget(tc_label)


class RequestDetailWidget(QWidget):
    """Widget showing full details of a request/response pair."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)

        # Tabs for different views
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # Messages view (structured)
        self.messages_widget = QWidget()
        self.messages_layout = QVBoxLayout(self.messages_widget)
        self.tabs.addTab(self.messages_widget, "Messages")

        # Raw request JSON
        self.request_text = QPlainTextEdit()
        self.request_text.setReadOnly(True)
        self.request_text.setFont(QFont("Monospace", 10))
        self.request_highlighter = JsonHighlighter(self.request_text.document())
        self.tabs.addTab(self.request_text, "Raw Request")

        # Raw response JSON
        self.response_text = QPlainTextEdit()
        self.response_text.setReadOnly(True)
        self.response_text.setFont(QFont("Monospace", 10))
        self.response_highlighter = JsonHighlighter(self.response_text.document())
        self.tabs.addTab(self.response_text, "Raw Response")

        # Diff view
        self.diff_widget = QWidget()
        self.diff_layout = QVBoxLayout(self.diff_widget)
        self.diff_info = QLabel()
        self.diff_layout.addWidget(self.diff_info)
        self.tabs.addTab(self.diff_widget, "Diff from Previous")

        # Cost analysis
        self.cost_widget = QWidget()
        self.cost_layout = QVBoxLayout(self.cost_widget)
        self.cost_info = QLabel()
        self.cost_layout.addWidget(self.cost_info)
        self.tabs.addTab(self.cost_widget, "Cost Analysis")

    def clear(self) -> None:
        """Clear all content."""
        # Clear messages
        while self.messages_layout.count():
            item = self.messages_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.request_text.clear()
        self.response_text.clear()
        self.diff_info.clear()
        self.cost_info.clear()

    def show_entry(
        self,
        entry: RequestLogEntry,
        prev_entry: RequestLogEntry | None,
        pricing_model: str,
    ) -> None:
        """Display a request/response entry."""
        self.clear()

        # Load request
        request_data: dict[str, Any] = {}
        if Path(entry.request_file).exists():
            request_data = json.loads(Path(entry.request_file).read_text())
            self.request_text.setPlainText(json.dumps(request_data, indent=2))

        # Load response
        response_data: dict[str, Any] = {}
        if entry.response_file and Path(entry.response_file).exists():
            response_data = json.loads(Path(entry.response_file).read_text())
            self.response_text.setPlainText(json.dumps(response_data, indent=2))

        # Show messages with caching info
        messages = request_data.get("messages", [])
        prev_messages: list[dict[str, Any]] = []
        if prev_entry and Path(prev_entry.request_file).exists():
            prev_data = json.loads(Path(prev_entry.request_file).read_text())
            prev_messages = prev_data.get("messages", [])

        # Find common prefix
        common_prefix_len = 0
        for i, (cur, prev) in enumerate(zip(messages, prev_messages, strict=False)):
            if json.dumps(cur, sort_keys=True) == json.dumps(prev, sort_keys=True):
                common_prefix_len = i + 1
            else:
                break

        # Add message widgets
        for i, msg in enumerate(messages):
            is_cached = i < common_prefix_len
            widget = MessageContentWidget(msg, is_cached)
            self.messages_layout.addWidget(widget)

        self.messages_layout.addStretch()

        # Diff info
        if prev_entry:
            self.diff_info.setText(
                f"<b>Common prefix with previous request:</b> {common_prefix_len}/{len(messages)} messages\n"
                f"<span style='color: green;'>Green = cached (reused from previous)</span>"
            )
        else:
            self.diff_info.setText("<i>No previous request to compare</i>")

        # Cost analysis
        self._show_cost_analysis(
            entry, request_data, response_data, common_prefix_len, pricing_model
        )

    def _show_cost_analysis(
        self,
        entry: RequestLogEntry,
        request_data: dict[str, Any],
        response_data: dict[str, Any],
        cached_messages: int,
        pricing_model: str,
    ) -> None:
        """Show cost analysis for this request."""
        model = entry.model or request_data.get("model", "")
        prices = PRICING_MODELS.get(pricing_model, PRICING_MODELS["No Caching"])
        input_price, output_price, cached_price = prices.get(
            model, prices.get("default", (3.0, 15.0, 0.30))
        )

        # Estimate tokens (rough: 4 chars per token)
        messages = request_data.get("messages", [])

        # Calculate cached vs uncached input tokens
        cached_chars = sum(
            len(json.dumps(m)) for i, m in enumerate(messages) if i < cached_messages
        )
        uncached_chars = sum(
            len(json.dumps(m)) for i, m in enumerate(messages) if i >= cached_messages
        )

        cached_tokens = cached_chars // 4
        uncached_tokens = uncached_chars // 4

        # Output tokens from response
        output_tokens = 0
        if entry.streaming:
            chunks = response_data.get("chunks", [])
            for chunk in chunks:
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    output_tokens += len(content) // 4
        else:
            choices = response_data.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
                output_tokens = len(content) // 4

        # Calculate predicted cost
        cached_cost = (cached_tokens / 1_000_000) * cached_price
        uncached_cost = (uncached_tokens / 1_000_000) * input_price
        output_cost = (output_tokens / 1_000_000) * output_price
        predicted_cost = cached_cost + uncached_cost + output_cost

        # Compare with actual
        actual_cost = entry.actual_cost
        cost_diff = ""
        if actual_cost is not None:
            diff = actual_cost - predicted_cost
            diff_pct = (diff / predicted_cost * 100) if predicted_cost > 0 else 0
            if abs(diff_pct) > 20:
                cost_diff = f"<br><span style='color: red;'>⚠️ Divergence: {diff_pct:+.1f}%</span>"
            else:
                cost_diff = f"<br><span style='color: green;'>✓ Within 20%: {diff_pct:+.1f}%</span>"

        self.cost_info.setText(
            f"<b>Model:</b> {model}<br>"
            f"<b>Pricing Model:</b> {pricing_model}<br><br>"
            f"<b>Input Tokens:</b><br>"
            f"  • Cached: ~{cached_tokens:,} @ ${cached_price}/M = ${cached_cost:.6f}<br>"
            f"  • Uncached: ~{uncached_tokens:,} @ ${input_price}/M = ${uncached_cost:.6f}<br>"
            f"<b>Output Tokens:</b> ~{output_tokens:,} @ ${output_price}/M = ${output_cost:.6f}<br><br>"
            f"<b>Predicted Cost:</b> ${predicted_cost:.6f}<br>"
            f"<b>Actual Cost:</b> ${actual_cost:.6f if actual_cost else 'N/A'}"
            f"{cost_diff}"
        )


class RequestDebugWindow(QMainWindow):
    """Main debug window for request inspection."""

    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Request Debug")
        self.setGeometry(200, 200, 1200, 800)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Top bar with controls
        controls = QHBoxLayout()

        # Pricing model selector
        controls.addWidget(QLabel("Pricing Model:"))
        self.pricing_combo = QComboBox()
        self.pricing_combo.addItems(list(PRICING_MODELS.keys()))
        self.pricing_combo.currentTextChanged.connect(self._on_pricing_changed)
        controls.addWidget(self.pricing_combo)

        controls.addStretch()

        # Refresh button
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_entries)
        controls.addWidget(refresh_btn)

        layout.addLayout(controls)

        # Main splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # Left: request list
        self.request_list = QListWidget()
        self.request_list.currentItemChanged.connect(self._on_request_selected)
        splitter.addWidget(self.request_list)

        # Right: detail view
        self.detail_widget = RequestDetailWidget()
        splitter.addWidget(self.detail_widget)

        splitter.setSizes([300, 900])

        # Load initial data
        self._refresh_entries()

    def _refresh_entries(self) -> None:
        """Refresh the list of entries."""
        self.request_list.clear()
        entries = REQUEST_LOG.get_entries()

        for i, entry in enumerate(entries):
            # Format timestamp
            ts = datetime.fromtimestamp(entry.timestamp)
            time_str = ts.strftime("%H:%M:%S")

            # Cost indicator
            cost_str = f"${entry.actual_cost:.4f}" if entry.actual_cost else "?"

            # Streaming indicator
            stream_str = "⏳" if entry.streaming else "📦"

            label = f"{stream_str} {time_str} - {cost_str}"

            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, i)
            self.request_list.addItem(item)

        # Select last item
        if self.request_list.count() > 0:
            self.request_list.setCurrentRow(self.request_list.count() - 1)

    def _on_request_selected(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        """Handle request selection."""
        if not current:
            self.detail_widget.clear()
            return

        idx = current.data(Qt.ItemDataRole.UserRole)
        entries = REQUEST_LOG.get_entries()

        if 0 <= idx < len(entries):
            entry = entries[idx]
            prev_entry = entries[idx - 1] if idx > 0 else None
            pricing_model = self.pricing_combo.currentText()
            self.detail_widget.show_entry(entry, prev_entry, pricing_model)

    def _on_pricing_changed(self, _text: str) -> None:
        """Handle pricing model change."""
        # Re-show current entry with new pricing
        current = self.request_list.currentItem()
        if current:
            self._on_request_selected(current, None)

    def closeEvent(self, event: Any) -> None:  # noqa: N802
        """Emit signal when window closes."""
        self.closed.emit()
        super().closeEvent(event)
