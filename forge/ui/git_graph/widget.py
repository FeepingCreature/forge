"""Git graph view widget - main entry point for git graph visualization."""

from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QPainter, QWheelEvent
from PySide6.QtWidgets import QGraphicsView, QWidget

if TYPE_CHECKING:
    from forge.git_backend.actions import GitActionLog

from forge.git_backend.repository import ForgeRepository
from forge.ui.git_graph.branches import BranchListWidget
from forge.ui.git_graph.scene import GitGraphScene


class GitGraphView(QGraphicsView):
    """Pannable and zoomable view of the git graph."""

    # Signals for git operations
    merge_requested = Signal(str)  # oid
    rebase_requested = Signal(str)  # oid
    squash_requested = Signal(str)  # oid

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
        self._scene.squash_requested.connect(self.squash_requested.emit)

        # Connect merge drag signals from panels
        self._connect_panel_signals()

        # Track if we're in a merge drag (need to intercept mouse events)
        self._in_merge_drag = False

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
        self._middle_drag_scene_center: QPointF | None = None

        # Branch list overlay (top-left corner)
        self._branch_list = BranchListWidget(repo, self)
        self._branch_list.branch_clicked.connect(self._jump_to_branch)
        self._branch_list.branch_deleted.connect(self._on_branch_deleted)
        self._branch_list.move(8, 8)

        # Install event filter on viewport to catch middle mouse before QGraphicsView does
        self.viewport().installEventFilter(self)

        # Reserve space on left for branch list overlay
        self._update_branch_list_margin()

        # Scroll to show leftmost content
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().minimum())

    def _update_branch_list_margin(self) -> None:
        """Update scene rect to include space for branch list overlay."""
        if self._scene:
            # Add left margin for branch list
            rect = self._scene.sceneRect()
            branch_width = self._branch_list.width() + 16
            if rect.left() >= 0:
                # Shift scene rect to leave room for overlay
                self._scene.setSceneRect(
                    -branch_width, rect.top(), rect.width() + branch_width, rect.height()
                )

    def _apply_zoom(self, new_zoom: float) -> None:
        """Apply zoom level, clamped to min/max."""
        new_zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, new_zoom))
        if new_zoom != self._zoom:
            factor = new_zoom / self._zoom
            self._zoom = new_zoom
            self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
            self.scale(factor, factor)

    def eventFilter(self, obj: object, event: object) -> bool:  # noqa: N802
        """Filter events on viewport to intercept middle mouse before QGraphicsView."""
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent

        if obj is not self.viewport():
            return False

        if not isinstance(event, QEvent):
            return False

        event_type = event.type()

        if event_type == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
            if event.button() == Qt.MouseButton.MiddleButton:
                # Capture the scene center at drag start
                viewport_center = self.viewport().rect().center()
                self._middle_drag_scene_center = self.mapToScene(viewport_center)
                self._middle_dragging = True
                self._middle_drag_start_y = event.pos().y()
                self._middle_drag_start_zoom = self._zoom
                self.setCursor(Qt.CursorShape.SizeVerCursor)
                return True  # Consume event

        elif event_type == QEvent.Type.MouseMove and isinstance(event, QMouseEvent):
            if self._middle_dragging and self._middle_drag_scene_center is not None:
                # Calculate zoom based on vertical movement
                delta_y = self._middle_drag_start_y - event.pos().y()
                zoom_delta = delta_y / 100.0
                new_zoom = self._middle_drag_start_zoom * (1.0 + zoom_delta)
                self._apply_zoom(new_zoom)
                return True  # Consume event

        elif (
            event_type == QEvent.Type.MouseButtonRelease
            and isinstance(event, QMouseEvent)
            and event.button() == Qt.MouseButton.MiddleButton
            and self._middle_dragging
        ):
            self._middle_dragging = False
            self._middle_drag_scene_center = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return True  # Consume event

        return False  # Let other events through

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        """Handle mouse wheel - Ctrl+wheel zooms, plain wheel scrolls. Works during merge drag."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Ctrl+wheel = zoom
            delta = event.angleDelta().y()
            if delta > 0:
                self._apply_zoom(self._zoom * 1.1)
            elif delta < 0:
                self._apply_zoom(self._zoom / 1.1)
            event.accept()
        else:
            # Plain wheel = scroll (allow during merge drag too)
            super().wheelEvent(event)

    def _connect_panel_signals(self) -> None:
        """Connect drag signals from all panels."""
        for panel in self._scene.oid_to_panel.values():
            panel.merge_drag_started.connect(self._on_merge_drag_started)
            panel.branch_drag_started.connect(self._on_branch_drag_started)
            panel.diff_requested.connect(self._on_diff_requested)

    def _on_merge_drag_started(self, oid: str) -> None:
        """Handle merge drag start from a panel."""
        self._in_merge_drag = True
        self._scene.start_merge_drag(oid)
        # Change to no drag mode so we can track mouse movement
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def _on_branch_drag_started(self, branch_name: str, source_oid: str) -> None:
        """Handle branch drag start from a panel's branch label."""
        self._in_merge_drag = True  # Reuse merge drag tracking
        self._scene.start_branch_drag(branch_name, source_oid)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def _on_diff_requested(self, oid: str) -> None:
        """Handle diff button click - open diff window."""
        from forge.ui.commit_diff_window import CommitDiffWindow

        window = CommitDiffWindow(self.repo, oid, self)
        window.show()

    def mouseMoveEvent(self, event: object) -> None:  # noqa: N802
        """Handle mouse move - update merge/branch drag if active."""
        from PySide6.QtGui import QMouseEvent

        if not isinstance(event, QMouseEvent):
            return

        if self._in_merge_drag:
            scene_pos = self.mapToScene(event.pos())
            # Update whichever drag is active
            if self._scene._merge_drag_active:
                self._scene.update_merge_drag(scene_pos)
            elif self._scene._branch_drag_active:
                self._scene.update_branch_drag(scene_pos)
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: object) -> None:  # noqa: N802
        """Handle mouse release - end merge/branch drag if active."""
        from PySide6.QtGui import QMouseEvent

        if not isinstance(event, QMouseEvent):
            return

        if self._in_merge_drag and event.button() == Qt.MouseButton.LeftButton:
            scene_pos = self.mapToScene(event.pos())

            # End whichever drag is active
            if self._scene._merge_drag_active:
                success = self._scene.end_merge_drag(scene_pos)
            elif self._scene._branch_drag_active:
                success = self._scene.end_branch_drag(scene_pos)
            else:
                success = False

            self._in_merge_drag = False
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.setCursor(Qt.CursorShape.ArrowCursor)

            if success:
                # Refresh the graph after operation
                self.refresh()

            event.accept()
            return

        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: object) -> None:  # noqa: N802
        """Handle key press - Escape cancels merge drag."""
        from PySide6.QtGui import QKeyEvent

        if not isinstance(event, QKeyEvent):
            return

        if self._in_merge_drag and event.key() == Qt.Key.Key_Escape:
            self._scene.cancel_merge_drag()
            self._in_merge_drag = False
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return

        super().keyPressEvent(event)

    def _jump_to_branch(self, branch_name: str) -> None:
        """Jump to center the view on a branch's tip commit."""
        # Find the branch tip commit
        if branch_name not in self.repo.repo.branches:
            return

        branch = self.repo.repo.branches[branch_name]
        commit = branch.peel()
        oid = str(commit.id)

        # Find the panel for this commit
        if oid in self._scene.oid_to_panel:
            panel = self._scene.oid_to_panel[oid]
            self.centerOn(panel)

    def _on_branch_deleted(self, branch_name: str) -> None:
        """Handle branch deletion - refresh the entire view."""
        self.refresh()

    def refresh(self) -> None:
        """Refresh the graph (reload commits and redraw)."""
        # Preserve action log
        old_action_log = self._scene._action_log if self._scene else None

        self._scene = GitGraphScene(self.repo)
        self.setScene(self._scene)
        self._scene.merge_requested.connect(self.merge_requested.emit)
        self._scene.rebase_requested.connect(self.rebase_requested.emit)
        self._scene.squash_requested.connect(self.squash_requested.emit)

        # Restore action log and reconnect panel signals
        if old_action_log:
            self._scene.set_action_log(old_action_log)
        self._connect_panel_signals()

        # Refresh branch list and update margin
        self._branch_list._load_branches()
        self._update_branch_list_margin()

    def set_action_log(self, action_log: "GitActionLog") -> None:
        """Set the action log for recording undoable actions."""
        self._scene.set_action_log(action_log)

    def fit_in_view(self) -> None:
        """Fit the entire graph in the view."""
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        # Update zoom tracking
        self._zoom = self.transform().m11()
