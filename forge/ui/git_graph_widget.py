"""
Git graph widget - visualizes commit history with temporal ordering.

Uses QGraphicsView for pan/zoom, spline connections, and interactive commit panels.
See GRAPH_COMMIT_ORDERING.md for the algorithm details.
"""

import heapq
from dataclasses import dataclass, field

import pygit2
from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyleOptionGraphicsItem,
    QVBoxLayout,
    QWidget,
)

from forge.git_backend.repository import ForgeRepository


@dataclass
class CommitNode:
    """A commit with its layout position."""

    oid: str
    short_id: str
    message: str
    full_message: str
    timestamp: int
    parent_oids: list[str]
    branch_names: list[str] = field(default_factory=list)
    row: int = 0
    column: int = 0


# Colors for different columns (branches)
LANE_COLORS = [
    QColor("#4CAF50"),  # Green
    QColor("#2196F3"),  # Blue
    QColor("#FF9800"),  # Orange
    QColor("#9C27B0"),  # Purple
    QColor("#F44336"),  # Red
    QColor("#00BCD4"),  # Cyan
    QColor("#E91E63"),  # Pink
    QColor("#795548"),  # Brown
]


def get_lane_color(column: int) -> QColor:
    """Get color for a lane/column."""
    return LANE_COLORS[column % len(LANE_COLORS)]


