"""Types and constants for git graph visualization."""

from dataclasses import dataclass, field

from PySide6.QtGui import QColor


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
