"""
Action system for Forge.

Provides a central registry of all commands/actions with configurable keybindings.
Actions can be triggered via keyboard shortcuts or the command palette.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QWidget


@dataclass
class Action:
    """Represents a command that can be triggered."""

    id: str  # Unique identifier, e.g. "file.save", "edit.undo"
    name: str  # Display name, e.g. "Save File", "Undo"
    callback: Callable[[], None]  # Function to call when triggered
    shortcut: str = ""  # Default keyboard shortcut, e.g. "Ctrl+S"
    category: str = "General"  # Category for grouping in command palette
    description: str = ""  # Optional longer description

    # Internal state
    _shortcut_obj: QShortcut | None = field(default=None, repr=False)


class ActionRegistry:
    """
    Central registry for all actions in the application.

    Actions are registered with default shortcuts. Users can override
    shortcuts via settings. The command palette uses this registry
    to show all available commands.
    """

    def __init__(self, parent_widget: QWidget) -> None:
        self.parent = parent_widget
        self._actions: dict[str, Action] = {}
        self._custom_shortcuts: dict[str, str] = {}  # action_id -> custom shortcut

    def register(
        self,
        action_id: str,
        name: str,
        callback: Callable[[], None],
        shortcut: str = "",
        category: str = "General",
        description: str = "",
    ) -> Action:
        """Register a new action."""
        action = Action(
            id=action_id,
            name=name,
            callback=callback,
            shortcut=shortcut,
            category=category,
            description=description,
        )
        self._actions[action_id] = action

        # Setup keyboard shortcut if provided
        effective_shortcut = self._custom_shortcuts.get(action_id, shortcut)
        if effective_shortcut:
            self._bind_shortcut(action, effective_shortcut)

        return action

    def get(self, action_id: str) -> Action | None:
        """Get an action by ID."""
        return self._actions.get(action_id)

    def get_all(self) -> list[Action]:
        """Get all registered actions."""
        return list(self._actions.values())

    def get_by_category(self) -> dict[str, list[Action]]:
        """Get actions grouped by category."""
        result: dict[str, list[Action]] = {}
        for action in self._actions.values():
            if action.category not in result:
                result[action.category] = []
            result[action.category].append(action)
        return result

    def trigger(self, action_id: str) -> bool:
        """Trigger an action by ID. Returns True if action was found and triggered."""
        action = self._actions.get(action_id)
        if action:
            action.callback()
            return True
        return False

    def set_shortcut(self, action_id: str, shortcut: str) -> bool:
        """
        Set a custom shortcut for an action.
        Pass empty string to remove custom shortcut (revert to default).
        """
        action = self._actions.get(action_id)
        if not action:
            return False

        # Remove old shortcut binding
        if action._shortcut_obj:
            action._shortcut_obj.setEnabled(False)
            action._shortcut_obj.deleteLater()
            action._shortcut_obj = None

        # Update custom shortcuts dict
        if shortcut:
            self._custom_shortcuts[action_id] = shortcut
        elif action_id in self._custom_shortcuts:
            del self._custom_shortcuts[action_id]

        # Bind new shortcut
        effective_shortcut = shortcut or action.shortcut
        if effective_shortcut:
            self._bind_shortcut(action, effective_shortcut)

        return True

    def get_effective_shortcut(self, action_id: str) -> str:
        """Get the current shortcut for an action (custom or default)."""
        action = self._actions.get(action_id)
        if not action:
            return ""
        return self._custom_shortcuts.get(action_id, action.shortcut)

    def _bind_shortcut(self, action: Action, shortcut: str) -> None:
        """Bind a keyboard shortcut to an action."""
        try:
            shortcut_obj = QShortcut(QKeySequence(shortcut), self.parent)
            shortcut_obj.activated.connect(action.callback)
            action._shortcut_obj = shortcut_obj
        except Exception as e:
            print(f"Failed to bind shortcut {shortcut} for {action.id}: {e}")

    def load_custom_shortcuts(self, shortcuts: dict[str, str]) -> None:
        """Load custom shortcuts from settings."""
        self._custom_shortcuts = shortcuts.copy()

        # Re-bind all actions with custom shortcuts
        for action_id, shortcut in shortcuts.items():
            action = self._actions.get(action_id)
            if action:
                # Remove old binding
                if action._shortcut_obj:
                    action._shortcut_obj.setEnabled(False)
                    action._shortcut_obj.deleteLater()
                    action._shortcut_obj = None

                # Bind new shortcut
                if shortcut:
                    self._bind_shortcut(action, shortcut)

    def get_custom_shortcuts(self) -> dict[str, str]:
        """Get all custom shortcuts for saving to settings."""
        return self._custom_shortcuts.copy()

    def clear_all_shortcuts(self) -> None:
        """Clear all shortcut bindings (useful before re-registering)."""
        for action in self._actions.values():
            if action._shortcut_obj:
                action._shortcut_obj.setEnabled(False)
                action._shortcut_obj.deleteLater()
                action._shortcut_obj = None