class CommitPanel(QGraphicsObject):
    """
    A commit panel showing commit info with hover buttons.

    Shows: short hash, first line of message, branch labels.
    On hover: fade in Merge (top) and Rebase (bottom) buttons.
    """

    # Signals for git operations
    merge_requested = Signal(str)  # oid
    rebase_requested = Signal(str)  # oid

    # Panel dimensions
    WIDTH = 180
    HEIGHT = 70
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
        self._button_opacity = 0.0

        # Enable hover events
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)

        # Setup fade animation for buttons
        self._fade_anim = QPropertyAnimation(self, b"button_opacity")
        self._fade_anim.setDuration(self.BUTTON_FADE_DURATION)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

    def boundingRect(self) -> QRectF:  # noqa: N802
        """Return bounding rect including button areas."""
        return QRectF(
            -self.WIDTH / 2,
            -self.BUTTON_HEIGHT,
            self.WIDTH,
            self.HEIGHT + 2 * self.BUTTON_HEIGHT,
        )

    def get_button_opacity(self) -> float:
        return self._button_opacity

    def set_button_opacity(self, value: float) -> None:
        self._button_opacity = value
        self.update()

    button_opacity = property(get_button_opacity, set_button_opacity)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        """Paint the commit panel."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Panel rectangle (centered horizontally)
        panel_rect = QRectF(-self.WIDTH / 2, 0, self.WIDTH, self.HEIGHT)

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
        bar_rect = QRectF(-self.WIDTH / 2, 4, 4, self.HEIGHT - 8)
        painter.setBrush(self.color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(bar_rect, 2, 2)

        # Text setup
        text_x = -self.WIDTH / 2 + 12
        text_width = self.WIDTH - 20

        # Draw short hash
        painter.setPen(QColor("#666666"))
        font = QFont("monospace", 9)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            QRectF(text_x, 6, text_width, 16), Qt.AlignmentFlag.AlignLeft, self.node.short_id
        )

        # Draw commit message (first line, truncated)
        painter.setPen(QColor("#333333"))
        font = QFont("sans-serif", 9)
        painter.setFont(font)
        fm = QFontMetrics(font)
        message = fm.elidedText(self.node.message, Qt.TextElideMode.ElideRight, int(text_width))
        painter.drawText(QRectF(text_x, 22, text_width, 18), Qt.AlignmentFlag.AlignLeft, message)

        # Draw branch labels if any
        if self.node.branch_names:
            label_x = text_x
            for branch_name in self.node.branch_names[:2]:  # Max 2 labels
                label_text = branch_name if len(branch_name) <= 12 else branch_name[:10] + "…"
                label_width = fm.horizontalAdvance(label_text) + 8

                # Label background
                label_rect = QRectF(label_x, 44, label_width, 16)
                painter.setBrush(self.color.lighter(140))
                painter.setPen(QPen(self.color, 1))
                painter.drawRoundedRect(label_rect, 3, 3)

                # Label text
                painter.setPen(self.color.darker(120))
                font = QFont("sans-serif", 8)
                painter.setFont(font)
                painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label_text)

                label_x += label_width + 4

        # Draw hover buttons with fade
        if self._button_opacity > 0.01:
            self._draw_buttons(painter, panel_rect)

    def _draw_buttons(self, painter: QPainter, panel_rect: QRectF) -> None:
        """Draw the merge/rebase buttons with current opacity."""
        opacity = self._button_opacity
        button_width = 60

        # Merge button (top)
        merge_rect = QRectF(
            panel_rect.center().x() - button_width / 2,
            panel_rect.top() - self.BUTTON_HEIGHT - 4,
            button_width,
            self.BUTTON_HEIGHT,
        )
        merge_color = QColor(76, 175, 80, int(opacity * 255))  # Green
        merge_border = QColor(56, 142, 60, int(opacity * 255))
        painter.setBrush(merge_color)
        painter.setPen(QPen(merge_border, 1))
        painter.drawRoundedRect(merge_rect, 4, 4)

        painter.setPen(QColor(255, 255, 255, int(opacity * 255)))
        font = QFont("sans-serif", 9)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(merge_rect, Qt.AlignmentFlag.AlignCenter, "Merge")

        # Rebase button (bottom)
        rebase_rect = QRectF(
            panel_rect.center().x() - button_width / 2,
            panel_rect.bottom() + 4,
            button_width,
            self.BUTTON_HEIGHT,
        )
        rebase_color = QColor(255, 152, 0, int(opacity * 255))  # Orange
        rebase_border = QColor(230, 126, 0, int(opacity * 255))
        painter.setBrush(rebase_color)
        painter.setPen(QPen(rebase_border, 1))
        painter.drawRoundedRect(rebase_rect, 4, 4)

        painter.setPen(QColor(255, 255, 255, int(opacity * 255)))
        painter.drawText(rebase_rect, Qt.AlignmentFlag.AlignCenter, "Rebase")

        # Store button rects for click detection
        self._merge_rect = merge_rect
        self._rebase_rect = rebase_rect

    def hoverEnterEvent(self, event: object) -> None:  # noqa: N802
        """Handle hover enter - fade in buttons."""
        self._hovered = True
        self._fade_anim.stop()
        self._fade_anim.setStartValue(self._button_opacity)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.start()
        self.update()

    def hoverLeaveEvent(self, event: object) -> None:  # noqa: N802
        """Handle hover leave - fade out buttons."""
        self._hovered = False
        self._fade_anim.stop()
        self._fade_anim.setStartValue(self._button_opacity)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.start()
        self.update()

    def mousePressEvent(self, event: object) -> None:  # noqa: N802
        """Handle mouse press - check for button clicks."""
        from PySide6.QtWidgets import QGraphicsSceneMouseEvent

        if not isinstance(event, QGraphicsSceneMouseEvent):
            return

        pos = event.pos()

        if self._button_opacity > 0.5:
            if hasattr(self, "_merge_rect") and self._merge_rect.contains(pos):
                self.merge_requested.emit(self.node.oid)
                event.accept()
                return
            if hasattr(self, "_rebase_rect") and self._rebase_rect.contains(pos):
                self.rebase_requested.emit(self.node.oid)
                event.accept()
                return

        super().mousePressEvent(event)


class SplineEdge(QGraphicsPathItem):
    """A spline (bezier curve) connecting two commits."""

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
        """Build the bezier curve path."""
        path = QPainterPath()
        path.moveTo(self.start)

        # Control points for smooth S-curve
        # Vertical distance between points
        dy = self.end.y() - self.start.y()
        dx = self.end.x() - self.start.x()

        # Control point offset (more curve for longer distances)
        ctrl_offset = min(abs(dy) * 0.4, 60)

        if abs(dx) < 5:
            # Straight vertical - simple bezier
            ctrl1 = QPointF(self.start.x(), self.start.y() + ctrl_offset)
            ctrl2 = QPointF(self.end.x(), self.end.y() - ctrl_offset)
        else:
            # Diagonal - S-curve
            ctrl1 = QPointF(self.start.x(), self.start.y() + ctrl_offset)
            ctrl2 = QPointF(self.end.x(), self.end.y() - ctrl_offset)

        path.cubicTo(ctrl1, ctrl2, self.end)
        self.setPath(path)

    def _setup_style(self) -> None:
        """Setup pen style."""
        pen = QPen(self.color, 2.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        self.setPen(pen)
        self.setBrush(Qt.BrushStyle.NoBrush)

        # Draw behind commit panels
        self.setZValue(-1)


class GitGraphScene(QGraphicsScene):
    """Scene containing the git graph with commit panels and spline edges."""

    # Layout constants
    ROW_HEIGHT = 100
    COLUMN_WIDTH = 220
    PADDING = 50

    # Signals for git operations
    merge_requested = Signal(str)  # oid
    rebase_requested = Signal(str)  # oid

    def __init__(self, repo: ForgeRepository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repo = repo
        self.nodes: list[CommitNode] = []
        self.oid_to_node: dict[str, CommitNode] = {}
        self.oid_to_panel: dict[str, CommitPanel] = {}
        self.num_rows = 0
        self.num_columns = 0

        self.setBackgroundBrush(QColor("#FAFAFA"))

        self._load_commits()
        self._assign_rows()
        self._assign_columns()
        self._build_scene()

    def _load_commits(self) -> None:
        """Load all commits from the repository."""
        self.nodes = []
        self.oid_to_node = {}

        # Build branch name lookup: oid -> list of branch names
        branch_tips: dict[str, list[str]] = {}
        for branch_name in self.repo.repo.branches:
            branch = self.repo.repo.branches[branch_name]
            commit = branch.peel(pygit2.Commit)
            oid = str(commit.id)
            if oid not in branch_tips:
                branch_tips[oid] = []
            branch_tips[oid].append(branch_name)

        # Walk all branches to collect commits
        seen_oids: set[str] = set()

        for branch_name in self.repo.repo.branches:
            branch = self.repo.repo.branches[branch_name]
            commit = branch.peel(pygit2.Commit)

            # Walk the commit history
            for c in self.repo.repo.walk(commit.id, pygit2.enums.SortMode.TIME):
                oid = str(c.id)
                if oid in seen_oids:
                    continue
                seen_oids.add(oid)

                # Get first line of commit message
                full_message = c.message.strip()
                first_line = full_message.split("\n")[0][:60]

                node = CommitNode(
                    oid=oid,
                    short_id=oid[:7],
                    message=first_line,
                    full_message=full_message,
                    timestamp=c.commit_time,
                    parent_oids=[str(p.id) for p in c.parents],
                    branch_names=branch_tips.get(oid, []),
                )
                self.nodes.append(node)
                self.oid_to_node[oid] = node

    def _is_ancestor(self, maybe_ancestor: CommitNode, maybe_descendant: CommitNode) -> bool:
        """Check if maybe_ancestor is an ancestor of maybe_descendant."""
        visited: set[str] = set()
        stack = [maybe_descendant.oid]

        while stack:
            current_oid = stack.pop()
            if current_oid in visited:
                continue
            visited.add(current_oid)

            if current_oid == maybe_ancestor.oid:
                return True

            if current_oid in self.oid_to_node:
                node = self.oid_to_node[current_oid]
                stack.extend(node.parent_oids)

        return False

    def _compute_order_keys(self) -> dict[str, int]:
        """Compute global order key for each commit using topological sort."""
        children_of: dict[str, list[str]] = {node.oid: [] for node in self.nodes}
        for node in self.nodes:
            for parent_oid in node.parent_oids:
                if parent_oid in children_of:
                    children_of[parent_oid].append(node.oid)

        in_degree: dict[str, int] = {}
        for node in self.nodes:
            in_degree[node.oid] = len(children_of[node.oid])

        ready: list[tuple[int, str]] = []
        for node in self.nodes:
            if in_degree[node.oid] == 0:
                ready.append((-node.timestamp, node.oid))

        heapq.heapify(ready)

        order_key: dict[str, int] = {}
        current_order = 0

        while ready:
            _, oid = heapq.heappop(ready)

            if oid in order_key:
                continue

            order_key[oid] = current_order
            current_order += 1

            node = self.oid_to_node[oid]
            for parent_oid in node.parent_oids:
                if parent_oid not in in_degree:
                    continue
                in_degree[parent_oid] -= 1
                if in_degree[parent_oid] == 0:
                    parent_node = self.oid_to_node[parent_oid]
                    heapq.heappush(ready, (-parent_node.timestamp, parent_oid))

        return order_key

    def _assign_rows(self) -> None:
        """Assign rows using temporal contiguity algorithm."""
        order_keys = self._compute_order_keys()
        sorted_nodes = sorted(self.nodes, key=lambda n: order_keys.get(n.oid, float("inf")))

        current_row = 0
        current_row_nodes: list[CommitNode] = []

        for node in sorted_nodes:
            can_join = True
            for existing in current_row_nodes:
                if self._is_ancestor(node, existing) or self._is_ancestor(existing, node):
                    can_join = False
                    break

            if can_join and current_row_nodes:
                current_row_nodes.append(node)
                node.row = current_row
            else:
                if current_row_nodes:
                    current_row += 1
                current_row_nodes = [node]
                node.row = current_row

        self.num_rows = current_row + 1

    def _assign_columns(self) -> None:
        """Assign columns (lanes) to commits."""
        rows: dict[int, list[CommitNode]] = {}
        for node in self.nodes:
            if node.row not in rows:
                rows[node.row] = []
            rows[node.row].append(node)

        claimed_by: dict[str, str] = {}
        lane_last_row: dict[int, int] = {}
        commit_lane: dict[str, int] = {}
        max_lane = 0

        for row in range(self.num_rows):
            if row not in rows:
                continue

            row_nodes = sorted(rows[row], key=lambda n: -n.timestamp)
            rows[row] = row_nodes

            active_lanes: set[int] = {
                lane for lane, last_row in lane_last_row.items() if last_row >= row
            }

            for node in row_nodes:
                if node.oid in claimed_by:
                    child_oid = claimed_by[node.oid]
                    if child_oid in commit_lane:
                        node.column = commit_lane[child_oid]
                    else:
                        lane = 0
                        while lane in active_lanes:
                            lane += 1
                        node.column = lane
                else:
                    lane = 0
                    while lane in active_lanes:
                        lane += 1
                    node.column = lane

                commit_lane[node.oid] = node.column
                active_lanes.add(node.column)
                max_lane = max(max_lane, node.column)

                if node.parent_oids:
                    first_parent = node.parent_oids[0]
                    if first_parent in self.oid_to_node:
                        parent_row = self.oid_to_node[first_parent].row
                        current_last = lane_last_row.get(node.column, row)
                        lane_last_row[node.column] = max(current_last, parent_row)
                    else:
                        lane_last_row[node.column] = max(lane_last_row.get(node.column, row), row)
                else:
                    lane_last_row[node.column] = max(lane_last_row.get(node.column, row), row)

                if node.parent_oids:
                    first_parent = node.parent_oids[0]
                    if first_parent in claimed_by:
                        old_claimer = claimed_by[first_parent]
                        if old_claimer in commit_lane:
                            old_lane = commit_lane[old_claimer]
                            if node.column < old_lane:
                                claimed_by[first_parent] = node.oid
                    else:
                        claimed_by[first_parent] = node.oid

        self.num_columns = max_lane + 1 if max_lane >= 0 else 1

    def _get_node_pos(self, node: CommitNode) -> QPointF:
        """Get the position of a node's center."""
        x = self.PADDING + node.column * self.COLUMN_WIDTH + self.COLUMN_WIDTH / 2
        y = self.PADDING + node.row * self.ROW_HEIGHT + self.ROW_HEIGHT / 2
        return QPointF(x, y)

    def _build_scene(self) -> None:
        """Build the graphics scene with panels and edges."""
        self.clear()
        self.oid_to_panel = {}

        # Draw edges first (behind panels)
        for node in self.nodes:
            child_center = self._get_node_pos(node)
            # Start from bottom center of child panel
            start_pos = QPointF(child_center.x(), child_center.y() + CommitPanel.HEIGHT / 2)

            for parent_oid in node.parent_oids:
                if parent_oid not in self.oid_to_node:
                    continue

                parent = self.oid_to_node[parent_oid]
                parent_center = self._get_node_pos(parent)
                # End at top center of parent panel
                end_pos = QPointF(parent_center.x(), parent_center.y() - CommitPanel.HEIGHT / 2)

                # Use parent's lane color for the edge (edge leads to parent)
                color = get_lane_color(parent.column)
                edge = SplineEdge(start_pos, end_pos, color)
                self.addItem(edge)

        # Draw commit panels
        for node in self.nodes:
            pos = self._get_node_pos(node)
            color = get_lane_color(node.column)

            panel = CommitPanel(node, color)
            panel.setPos(pos)
            panel.merge_requested.connect(self.merge_requested.emit)
            panel.rebase_requested.connect(self.rebase_requested.emit)

            self.addItem(panel)
            self.oid_to_panel[node.oid] = panel

        # Set scene rect with padding
        width = self.num_columns * self.COLUMN_WIDTH + 2 * self.PADDING
        height = self.num_rows * self.ROW_HEIGHT + 2 * self.PADDING
        self.setSceneRect(0, 0, width, height)


