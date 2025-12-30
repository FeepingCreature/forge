"""Edge rendering for git graph - spline connections between commits."""

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPathItem


class SplineEdge(QGraphicsPathItem):
    """
    A spline (bezier curve) connecting child commit to parent commit.

    COORDINATE SYSTEM NOTE:
    The git graph is drawn "upside down" compared to typical graphs:
    - Newer commits (children) are at the TOP of the screen (LOWER y values)
    - Older commits (parents) are at the BOTTOM of the screen (HIGHER y values)
    - So start.y < end.y (we draw DOWN from child to parent)

    For diagonal connections, we use an S-shaped path with two rounded corners:
    1. Go DOWN from child
    2. Turn horizontally toward parent's column
    3. Go horizontal
    4. Turn DOWN into parent
    """

    # Corner radius for turns - should be less than half the space between panels
    # Free space = ROW_HEIGHT - CommitPanel.HEIGHT = 130 - 100 = 30, so max radius ~15
    CORNER_RADIUS = 15

    def __init__(
        self,
        start: QPointF,
        end: QPointF,
        color: QColor,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self.start = start
        self.end = end
        self.color = color
        self._build_path()
        self._setup_style()

    def _build_path(self) -> None:
        """Build an axis-aligned path with two rounded corners.

        Path structure for diagonal connections:
        1. Vertical line DOWN from start to (start.x, end.y - 2r)
        2. Curve toward target column, ending at (start.x ± r, end.y - r)
        3. Horizontal line to (end.x ∓ r, end.y - r)
        4. Curve DOWN into parent, ending at end

        The horizontal segment is at (end.y - r), leaving room for the
        final curve down into the parent panel.
        """
        path = QPainterPath()
        path.moveTo(self.start)

        dx = self.end.x() - self.start.x()
        r = self.CORNER_RADIUS

        if abs(dx) < 5:
            # Straight vertical line - just draw it
            path.lineTo(self.end)
        elif dx > 0:
            # Going down-right: child is top-left, parent is bottom-right
            # 1. Line down from start to turn level
            path.lineTo(self.start.x(), self.end.y() - 2 * r)
            # 2. Curve right: control at corner, end one radius right and down
            path.quadTo(
                QPointF(self.start.x(), self.end.y() - r),
                QPointF(self.start.x() + r, self.end.y() - r),
            )
            # 3. Horizontal line right to above parent
            path.lineTo(self.end.x() - r, self.end.y() - r)
            # 4. Curve down into parent
            path.quadTo(
                QPointF(self.end.x(), self.end.y() - r),
                self.end,
            )
        else:
            # Going down-left: child is top-right, parent is bottom-left
            # 1. Line down from start to turn level
            path.lineTo(self.start.x(), self.end.y() - 2 * r)
            # 2. Curve left: control at corner, end one radius left and down
            path.quadTo(
                QPointF(self.start.x(), self.end.y() - r),
                QPointF(self.start.x() - r, self.end.y() - r),
            )
            # 3. Horizontal line left to above parent
            path.lineTo(self.end.x() + r, self.end.y() - r)
            # 4. Curve down into parent
            path.quadTo(
                QPointF(self.end.x(), self.end.y() - r),
                self.end,
            )

        self.setPath(path)

    def _setup_style(self) -> None:
        """Setup pen style."""
        pen = QPen(self.color, 2.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self.setPen(pen)
        self.setBrush(Qt.BrushStyle.NoBrush)

        # Draw behind commit panels
        self.setZValue(-1)


class MergeDragSpline(QGraphicsPathItem):
    """
    A spline drawn during merge drag operation.

    Draws from the source commit's merge button upward, then curves toward cursor.
    """

    def __init__(self, start: QPointF, parent: QGraphicsItem | None = None) -> None:
        super().__init__(parent)
        self.start = start
        self.end = start  # Will be updated during drag

        pen = QPen(QColor("#4CAF50"), 3)  # Green like merge button
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)
        self.setBrush(Qt.BrushStyle.NoBrush)
        self.setZValue(100)  # Draw on top

    def update_end(self, end: QPointF) -> None:
        """Update the end point and rebuild the path."""
        self.end = end
        self._build_path()

    def _build_path(self) -> None:
        """Build a curved path from start to end."""
        path = QPainterPath()
        path.moveTo(self.start)

        # Control points for a nice curve
        # Start going up, then curve toward target
        dy = self.end.y() - self.start.y()

        # First control point: above start
        c1 = QPointF(self.start.x(), self.start.y() - 50)
        # Second control point: above end
        c2 = QPointF(self.end.x(), self.end.y() - abs(dy) * 0.3 - 30)

        path.cubicTo(c1, c2, self.end)
        self.setPath(path)
