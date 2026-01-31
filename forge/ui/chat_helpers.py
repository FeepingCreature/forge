"""
Helper classes for AI chat widget.

Contains WebEngine-related helpers for the chat display.
"""

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QUrl, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebEngineCore import QWebEnginePage

if TYPE_CHECKING:
    from forge.ui.ai_chat_widget import AIChatWidget


class ExternalLinkPage(QWebEnginePage):
    """Custom page that opens links in external browser instead of navigating in-place"""

    def acceptNavigationRequest(  # noqa: N802 - Qt override
        self, url: QUrl | str, nav_type: QWebEnginePage.NavigationType, is_main_frame: bool
    ) -> bool:
        # Allow initial page load and JavaScript-driven updates
        if nav_type == QWebEnginePage.NavigationType.NavigationTypeTyped:
            return True

        # For link clicks, open externally
        if nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            # Convert to QUrl if string
            if isinstance(url, str):
                url = QUrl(url)
            QDesktopServices.openUrl(url)
            return False

        # Allow other navigation types (reloads, form submissions, etc.)
        return True


class ChatBridge(QObject):
    """Bridge object for JavaScript-to-Python communication"""

    def __init__(self, parent_widget: "AIChatWidget") -> None:
        super().__init__()
        self.parent_widget = parent_widget

    @Slot(str, bool)
    def handleToolApproval(self, tool_name: str, approved: bool) -> None:  # noqa: N802 - JS bridge
        """Handle tool approval from JavaScript"""
        self.parent_widget._handle_approval(tool_name, approved)

    @Slot(int)
    def handleRewind(self, message_index: int) -> None:  # noqa: N802 - JS bridge
        """Handle rewind to a specific message index"""
        self.parent_widget._handle_rewind(message_index)

    @Slot(str)
    def handleRewindToCommit(self, commit_oid: str) -> None:  # noqa: N802 - JS bridge
        """Handle rewind to a specific commit"""
        self.parent_widget._handle_rewind_to_commit(commit_oid)

    @Slot(int)
    def handleRewindToMessage(self, message_index: int) -> None:  # noqa: N802 - JS bridge
        """Handle rewind to a specific message index"""
        self.parent_widget._handle_rewind_to_message(message_index)

    @Slot(int)
    def handleRevertTurn(self, first_message_index: int) -> None:  # noqa: N802 - JS bridge
        """Handle reverting a turn (and all following turns)"""
        self.parent_widget._handle_revert_turn(first_message_index)

    @Slot(int)
    def handleRevertToTurn(self, first_message_index: int) -> None:  # noqa: N802 - JS bridge
        """Handle reverting TO a turn (keep this turn, undo later)"""
        self.parent_widget._handle_revert_to_turn(first_message_index)

    @Slot(int)
    def handleForkBeforeTurn(self, first_message_index: int) -> None:  # noqa: N802 - JS bridge
        """Handle forking from before a turn"""
        self.parent_widget._handle_fork_from_turn(first_message_index, before=True)

    @Slot(int)
    def handleForkAfterTurn(self, first_message_index: int) -> None:  # noqa: N802 - JS bridge
        """Handle forking from after a turn"""
        self.parent_widget._handle_fork_from_turn(first_message_index, before=False)