class BranchListWidget(QWidget):
    """Overlay widget listing branches for quick navigation."""

    branch_clicked = Signal(str)  # branch name

    def __init__(self, repo: ForgeRepository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repo = repo

        # Semi-transparent background
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(self.backgroundRole(), QColor(255, 255, 255, 230))
        self.setPalette(palette)

        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)

        # Branch list
        self._list = QListWidget()
        self._list.setStyleSheet("""
            QListWidget {
                border: 1px solid #ddd;
                border-radius: 4px;
                background: transparent;
                font-size: 11px;
            }
            QListWidget::item {
                padding: 4px 8px;
                border-radius: 3px;
            }
            QListWidget::item:hover {
                background: #E3F2FD;
            }
            QListWidget::item:selected {
                background: #2196F3;
                color: white;
            }
        """)
        self._list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._list)

        self._load_branches()
        self.setFixedWidth(160)
        self.adjustSize()

    def _load_branches(self) -> None:
        """Load branches into the list."""
        self._list.clear()
        for branch_name in sorted(self.repo.repo.branches):
            item = QListWidgetItem(branch_name)
            # Color code by lane
            color = get_lane_color(hash(branch_name) % len(LANE_COLORS))
            item.setForeground(color.darker(120))
            self._list.addItem(item)

        # Adjust height based on items (max 10 visible)
        item_height = 24
        visible_items = min(self._list.count(), 10)
        self._list.setFixedHeight(visible_items * item_height + 8)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        """Handle branch click."""
        self.branch_clicked.emit(item.text())


