"""
Mood bar widget for visualizing prompt token usage with color-coded segments.
"""

from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QColor, QHelpEvent, QPainter, QPainterPath, QPaintEvent
from PySide6.QtWidgets import QToolTip, QWidget

# Color scheme for different message/block types
MOOD_COLORS = {
    "system": "#6366f1",  # Indigo - system prompt
    "summaries": "#8b5cf6",  # Violet - file summaries
    "file": "#06b6d4",  # Cyan - file contents
    "user": "#f59e0b",  # Amber - user messages
    "assistant": "#10b981",  # Emerald - assistant messages
    "tool_call": "#ec4899",  # Pink - tool calls
    "tool_result": "#f97316",  # Orange - tool results
    "empty": "#374151",  # Gray - unused space
}


class MoodBar(QWidget):
    """
    A horizontal bar widget that shows token usage with colored segments.

    Each segment represents a prompt block (system, summaries, files, messages)
    with proportional widths based on token counts.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._segments: list[dict] = []
        self._total_tokens: int = 0
        self._segment_rects: list[tuple[QRect, dict]] = []  # For hit testing

        # Empty/unused color
        self._empty_color = QColor(MOOD_COLORS["empty"])

        # Tick mark settings
        self._tick_interval = 10_000  # Tokens between big marks
        self._tick_color = QColor(0, 0, 0, 178)  # 70% opacity black triangles

        # Enable mouse tracking for tooltips
        self.setMouseTracking(True)

        # Set fixed height for the bar
        self.setFixedHeight(24)

    def set_segments(self, segments: list[dict]) -> None:
        """Set segments to display.

        Each segment dict has:
        - name: str (e.g., "User message", "Assistant response")
        - type: str (system/summaries/file/user/assistant/tool_call/tool_result)
        - tokens: int
        - details: str (optional, content preview for tooltip)
        """
        self._segments = segments
        self._total_tokens = sum(s.get("tokens", 0) for s in segments)
        self._segment_rects = []  # Will be recalculated on paint
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """Draw the colored segments and tick marks."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        rect = self.rect()
        width = rect.width()
        height = rect.height()

        # Clear segment rects for hit testing
        self._segment_rects = []

        if self._total_tokens == 0:
            # Draw empty bar
            painter.fillRect(rect, self._empty_color)
            return

        # Draw colored segments — always fill full width
        x = 0.0
        for segment in self._segments:
            tokens = segment.get("tokens", 0)
            if tokens == 0:
                continue

            proportion = tokens / self._total_tokens
            segment_width = width * proportion

            if segment_width < 1 and tokens > 0:
                segment_width = 1

            seg_type = segment.get("type", "empty")
            color = QColor(MOOD_COLORS.get(seg_type, MOOD_COLORS["empty"]))

            segment_rect = QRect(int(x), 0, max(1, int(segment_width)), height)
            painter.fillRect(segment_rect, color)
            self._segment_rects.append((segment_rect, segment))

            x += segment_width

        # Fill any rounding remainder
        if int(x) < width:
            painter.fillRect(QRect(int(x), 0, width - int(x), height), self._empty_color)

        # Draw triangular tick marks at 10k token intervals
        if self._total_tokens >= self._tick_interval:
            tri_size = 5  # Triangle size in pixels
            alpha = 0.7  # Blend factor (0=segment color, 1=black)

            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)

            tick_tokens = self._tick_interval
            while tick_tokens < self._total_tokens:
                tick_x = float(width * tick_tokens / self._total_tokens)

                # Find the segment color under this tick to blend against
                bg = self._empty_color
                for seg_rect, seg in self._segment_rects:
                    if seg_rect.left() <= int(tick_x) < seg_rect.right():
                        bg = QColor(MOOD_COLORS.get(seg.get("type", "empty"), MOOD_COLORS["empty"]))
                        break

                # Pre-blend: mix black into the background color (works without compositor)
                blended = QColor(
                    int(bg.red() * (1 - alpha)),
                    int(bg.green() * (1 - alpha)),
                    int(bg.blue() * (1 - alpha)),
                )
                print(f"TICK {tick_tokens}: bg=({bg.red()},{bg.green()},{bg.blue()}) blended=({blended.red()},{blended.green()},{blended.blue()}) alpha={blended.alpha()}")

                # Top triangle pointing down
                path = QPainterPath()
                path.moveTo(tick_x - tri_size, 0)
                path.lineTo(tick_x + tri_size, 0)
                path.lineTo(tick_x, tri_size)
                path.closeSubpath()
                painter.fillPath(path, blended)

                # Bottom triangle pointing up
                path = QPainterPath()
                path.moveTo(tick_x - tri_size, height)
                path.lineTo(tick_x + tri_size, height)
                path.lineTo(tick_x, height - tri_size)
                path.closeSubpath()
                painter.fillPath(path, blended)

                tick_tokens += self._tick_interval

    def mouseMoveEvent(self, event: QEvent) -> None:  # noqa: N802
        """Show tooltip immediately on mouse move (no delay)."""
        from PySide6.QtGui import QMouseEvent

        if not isinstance(event, QMouseEvent):
            return

        pos = event.pos()
        for segment_rect, segment in self._segment_rects:
            if segment_rect.contains(pos):
                name = segment.get("name", "Unknown")
                tokens = segment.get("tokens", 0)
                details = segment.get("details", "")

                # Calculate percentage
                percent = (tokens / self._total_tokens * 100) if self._total_tokens > 0 else 0

                # Format token count
                token_str = f"{tokens / 1000:.1f}k" if tokens >= 1000 else str(tokens)

                tooltip = f"{name}: {token_str} tokens ({percent:.1f}%)"
                if details:
                    # Truncate long details
                    if len(details) > 200:
                        details = details[:200] + "..."
                    tooltip += f"\n\n{details}"

                QToolTip.showText(event.globalPosition().toPoint(), tooltip, self)
                return

        # No segment under mouse, hide tooltip
        QToolTip.hideText()

    def event(self, event: QEvent) -> bool:
        """Handle tooltip events for segment-specific tooltips."""
        if event.type() == QEvent.Type.ToolTip:
            # Cast to QHelpEvent for tooltip-specific methods
            help_event = event if isinstance(event, QHelpEvent) else None
            if help_event is None:
                return super().event(event)

            # Find which segment the mouse is over
            pos = help_event.pos()
            for segment_rect, segment in self._segment_rects:
                if segment_rect.contains(pos):
                    name = segment.get("name", "Unknown")
                    tokens = segment.get("tokens", 0)
                    details = segment.get("details", "")

                    # Calculate percentage
                    percent = (tokens / self._total_tokens * 100) if self._total_tokens > 0 else 0

                    # Format token count
                    token_str = f"{tokens / 1000:.1f}k" if tokens >= 1000 else str(tokens)

                    tooltip = f"{name}: {token_str} tokens ({percent:.1f}%)"
                    if details:
                        # Truncate long details
                        if len(details) > 200:
                            details = details[:200] + "..."
                        tooltip += f"\n\n{details}"

                    QToolTip.showText(help_event.globalPos(), tooltip, self)
                    return True

            # No segment under mouse, hide tooltip
            QToolTip.hideText()
            return True

        return super().event(event)
