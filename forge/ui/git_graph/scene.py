"""Git graph scene - manages commit layout and rendering."""

import heapq
from typing import TYPE_CHECKING

import pygit2
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QGraphicsScene, QMessageBox, QWidget

if TYPE_CHECKING:
    from forge.git_backend.actions import GitActionLog

from forge.git_backend.repository import ForgeRepository
from forge.ui.git_graph.edges import MergeDragSpline, SplineEdge
from forge.ui.git_graph.panel import CommitPanel
from forge.ui.git_graph.types import CommitNode, get_lane_color


class GitGraphScene(QGraphicsScene):
    """Scene containing the git graph with commit panels and spline edges."""

    # Layout constants - increased for bigger panels
    ROW_HEIGHT = 130
    COLUMN_WIDTH = 260
    PADDING = 50

    # Signals for git operations
    merge_requested = Signal(str)  # oid
    rebase_requested = Signal(str)  # oid
    squash_requested = Signal(str)  # oid
    merge_completed = Signal()  # Emitted after successful merge

    def __init__(self, repo: ForgeRepository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repo = repo
        self.nodes: list[CommitNode] = []
        self.oid_to_node: dict[str, CommitNode] = {}
        self.oid_to_panel: dict[str, CommitPanel] = {}
        self.num_rows = 0
        self.num_columns = 0

        # Merge drag state
        self._merge_drag_active = False
        self._merge_drag_source_oid: str | None = None
        self._merge_drag_spline: MergeDragSpline | None = None
        self._merge_drag_valid_targets: set[str] = set()  # OIDs of valid drop targets
        self._merge_drag_hover_target: str | None = None  # Currently hovered target

        # Branch drag state
        self._branch_drag_active = False
        self._branch_drag_name: str | None = None
        self._branch_drag_source_oid: str | None = None
        self._branch_drag_spline: MergeDragSpline | None = None
        self._branch_drag_hover_target: str | None = None

        # Action log for undo
        self._action_log: GitActionLog | None = None

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
            panel.squash_requested.connect(self.squash_requested.emit)

            self.addItem(panel)
            self.oid_to_panel[node.oid] = panel

        # Set scene rect with padding
        width = self.num_columns * self.COLUMN_WIDTH + 2 * self.PADDING
        height = self.num_rows * self.ROW_HEIGHT + 2 * self.PADDING
        self.setSceneRect(0, 0, width, height)

    def set_action_log(self, action_log: "GitActionLog") -> None:
        """Set the action log for recording undoable actions."""
        self._action_log = action_log

    def _get_branch_heads(self) -> dict[str, str]:
        """Get mapping of branch name -> HEAD commit OID."""
        heads: dict[str, str] = {}
        for branch_name in self.repo.repo.branches.local:
            branch = self.repo.repo.branches[branch_name]
            commit = branch.peel(pygit2.Commit)
            heads[branch_name] = str(commit.id)
        return heads

    def _get_valid_merge_targets(self, source_oid: str) -> set[str]:
        """Get OIDs of valid merge targets (branch HEADs, excluding source's branch)."""
        heads = self._get_branch_heads()
        source_branches = set()

        # Find which branches the source commit is HEAD of
        for branch_name, head_oid in heads.items():
            if head_oid == source_oid:
                source_branches.add(branch_name)

        # Valid targets are HEADs of other branches
        valid: set[str] = set()
        for branch_name, head_oid in heads.items():
            if branch_name not in source_branches and head_oid != source_oid:
                valid.add(head_oid)

        return valid

    def start_merge_drag(self, source_oid: str) -> None:
        """Start a merge drag operation from the given commit."""
        if source_oid not in self.oid_to_panel:
            return

        self._merge_drag_active = True
        self._merge_drag_source_oid = source_oid
        self._merge_drag_valid_targets = self._get_valid_merge_targets(source_oid)
        self._merge_drag_hover_target = None

        # Create spline starting from source panel's top center
        source_panel = self.oid_to_panel[source_oid]
        start_pos = source_panel.scenePos() + QPointF(0, -CommitPanel.HEIGHT / 2)
        self._merge_drag_spline = MergeDragSpline(start_pos)
        self.addItem(self._merge_drag_spline)

        # Gray out invalid targets
        for oid, panel in self.oid_to_panel.items():
            if oid != source_oid and oid not in self._merge_drag_valid_targets:
                panel._grayed_out = True
                panel.update()

    def update_merge_drag(self, scene_pos: QPointF) -> None:
        """Update the merge drag spline endpoint and check hover targets."""
        if not self._merge_drag_active or self._merge_drag_spline is None:
            return

        # Check if hovering over a valid target
        old_hover = self._merge_drag_hover_target
        self._merge_drag_hover_target = None

        for oid in self._merge_drag_valid_targets:
            if oid not in self.oid_to_panel:
                continue
            panel = self.oid_to_panel[oid]
            if panel.sceneBoundingRect().contains(scene_pos):
                self._merge_drag_hover_target = oid
                break

        # Update spline endpoint - snap to target if hovering, else follow cursor
        if self._merge_drag_hover_target:
            target_panel = self.oid_to_panel[self._merge_drag_hover_target]
            # Snap to top center of target panel
            snap_pos = target_panel.scenePos() + QPointF(0, -CommitPanel.HEIGHT / 2)
            self._merge_drag_spline.update_end(snap_pos)
        else:
            self._merge_drag_spline.update_end(scene_pos)

        # Update merge check icon if hover target changed
        if self._merge_drag_hover_target != old_hover:
            # Clear old hover
            if old_hover and old_hover in self.oid_to_panel:
                self.oid_to_panel[old_hover]._merge_check_icon = None
                self.oid_to_panel[old_hover].update()

            # Check new hover
            if self._merge_drag_hover_target and self._merge_drag_source_oid:
                self._check_merge_and_update_icon(
                    self._merge_drag_source_oid, self._merge_drag_hover_target
                )

    def _check_merge_and_update_icon(self, source_oid: str, target_oid: str) -> None:
        """Check if merge would be clean and update the target panel's icon."""
        from forge.git_backend.actions import check_merge_clean

        # Find branch for target
        heads = self._get_branch_heads()
        target_branch = None
        for branch_name, head_oid in heads.items():
            if head_oid == target_oid:
                target_branch = branch_name
                break

        if not target_branch:
            return

        is_clean = check_merge_clean(self.repo, source_oid, target_branch)

        if target_oid in self.oid_to_panel:
            panel = self.oid_to_panel[target_oid]
            panel._merge_check_icon = "clean" if is_clean else "conflict"
            panel.update()

    def end_merge_drag(self, scene_pos: QPointF) -> bool:
        """
        End the merge drag operation.

        Returns True if a merge was performed, False otherwise.
        """
        if not self._merge_drag_active:
            return False

        # Clean up spline
        if self._merge_drag_spline:
            self.removeItem(self._merge_drag_spline)
            self._merge_drag_spline = None

        # Un-gray panels and clear icons
        for panel in self.oid_to_panel.values():
            panel._grayed_out = False
            panel._merge_check_icon = None
            panel.update()

        source_oid = self._merge_drag_source_oid
        target_oid = self._merge_drag_hover_target

        # Reset state
        self._merge_drag_active = False
        self._merge_drag_source_oid = None
        self._merge_drag_valid_targets = set()
        self._merge_drag_hover_target = None

        # Perform merge if we have a valid target
        if source_oid and target_oid:
            return self._perform_merge(source_oid, target_oid)

        return False

    def cancel_merge_drag(self) -> None:
        """Cancel the merge drag without performing any action."""
        if not self._merge_drag_active:
            return

        # Clean up spline
        if self._merge_drag_spline:
            self.removeItem(self._merge_drag_spline)
            self._merge_drag_spline = None

        # Un-gray panels and clear icons
        for panel in self.oid_to_panel.values():
            panel._grayed_out = False
            panel._merge_check_icon = None
            panel.update()

        self._merge_drag_active = False
        self._merge_drag_source_oid = None
        self._merge_drag_valid_targets = set()
        self._merge_drag_hover_target = None

    def _perform_merge(self, source_oid: str, target_oid: str) -> bool:
        """Perform the merge operation."""
        from forge.git_backend.actions import MergeAction

        # Find target branch
        heads = self._get_branch_heads()
        target_branch = None
        for branch_name, head_oid in heads.items():
            if head_oid == target_oid:
                target_branch = branch_name
                break

        if not target_branch:
            return False

        # Create and perform merge action
        action = MergeAction(
            repo=self.repo,
            source_oid=source_oid,
            target_branch=target_branch,
        )

        try:
            action.perform()
        except ValueError as e:
            # Merge has conflicts - show dialog
            QMessageBox.warning(
                None,
                "Merge Conflict",
                f"Cannot merge: {e}\n\nConflict resolution is not yet implemented.",
            )
            return False

        # Record action for undo
        if self._action_log:
            self._action_log.record(action)

        # Emit signal to trigger refresh
        self.merge_completed.emit()
        return True

    # --- Branch drag methods ---

    def start_branch_drag(self, branch_name: str, source_oid: str) -> None:
        """Start a branch drag operation."""
        if source_oid not in self.oid_to_panel:
            return

        self._branch_drag_active = True
        self._branch_drag_name = branch_name
        self._branch_drag_source_oid = source_oid
        self._branch_drag_hover_target = None

        # Create spline starting from source panel
        source_panel = self.oid_to_panel[source_oid]
        start_pos = source_panel.scenePos() + QPointF(0, -CommitPanel.HEIGHT / 2)

        # Use orange color for branch drag (different from merge green)
        self._branch_drag_spline = MergeDragSpline(start_pos)
        self._branch_drag_spline.setPen(QPen(QColor("#FF9800"), 3, Qt.PenStyle.DashLine))
        self.addItem(self._branch_drag_spline)

        # Gray out source commit (can't drop on self)
        source_panel._grayed_out = True
        source_panel.update()

    def update_branch_drag(self, scene_pos: QPointF) -> None:
        """Update the branch drag spline endpoint."""
        if not self._branch_drag_active or self._branch_drag_spline is None:
            return

        # Check if hovering over any commit (all commits are valid targets except source)
        old_hover = self._branch_drag_hover_target
        self._branch_drag_hover_target = None

        for oid, panel in self.oid_to_panel.items():
            if oid == self._branch_drag_source_oid:
                continue
            if panel.sceneBoundingRect().contains(scene_pos):
                self._branch_drag_hover_target = oid
                break

        # Update spline endpoint
        if self._branch_drag_hover_target:
            target_panel = self.oid_to_panel[self._branch_drag_hover_target]
            snap_pos = target_panel.scenePos() + QPointF(0, -CommitPanel.HEIGHT / 2)
            self._branch_drag_spline.update_end(snap_pos)
        else:
            self._branch_drag_spline.update_end(scene_pos)

        # Update visual feedback on hover change
        if self._branch_drag_hover_target != old_hover:
            if old_hover and old_hover in self.oid_to_panel:
                self.oid_to_panel[old_hover]._merge_check_icon = None
                self.oid_to_panel[old_hover].update()
            if self._branch_drag_hover_target:
                panel = self.oid_to_panel[self._branch_drag_hover_target]
                panel._merge_check_icon = "clean"  # Always valid for branch move
                panel.update()

    def end_branch_drag(self, scene_pos: QPointF) -> bool:
        """End the branch drag operation. Returns True if branch was moved."""
        if not self._branch_drag_active:
            return False

        # Clean up spline
        if self._branch_drag_spline:
            self.removeItem(self._branch_drag_spline)
            self._branch_drag_spline = None

        # Un-gray panels and clear icons
        for panel in self.oid_to_panel.values():
            panel._grayed_out = False
            panel._merge_check_icon = None
            panel.update()

        branch_name = self._branch_drag_name
        target_oid = self._branch_drag_hover_target

        # Reset state
        self._branch_drag_active = False
        self._branch_drag_name = None
        self._branch_drag_source_oid = None
        self._branch_drag_hover_target = None

        # Perform move if we have a valid target
        if branch_name and target_oid:
            return self._perform_branch_move(branch_name, target_oid)

        return False

    def cancel_branch_drag(self) -> None:
        """Cancel the branch drag without performing any action."""
        if not self._branch_drag_active:
            return

        if self._branch_drag_spline:
            self.removeItem(self._branch_drag_spline)
            self._branch_drag_spline = None

        for panel in self.oid_to_panel.values():
            panel._grayed_out = False
            panel._merge_check_icon = None
            panel.update()

        self._branch_drag_active = False
        self._branch_drag_name = None
        self._branch_drag_source_oid = None
        self._branch_drag_hover_target = None

    def _perform_branch_move(self, branch_name: str, target_oid: str) -> bool:
        """Move a branch to point to a different commit."""
        try:
            self.repo.move_branch(branch_name, target_oid)
            self.merge_completed.emit()  # Reuse signal to trigger refresh
            return True
        except (ValueError, KeyError) as e:
            QMessageBox.warning(
                None,
                "Cannot Move Branch",
                str(e),
            )
            return False
