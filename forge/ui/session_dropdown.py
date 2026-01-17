"""
Session dropdown widget - shows all sessions and their states.

Replaces the simple "+" button with a dropdown that shows:
- All registered sessions with their states (running, idle, waiting, etc.)
- Parent/child relationships
- Branches with sessions that aren't currently open
- Quick actions (new session, new branch)
"""

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QWidget,
    QWidgetAction,
)

if TYPE_CHECKING:
    from forge.git_backend.repository import ForgeRepository


# State display configuration
STATE_ICONS = {
    "idle": "⏸️",
    "running": "🔄",
    "waiting_approval": "⚠️",
    "waiting_input": "💬",
    "waiting_children": "⏳",
    "completed": "✅",
    "error": "❌",
}

STATE_LABELS = {
    "idle": "Idle",
    "running": "Running",
    "waiting_approval": "Needs Approval",
    "waiting_input": "Waiting for Input",
    "waiting_children": "Waiting on Children",
    "completed": "Completed",
    "error": "Error",
}


class SessionItemWidget(QFrame):
    """Widget displaying a single session in the dropdown."""

    clicked = Signal(str)  # branch_name

    def __init__(self, branch_name: str, state_info: dict[str, Any], parent: QWidget | None = None):
        super().__init__(parent)
        self.branch_name = branch_name
        self.state_info = state_info

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        # State icon
        state = state_info.get("state", "idle")
        icon = STATE_ICONS.get(state, "❓")
        icon_label = QLabel(icon)
        icon_label.setFixedWidth(20)
        layout.addWidget(icon_label)

        # Branch name (with indentation for children)
        name_text = branch_name
        if state_info.get("is_child"):
            name_text = f"↳ {branch_name}"
        name_label = QLabel(name_text)
        name_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(name_label, stretch=1)

        # State text
        state_text = STATE_LABELS.get(state, state)
        state_label = QLabel(state_text)
        state_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(state_label)

        # Hover effect
        self.setStyleSheet("""
            SessionItemWidget:hover {
                background-color: palette(highlight);
            }
        """)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        """Handle click to select this session."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.branch_name)
        super().mousePressEvent(event)


class SessionDropdown(QWidget):
    """
    Dropdown widget showing all sessions and their states.

    Replaces the "+" button in the corner of the branch tab widget.
    """

    # Signals
    session_selected = Signal(str)  # branch_name - user wants to open/focus this session
    new_session_requested = Signal()
    new_branch_requested = Signal()

    def __init__(self, repo: "ForgeRepository | None" = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.repo = repo

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Main dropdown button
        self.dropdown_btn = QPushButton("Sessions ▾")
        self.dropdown_btn.setFixedHeight(24)
        self.dropdown_btn.setToolTip("View all sessions")
        self.dropdown_btn.clicked.connect(self._show_dropdown)
        layout.addWidget(self.dropdown_btn)

        # Connect to registry for live updates
        from forge.session.registry import SESSION_REGISTRY

        SESSION_REGISTRY.session_registered.connect(self._on_session_changed)
        SESSION_REGISTRY.session_unregistered.connect(self._on_session_changed)
        SESSION_REGISTRY.session_state_changed.connect(self._on_state_changed)

        # Also listen for branch changes from the repo (for newly spawned sessions)
        if self.repo:
            self.repo.signals.branches_changed.connect(self._on_branches_changed)

        self._update_button_text()

    def _update_button_text(self) -> None:
        """Update button text to show session count and any needing attention."""
        from forge.session.registry import SESSION_REGISTRY

        states = SESSION_REGISTRY.get_session_states()
        total = len(states)

        # Count sessions needing attention
        attention_states = {"waiting_input", "waiting_approval", "error"}
        attention_count = sum(
            1 for info in states.values() if info.get("state") in attention_states
        )

        if attention_count > 0:
            self.dropdown_btn.setText(f"Sessions ({attention_count}!) ▾")
            self.dropdown_btn.setStyleSheet("color: orange; font-weight: bold;")
        elif total > 0:
            self.dropdown_btn.setText(f"Sessions ({total}) ▾")
            self.dropdown_btn.setStyleSheet("")
        else:
            self.dropdown_btn.setText("Sessions ▾")
            self.dropdown_btn.setStyleSheet("")

    def _on_session_changed(self, branch_name: str) -> None:
        """Handle session registered/unregistered."""
        self._update_button_text()

    def _on_state_changed(self, branch_name: str, new_state: str) -> None:
        """Handle session state change."""
        self._update_button_text()

    def _on_branches_changed(self) -> None:
        """Handle branch created/deleted in the repo.

        This ensures newly spawned session branches appear in the dropdown.
        The dropdown menu is rebuilt each time it's shown, so we just need
        to update the button text here - the new branches will appear when
        the user next opens the dropdown.
        """
        self._update_button_text()

    def _show_dropdown(self) -> None:
        """Show the dropdown menu with all sessions."""
        import json

        from forge.session.registry import SESSION_REGISTRY

        menu = QMenu(self)

        # Get all session states from registry (currently open sessions)
        states = SESSION_REGISTRY.get_session_states()
        open_branches = set(states.keys())

        # Also find branches with sessions that aren't open
        closed_sessions: list[str] = []
        if self.repo:
            for branch_name in self.repo.repo.branches.local:
                if branch_name not in open_branches:
                    # Check if this branch has a session file
                    try:
                        content = self.repo.get_file_content(".forge/session.json", branch_name)
                        json.loads(content)  # Validate it's valid JSON
                        closed_sessions.append(branch_name)
                    except (FileNotFoundError, KeyError, json.JSONDecodeError):
                        pass

        has_any = bool(states) or bool(closed_sessions)

        if states:
            # Group by parent/child relationship
            # First show top-level sessions (no parent)
            top_level = {k: v for k, v in states.items() if not v.get("is_child")}

            for branch_name, state_info in sorted(top_level.items()):
                self._add_session_to_menu(menu, branch_name, state_info)

                # Add children indented
                for child_name in state_info.get("children", []):
                    if child_name in states:
                        self._add_session_to_menu(menu, child_name, states[child_name], indent=True)

            # Show orphan children (parent not in registry)
            orphans = {
                k: v
                for k, v in states.items()
                if v.get("is_child") and v.get("parent") not in states
            }
            if orphans:
                menu.addSeparator()
                orphan_label = QWidgetAction(menu)
                label = QLabel("  Orphaned sessions:")
                label.setStyleSheet("color: #888; font-size: 11px;")
                orphan_label.setDefaultWidget(label)
                menu.addAction(orphan_label)
                for branch_name, state_info in sorted(orphans.items()):
                    self._add_session_to_menu(menu, branch_name, state_info)

        # Show closed sessions (have session file but not open)
        if closed_sessions:
            if states:
                menu.addSeparator()
            closed_label = QWidgetAction(menu)
            label = QLabel("  Other sessions:")
            label.setStyleSheet("color: #888; font-size: 11px;")
            closed_label.setDefaultWidget(label)
            menu.addAction(closed_label)

            for branch_name in sorted(closed_sessions):
                action = menu.addAction(f"    💤 {branch_name}")
                action.setToolTip("Session not open - click to open")
                action.triggered.connect(
                    lambda checked=False, bn=branch_name: self.session_selected.emit(bn)
                )

        if not has_any:
            no_sessions = menu.addAction("No sessions")
            no_sessions.setEnabled(False)

        menu.addSeparator()

        # New session/branch actions
        new_session = menu.addAction("🤖 New AI Session...")
        new_session.triggered.connect(self.new_session_requested.emit)

        new_branch = menu.addAction("🌿 New Branch...")
        new_branch.triggered.connect(self.new_branch_requested.emit)

        # Show menu below button
        menu.exec(self.dropdown_btn.mapToGlobal(self.dropdown_btn.rect().bottomLeft()))

    def _add_session_to_menu(
        self, menu: QMenu, branch_name: str, state_info: dict[str, Any], indent: bool = False
    ) -> None:
        """Add a session item to the menu."""
        state = state_info.get("state", "idle")
        icon = STATE_ICONS.get(state, "❓")
        state_text = STATE_LABELS.get(state, state)

        prefix = "    " if indent else ""
        child_marker = "↳ " if state_info.get("is_child") else ""

        action = menu.addAction(f"{prefix}{icon} {child_marker}{branch_name}")
        action.setToolTip(f"State: {state_text}")
        action.triggered.connect(
            lambda checked=False, bn=branch_name: self.session_selected.emit(bn)
        )
