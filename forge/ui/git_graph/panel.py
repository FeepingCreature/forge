"""Commit panel - interactive display for a single commit."""

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsObject,
    QStyle,
    QStyleOptionGraphicsItem,
    QWidget,
)

from forge.ui.git_graph.types import CommitNode


class CommitPanel(QGraphicsObject):
    """
    A commit panel showing commit info with hover buttons.

    Shows: short hash, multiline message, branch labels.
    On hover: fade in Merge (top), Rebase and Squash (bottom) buttons.
    Branch labels are draggable to move branches to other commits.
    """

    # Signals for git operations
    merge_requested = Signal(str)  # oid
    rebase_requested = Signal(str)  # oid
    squash_requested = Signal(str)  # oid
    diff_requested = Signal(str)  # oid - emitted when Diff button clicked
    merge_drag_started = Signal(str)  # oid - emitted when merge button drag begins
    branch_drag_started = Signal(
        str, str
    )  # (branch_name, source_oid) - emitted when branch label drag begins

    # Panel dimensions - bigger to fit multiline messages
    WIDTH = 220
    HEIGHT = 100
    CORNER_RADIUS = 8
    BUTTON_HEIGHT = 24
    BUTTON_FADE_DURATION = 150

    def __init__(
        self,
        node: CommitNode,
        color: QColor,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self.node = node
        self.color = color
        self._hovered = False
        self._button_opacity_value = 0.0
        self._grayed_out = False  # For merge drag visual feedback
        self._merge_check_icon: str | None = None  # "clean" or "conflict" when hovering during drag

        # Branch label rects for click/drag detection (populated during paint)
        self._branch_label_rects: list[tuple[QRectF, str]] = []  # (rect, branch_name)

        # Enable hover events
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)

        # Setup fade animation for buttons
        self._fade_anim = QPropertyAnimation(self, b"buttonOpacity")
        self._fade_anim.setDuration(self.BUTTON_FADE_DURATION)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

    def boundingRect(self) -> QRectF:  # noqa: N802
        """Return bounding rect (buttons are now inside panel)."""
        # Panel is drawn with top-left at (-WIDTH/2, -HEIGHT/2)
        return QRectF(
            -self.WIDTH / 2,
            -self.HEIGHT / 2,
            self.WIDTH,
            self.HEIGHT,
        )

    def _get_button_opacity(self) -> float:
        return self._button_opacity_value

    def _set_button_opacity(self, value: float) -> None:
        self._button_opacity_value = value
        self.update()

    # Use PySide6 Property for QPropertyAnimation compatibility
    buttonOpacity = Property(float, _get_button_opacity, _set_button_opacity)  # noqa: N815

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        """Paint the commit panel."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Panel rectangle (centered both horizontally and vertically)
        panel_rect = QRectF(-self.WIDTH / 2, -self.HEIGHT / 2, self.WIDTH, self.HEIGHT)

        # Draw shadow
        shadow_rect = panel_rect.translated(2, 2)
        painter.setBrush(QColor(0, 0, 0, 30))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(shadow_rect, self.CORNER_RADIUS, self.CORNER_RADIUS)

        # Draw panel background
        state = option.state  # type: ignore[attr-defined]
        is_selected = bool(state and (state & QStyle.StateFlag.State_Selected))
        bg_color = QColor("#E3F2FD") if is_selected else QColor("#FFFFFF")
        painter.setBrush(bg_color)

        # Border color based on lane
        border_color = self.color if not self._hovered else self.color.darker(110)
        painter.setPen(QPen(border_color, 2))
        painter.drawRoundedRect(panel_rect, self.CORNER_RADIUS, self.CORNER_RADIUS)

        # Left color bar
        bar_rect = QRectF(-self.WIDTH / 2, -self.HEIGHT / 2 + 4, 4, self.HEIGHT - 8)
        painter.setBrush(self.color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(bar_rect, 2, 2)

        # Text setup
        text_x = -self.WIDTH / 2 + 12
        text_width = self.WIDTH - 24

        # Draw branch labels first if any (at top of panel, above hash)
        fm = QFontMetrics(QFont("sans-serif", 8))
        branch_label_height = 0
        self._branch_label_rects = []  # Reset for this paint
        if self.node.branch_names:
            label_y = -self.HEIGHT / 2 + 6
            label_x = text_x
            for branch_name in self.node.branch_names[:2]:  # Max 2 labels
                label_text = branch_name if len(branch_name) <= 12 else branch_name[:10] + "…"
                label_width = fm.horizontalAdvance(label_text) + 8

                # Label background
                label_rect = QRectF(label_x, label_y, label_width, 16)
                painter.setBrush(self.color.lighter(140))
                painter.setPen(QPen(self.color, 1))
                painter.drawRoundedRect(label_rect, 3, 3)

                # Label text
                painter.setPen(self.color.darker(120))
                font = QFont("sans-serif", 8)
                painter.setFont(font)
                painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label_text)

                # Store rect for click detection
                self._branch_label_rects.append((label_rect, branch_name))

                label_x += label_width + 4
            branch_label_height = 20  # Space taken by branch labels

        # Draw short hash (below branch labels if present)
        painter.setPen(QColor("#666666"))
        font = QFont("monospace", 9)
        font.setBold(True)
        painter.setFont(font)
        hash_y = -self.HEIGHT / 2 + 8 + branch_label_height
        painter.drawText(
            QRectF(text_x, hash_y, text_width, 16), Qt.AlignmentFlag.AlignLeft, self.node.short_id
        )

        # Draw commit message with word wrap (up to 3 lines)
        painter.setPen(QColor("#333333"))
        font = QFont("sans-serif", 9)
        painter.setFont(font)

        message_y = hash_y + 18
        message_height = 48  # Space for ~3 lines
        message_rect = QRectF(text_x, message_y, text_width, message_height)

        # Word wrap the message
        painter.drawText(
            message_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
            self.node.message,
        )

        # Draw hover buttons with fade (but not when grayed out)
        if self._button_opacity_value > 0.01 and not self._grayed_out:
            self._draw_buttons(painter, panel_rect)

        # Draw grayed out overlay
        if self._grayed_out:
            painter.setBrush(QColor(255, 255, 255, 180))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(panel_rect, self.CORNER_RADIUS, self.CORNER_RADIUS)

        # Draw merge check icon
        if self._merge_check_icon:
            self._draw_merge_check_icon(painter, panel_rect)

    def _draw_merge_check_icon(self, painter: QPainter, panel_rect: QRectF) -> None:
        """Draw the merge check icon (checkmark or X) on the panel."""
        icon_size = 32
        icon_rect = QRectF(
            panel_rect.right() - icon_size - 8,
            panel_rect.top() + 8,
            icon_size,
            icon_size,
        )

        # Draw circle background
        if self._merge_check_icon == "clean":
            bg_color = QColor("#4CAF50")  # Green
            icon_text = "✓"
        else:
            bg_color = QColor("#F44336")  # Red
            icon_text = "✗"

        painter.setBrush(bg_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(icon_rect)

        # Draw icon text
        painter.setPen(QColor("#FFFFFF"))
        font = QFont("sans-serif", 18)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, icon_text)

    def _draw_buttons(self, painter: QPainter, panel_rect: QRectF) -> None:
        """Draw the merge/rebase/squash buttons with current opacity (inside panel)."""
        opacity = self._button_opacity_value
        button_width = 52
        button_height = 20
        margin = 6
        spacing = 4

        # All buttons inside the panel at the bottom
        button_y = panel_rect.bottom() - button_height - margin

        # Merge button (left)
        merge_rect = QRectF(
            panel_rect.left() + margin,
            button_y,
            button_width,
            button_height,
        )
        merge_color = QColor(76, 175, 80, int(opacity * 255))  # Green
        merge_border = QColor(56, 142, 60, int(opacity * 255))
        painter.setBrush(merge_color)
        painter.setPen(QPen(merge_border, 1))
        painter.drawRoundedRect(merge_rect, 4, 4)

        painter.setPen(QColor(255, 255, 255, int(opacity * 255)))
        font = QFont("sans-serif", 8)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(merge_rect, Qt.AlignmentFlag.AlignCenter, "Merge")

        # Rebase button (center)
        rebase_rect = QRectF(
            merge_rect.right() + spacing,
            button_y,
            button_width,
            button_height,
        )
        rebase_color = QColor(255, 152, 0, int(opacity * 255))  # Orange
        rebase_border = QColor(230, 126, 0, int(opacity * 255))
        painter.setBrush(rebase_color)
        painter.setPen(QPen(rebase_border, 1))
        painter.drawRoundedRect(rebase_rect, 4, 4)

        painter.setPen(QColor(255, 255, 255, int(opacity * 255)))
        painter.drawText(rebase_rect, Qt.AlignmentFlag.AlignCenter, "Rebase")

        # Squash button (center-right)
        squash_rect = QRectF(
            rebase_rect.right() + spacing,
            button_y,
            button_width,
            button_height,
        )
        squash_color = QColor(156, 39, 176, int(opacity * 255))  # Purple
        squash_border = QColor(123, 31, 139, int(opacity * 255))
        painter.setBrush(squash_color)
        painter.setPen(QPen(squash_border, 1))
        painter.drawRoundedRect(squash_rect, 4, 4)

        painter.setPen(QColor(255, 255, 255, int(opacity * 255)))
        painter.drawText(squash_rect, Qt.AlignmentFlag.AlignCenter, "Squash")

        # Diff button (right)
        diff_width = 36
        diff_rect = QRectF(
            squash_rect.right() + spacing,
            button_y,
            diff_width,
            button_height,
        )
        diff_color = QColor(96, 125, 139, int(opacity * 255))  # Blue-gray
        diff_border = QColor(69, 90, 100, int(opacity * 255))
        painter.setBrush(diff_color)
        painter.setPen(QPen(diff_border, 1))
        painter.drawRoundedRect(diff_rect, 4, 4)

        painter.setPen(QColor(255, 255, 255, int(opacity * 255)))
        painter.drawText(diff_rect, Qt.AlignmentFlag.AlignCenter, "Diff")

        # Store button rects for click detection
        self._merge_rect = merge_rect
        self._rebase_rect = rebase_rect
        self._squash_rect = squash_rect
        self._diff_rect = diff_rect

    def hoverEnterEvent(self, event: object) -> None:  # noqa: N802
        """Handle hover enter - fade in buttons."""
        self._hovered = True
        self._fade_anim.stop()
        self._fade_anim.setStartValue(self._button_opacity_value)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.start()
        self.update()

    def hoverLeaveEvent(self, event: object) -> None:  # noqa: N802
        """Handle hover leave - fade out buttons."""
        self._hovered = False
        self._fade_anim.stop()
        self._fade_anim.setStartValue(self._button_opacity_value)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.start()
        self.update()

    def mousePressEvent(self, event: object) -> None:  # noqa: N802
        """Handle mouse press - check for button clicks, branch label drags, or merge drag."""
        from PySide6.QtWidgets import QGraphicsSceneMouseEvent

        if not isinstance(event, QGraphicsSceneMouseEvent):
            return

        pos = event.pos()

        # Check for branch label click (for drag)
        for label_rect, branch_name in self._branch_label_rects:
            if label_rect.contains(pos):
                self._branch_drag_pending: str | None = branch_name
                self._branch_drag_start_pos: QPointF | None = pos
                event.accept()
                return

        if self._button_opacity_value > 0.5:
            if hasattr(self, "_merge_rect") and self._merge_rect.contains(pos):
                # Start merge drag instead of immediate action
                self._merge_drag_pending = True
                event.accept()
                return
            if hasattr(self, "_rebase_rect") and self._rebase_rect.contains(pos):
                self.rebase_requested.emit(self.node.oid)
                event.accept()
                return
            if hasattr(self, "_squash_rect") and self._squash_rect.contains(pos):
                self.squash_requested.emit(self.node.oid)
                event.accept()
                return
            if hasattr(self, "_diff_rect") and self._diff_rect.contains(pos):
                self.diff_requested.emit(self.node.oid)
                event.accept()
                return

        self._merge_drag_pending = False
        self._branch_drag_pending = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: object) -> None:  # noqa: N802
        """Handle mouse move - start merge drag or branch drag if pending."""
        from PySide6.QtWidgets import QGraphicsSceneMouseEvent

        if not isinstance(event, QGraphicsSceneMouseEvent):
            return

        if getattr(self, "_merge_drag_pending", False):
            # Start the merge drag
            self._merge_drag_pending = False
            self.merge_drag_started.emit(self.node.oid)
            event.accept()
            return

        if getattr(self, "_branch_drag_pending", None):
            # Check if we've moved enough to start a branch drag
            start_pos = getattr(self, "_branch_drag_start_pos", None)
            if start_pos:
                delta = event.pos() - start_pos
                if delta.manhattanLength() > 5:
                    pending_branch: str | None = self._branch_drag_pending
                    self._branch_drag_pending = None
                    self._branch_drag_start_pos = None
                    if pending_branch:
                        self.branch_drag_started.emit(pending_branch, self.node.oid)
                    event.accept()
                    return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: object) -> None:  # noqa: N802
        """Handle mouse release - cancel pending drags."""
        from PySide6.QtWidgets import QGraphicsSceneMouseEvent

        self._merge_drag_pending = False
        self._branch_drag_pending = None
        self._branch_drag_start_pos = None
        if isinstance(event, QGraphicsSceneMouseEvent):
            super().mouseReleaseEvent(event)
