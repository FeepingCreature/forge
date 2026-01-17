"""
Mood bar widget for visualizing prompt token usage with color-coded segments.
"""

from PySide6.QtCore import QEvent, QRect
from PySide6.QtGui import QColor, QHelpEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QToolTip, QWidget


class MoodBar(QWidget):
    """
    A horizontal bar widget that shows token usage with colored segments.

    Each segment represents a content type (system prompt, summaries, files, conversation)
    with proportional widths based on token counts.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._segments: list[dict] = []
        self._total_tokens: int = 0
        self._segment_rects: list[tuple[QRect, dict]] = []  # For hit testing

        # Empty/unused color
        self._empty_color = QColor("#374151")

        # Enable mouse tracking for tooltips
        self.setMouseTracking(True)

    def set_segments(self, segments: list[dict]) -> None:
        """Set segments to display.

        Each segment dict has:
        - name: str (e.g., "System prompt")
        - tokens: int
        - color: str (hex color)
        - details: str (optional, for tooltip)
        """
        self._segments = segments
        self._total_tokens = sum(s.get("tokens", 0) for s in segments)
        self._segment_rects = []  # Will be recalculated on paint
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """Draw the colored segments."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        width = rect.width()
        height = rect.height()

        # Clear segment rects for hit testing
        self._segment_rects = []

        if self._total_tokens == 0:
            # Draw empty bar
            painter.fillRect(rect, self._empty_color)
            return

        x = 0
        for segment in self._segments:
            tokens = segment.get("tokens", 0)
            if tokens == 0:
                continue

            # Calculate proportional width
            proportion = tokens / self._total_tokens
            segment_width = int(width * proportion)

            # Ensure at least 1px for non-zero segments
            if segment_width == 0 and tokens > 0:
                segment_width = 1

            # Don't exceed remaining width
            segment_width = min(segment_width, width - x)

            if segment_width > 0:
                color = QColor(segment.get("color", "#374151"))
                segment_rect = QRect(x, 0, segment_width, height)
                painter.fillRect(segment_rect, color)

                # Store for hit testing
                self._segment_rects.append((segment_rect, segment))

                x += segment_width

        # Fill any remaining space with empty color
        if x < width:
            painter.fillRect(QRect(x, 0, width - x, height), self._empty_color)

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
                        tooltip += f"\n{details}"

                    QToolTip.showText(help_event.globalPos(), tooltip, self)
                    return True

            # No segment under mouse, hide tooltip
            QToolTip.hideText()
            return True

        return super().event(event)
