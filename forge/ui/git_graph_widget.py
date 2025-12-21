"""
Git graph widget - visualizes commit history with temporal ordering.

See GRAPH_COMMIT_ORDERING.md for the algorithm details.
"""

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

    def _assign_rows(self) -> None:
        """
        Assign rows using temporal contiguity algorithm.

        Process commits newest-first, greedily packing into rows.
        Start new row when we hit an ancestor conflict.
        """
        # Sort by timestamp, newest first
        sorted_nodes = sorted(self.nodes, key=lambda n: -n.timestamp)

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

        Process from bottom (oldest) to top (newest).
        Each commit continues its first child's lane if possible.
        """
        # Group nodes by row
        rows: dict[int, list[CommitNode]] = {}
        for node in self.nodes:
            if node.row not in rows:
                rows[node.row] = []
            rows[node.row].append(node)

        # Build child map (parent_oid -> list of child nodes)
        children_of: dict[str, list[CommitNode]] = {}
        for node in self.nodes:
            for parent_oid in node.parent_oids:
                if parent_oid not in children_of:
                    children_of[parent_oid] = []
                children_of[parent_oid].append(node)

        # Track which lane each commit is in
        commit_lane: dict[str, int] = {}
        next_lane = 0

        # Process rows from bottom (oldest) to top (newest)
        for row in range(self.num_rows - 1, -1, -1):
            if row not in rows:
                continue

            row_nodes = rows[row]

            for node in row_nodes:
                # Try to continue in a child's lane
                child_lane = None
                if node.oid in children_of:
                    for child in children_of[node.oid]:
                        if child.oid in commit_lane:
                            child_lane = commit_lane[child.oid]
                            break

                if child_lane is not None:
                    # Continue in child's lane
                    node.column = child_lane
                    print(f"Row {row}: {node.short_id} continues child's lane {child_lane}")
                else:
                    # Allocate new lane
                    node.column = next_lane
                    print(f"Row {row}: {node.short_id} gets new lane {next_lane}")
                    next_lane += 1

                commit_lane[node.oid] = node.column

        self.num_columns = next_lane if next_lane > 0 else 1

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
