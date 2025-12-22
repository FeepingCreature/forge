"""
Git graph widget - visualizes commit history with temporal ordering.

See GRAPH_COMMIT_ORDERING.md for the algorithm details.
"""

import heapq
from dataclasses import dataclass

import pygit2
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QScrollArea, QWidget

from forge.git_backend.repository import ForgeRepository


@dataclass
class CommitNode:
    """A commit with its layout position."""

    oid: str
    short_id: str
    message: str
    timestamp: int
    parent_oids: list[str]
    row: int = 0
    column: int = 0


class GitGraphWidget(QWidget):
    """Widget that displays git commit graph with temporal ordering."""

    # Layout constants
    ROW_HEIGHT = 60
    COLUMN_WIDTH = 200
    NODE_RADIUS = 8
    PADDING = 20

    # Colors for different columns (branches)
    LANE_COLORS = [
        QColor("#4CAF50"),  # Green
        QColor("#2196F3"),  # Blue
        QColor("#FF9800"),  # Orange
        QColor("#9C27B0"),  # Purple
        QColor("#F44336"),  # Red
        QColor("#00BCD4"),  # Cyan
        QColor("#FFEB3B"),  # Yellow
        QColor("#795548"),  # Brown
    ]

    def __init__(self, repo: ForgeRepository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repo = repo
        self.nodes: list[CommitNode] = []
        self.oid_to_node: dict[str, CommitNode] = {}
        self.num_rows = 0
        self.num_columns = 0

        self._load_commits()
        self._assign_rows()
        self._assign_columns()
        self._update_size()

    def _load_commits(self) -> None:
        """Load all commits from the repository."""
        self.nodes = []
        self.oid_to_node = {}

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
                message = c.message.split("\n")[0][:50]

                node = CommitNode(
                    oid=oid,
                    short_id=oid[:7],
                    message=message,
                    timestamp=c.commit_time,
                    parent_oids=[str(p.id) for p in c.parents],
                )
                self.nodes.append(node)
                self.oid_to_node[oid] = node

    def _is_ancestor(self, maybe_ancestor: CommitNode, maybe_descendant: CommitNode) -> bool:
        """Check if maybe_ancestor is an ancestor of maybe_descendant."""
        # Simple check: walk parents of descendant
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
        """
        Compute a global order key for each commit.

        Uses topological sort (ancestry) as primary order, timestamp as tiebreaker.
        Returns dict mapping oid -> order_key (lower = should appear first/top).
        """
        # Build child -> parents map (we have this in nodes)
        # Build parent -> children map
        children_of: dict[str, list[str]] = {node.oid: [] for node in self.nodes}
        for node in self.nodes:
            for parent_oid in node.parent_oids:
                if parent_oid in children_of:
                    children_of[parent_oid].append(node.oid)

        # Count how many children each node has (in-degree for reverse topo sort)
        in_degree: dict[str, int] = {}
        for node in self.nodes:
            in_degree[node.oid] = len(children_of[node.oid])

        # Start with nodes that have no children (branch tips)
        # Use negative timestamp so newest comes first (highest timestamp = lowest sort key)
        ready: list[tuple[int, str]] = []
        for node in self.nodes:
            if in_degree[node.oid] == 0:
                ready.append((-node.timestamp, node.oid))

        heapq.heapify(ready)

        order_key: dict[str, int] = {}
        current_order = 0

        while ready:
            _, oid = heapq.heappop(ready)

            # Skip if already processed (can happen with merge commits)
            if oid in order_key:
                continue

            order_key[oid] = current_order
            current_order += 1

            # Decrement in-degree of parents, add to ready if they hit 0
            node = self.oid_to_node[oid]
            for parent_oid in node.parent_oids:
                if parent_oid not in in_degree:
                    continue  # Parent not in our commit set
                in_degree[parent_oid] -= 1
                if in_degree[parent_oid] == 0:
                    parent_node = self.oid_to_node[parent_oid]
                    heapq.heappush(ready, (-parent_node.timestamp, parent_oid))

        return order_key

    def _assign_rows(self) -> None:
        """
        Assign rows using temporal contiguity algorithm.

        Process commits in topological order (with time tiebreaker),
        greedily packing into rows. Start new row when we hit an ancestor conflict.
        """
        # Get topological order with time tiebreaker
        order_keys = self._compute_order_keys()
        sorted_nodes = sorted(self.nodes, key=lambda n: order_keys.get(n.oid, float("inf")))

        current_row = 0
        current_row_nodes: list[CommitNode] = []

        for node in sorted_nodes:
            # Check if node can join current row (no ancestor relationship)
            can_join = True
            for existing in current_row_nodes:
                if self._is_ancestor(node, existing) or self._is_ancestor(existing, node):
                    can_join = False
                    break

            if can_join and current_row_nodes:
                # Add to current row
                current_row_nodes.append(node)
                node.row = current_row
            else:
                # Start new row
                if current_row_nodes:
                    current_row += 1
                current_row_nodes = [node]
                node.row = current_row

        self.num_rows = current_row + 1

    def _assign_columns(self) -> None:
        """
        Assign columns (lanes) to commits.

        Two-pass algorithm:
        1. First pass: assign lanes top-to-bottom, tracking claims
        2. Second pass: compute which lanes are active at each row

        Then reassign lanes to reuse freed lanes.
        """
        # Group nodes by row
        rows: dict[int, list[CommitNode]] = {}
        for node in self.nodes:
            if node.row not in rows:
                rows[node.row] = []
            rows[node.row].append(node)

        # For each commit, track which child "claimed" it
        claimed_by: dict[str, str] = {}  # parent_oid -> child_oid that claimed it

        # First pass: establish claims (top to bottom)
        # Process in row order to establish claim priority
        for row in range(self.num_rows):
            if row not in rows:
                continue
            # Sort nodes in each row by timestamp (newest first) for consistent ordering
            row_nodes = sorted(rows[row], key=lambda n: -n.timestamp)
            rows[row] = row_nodes

            for node in row_nodes:
                if node.parent_oids:
                    first_parent = node.parent_oids[0]
                    if first_parent not in claimed_by:
                        claimed_by[first_parent] = node.oid
                    else:
                        # Check if we should steal (lower row = processed earlier = more primary)
                        # But we don't have lanes yet... we'll handle stealing in second pass
                        pass

        # Second pass: assign lanes, reusing freed lanes
        commit_lane: dict[str, int] = {}
        # For each row, track which lanes have edges passing through
        # An edge passes through row R if: child is at row < R and parent is at row > R

        # Precompute: for each commit, what rows does its edge to first parent pass through?
        edge_rows: dict[str, set[int]] = {}  # commit_oid -> set of rows its edge passes through
        for node in self.nodes:
            if node.parent_oids:
                first_parent = node.parent_oids[0]
                if first_parent in self.oid_to_node:
                    parent_node = self.oid_to_node[first_parent]
                    # Edge passes through rows between node.row and parent_node.row (exclusive)
                    edge_rows[node.oid] = set(range(node.row + 1, parent_node.row))
                else:
                    edge_rows[node.oid] = set()
            else:
                edge_rows[node.oid] = set()

        max_lane = 0

        for row in range(self.num_rows):
            if row not in rows:
                continue

            row_nodes = rows[row]

            # Figure out which lanes are "blocked" at this row:
            # - lanes with commits at this row (will be computed as we assign)
            # - lanes with edges passing through this row
            blocked_lanes: set[int] = set()
            for oid, lanes_passing in edge_rows.items():
                if row in lanes_passing and oid in commit_lane:
                    blocked_lanes.add(commit_lane[oid])

            for node in row_nodes:
                # Check if a child already claimed us
                if node.oid in claimed_by:
                    child_oid = claimed_by[node.oid]
                    if child_oid in commit_lane:
                        node.column = commit_lane[child_oid]
                    else:
                        # Child not assigned yet? Shouldn't happen if processing top-down
                        lane = 0
                        while lane in blocked_lanes:
                            lane += 1
                        node.column = lane
                else:
                    # Allocate a lane - find lowest free lane
                    lane = 0
                    while lane in blocked_lanes:
                        lane += 1
                    node.column = lane

                commit_lane[node.oid] = node.column
                blocked_lanes.add(node.column)
                max_lane = max(max_lane, node.column)

                # Update claims - allow stealing based on lane number
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

    def _update_size(self) -> None:
        """Update widget size based on graph dimensions."""
        width = self.num_columns * self.COLUMN_WIDTH + 2 * self.PADDING
        height = self.num_rows * self.ROW_HEIGHT + 2 * self.PADDING
        self.setMinimumSize(width, height)
        self.setFixedSize(width, height)

    def _get_node_pos(self, node: CommitNode) -> tuple[int, int]:
        """Get the (x, y) position of a node's center."""
        x = self.PADDING + node.column * self.COLUMN_WIDTH + self.COLUMN_WIDTH // 2
        y = self.PADDING + node.row * self.ROW_HEIGHT + self.ROW_HEIGHT // 2
        return x, y

    def _get_lane_color(self, column: int) -> QColor:
        """Get color for a lane/column."""
        return self.LANE_COLORS[column % len(self.LANE_COLORS)]

    def paintEvent(self, event: object) -> None:  # noqa: N802 - Qt override
        """Paint the git graph."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw edges first (so they're behind nodes)
        for node in self.nodes:
            x1, y1 = self._get_node_pos(node)

            for parent_oid in node.parent_oids:
                if parent_oid not in self.oid_to_node:
                    continue

                parent = self.oid_to_node[parent_oid]
                x2, y2 = self._get_node_pos(parent)

                # Draw line from node to parent
                color = self._get_lane_color(node.column)
                pen = QPen(color, 2)
                painter.setPen(pen)
                painter.drawLine(x1, y1, x2, y2)

        # Draw nodes
        for node in self.nodes:
            x, y = self._get_node_pos(node)
            color = self._get_lane_color(node.column)

            # Draw circle
            painter.setBrush(color)
            painter.setPen(QPen(Qt.GlobalColor.black, 1))
            painter.drawEllipse(
                x - self.NODE_RADIUS,
                y - self.NODE_RADIUS,
                self.NODE_RADIUS * 2,
                self.NODE_RADIUS * 2,
            )

            # Draw text (short id and message)
            painter.setPen(Qt.GlobalColor.black)
            text = f"{node.short_id}: {node.message}"
            text_x = x + self.NODE_RADIUS + 5
            text_y = y + 5
            painter.drawText(text_x, text_y, text)


class GitGraphScrollArea(QScrollArea):
    """Scrollable container for the git graph."""

    def __init__(self, repo: ForgeRepository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repo = repo

        # Create the graph widget
        self.graph_widget = GitGraphWidget(repo)

        # Set up scroll area
        self.setWidget(self.graph_widget)
        self.setWidgetResizable(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def refresh(self) -> None:
        """Refresh the graph (reload commits and redraw)."""
        # Create a new graph widget
        self.graph_widget = GitGraphWidget(self.repo)
        self.setWidget(self.graph_widget)