class GitGraphView(QGraphicsView):
    """Pannable and zoomable view of the git graph."""

    # Signals for git operations
    merge_requested = Signal(str)  # oid
    rebase_requested = Signal(str)  # oid

    MIN_ZOOM = 0.2
    MAX_ZOOM = 2.0
    ZOOM_FACTOR = 1.05

    def __init__(self, repo: ForgeRepository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repo = repo

        # Create scene
        self._scene = GitGraphScene(repo)
        self.setScene(self._scene)

        # Forward signals
        self._scene.merge_requested.connect(self.merge_requested.emit)
        self._scene.rebase_requested.connect(self.rebase_requested.emit)

        # Setup view
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)

        # Enable panning with left-click drag
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        # Current zoom level
        self._zoom = 1.0

        # Middle mouse zoom state
        self._middle_dragging = False
        self._middle_drag_start_y = 0
        self._middle_drag_start_zoom = 1.0

        # Branch list overlay (top-left corner)
        self._branch_list = BranchListWidget(repo, self)
        self._branch_list.branch_clicked.connect(self._jump_to_branch)
        self._branch_list.move(8, 8)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        """Handle mouse wheel for vertical scrolling."""
        # Use wheel for vertical scroll (default behavior)
        super().wheelEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Handle mouse press - middle button starts zoom drag."""
        if event.button() == Qt.MouseButton.MiddleButton:
            self._middle_dragging = True
            self._middle_drag_start_y = event.pos().y()
            self._middle_drag_start_zoom = self._zoom
            self.setCursor(Qt.CursorShape.SizeVerCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Handle mouse move - zoom if middle dragging."""
        if self._middle_dragging:
            # Calculate zoom based on vertical movement
            delta_y = self._middle_drag_start_y - event.pos().y()
            # 100 pixels of drag = one full zoom factor
            zoom_delta = delta_y / 100.0
            new_zoom = self._middle_drag_start_zoom * (1.0 + zoom_delta)

            # Clamp zoom
            new_zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, new_zoom))

            # Apply zoom
            if new_zoom != self._zoom:
                factor = new_zoom / self._zoom
                self._zoom = new_zoom
                self.scale(factor, factor)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Handle mouse release - end middle drag zoom."""
        if event.button() == Qt.MouseButton.MiddleButton and self._middle_dragging:
            self._middle_dragging = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def _jump_to_branch(self, branch_name: str) -> None:
        """Jump to center the view on a branch's tip commit."""
        # Find the branch tip commit
        if branch_name not in self.repo.repo.branches:
            return

        branch = self.repo.repo.branches[branch_name]
        commit = branch.peel(pygit2.Commit)
        oid = str(commit.id)

        # Find the panel for this commit
        if oid in self._scene.oid_to_panel:
            panel = self._scene.oid_to_panel[oid]
            self.centerOn(panel)

    def refresh(self) -> None:
        """Refresh the graph (reload commits and redraw)."""
        self._scene = GitGraphScene(self.repo)
        self.setScene(self._scene)
        self._scene.merge_requested.connect(self.merge_requested.emit)
        self._scene.rebase_requested.connect(self.rebase_requested.emit)
        # Refresh branch list too
        self._branch_list._load_branches()

    def fit_in_view(self) -> None:
        """Fit the entire graph in the view."""
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        # Update zoom tracking
        self._zoom = self.transform().m11()


# Backwards compatibility alias
GitGraphScrollArea = GitGraphView
