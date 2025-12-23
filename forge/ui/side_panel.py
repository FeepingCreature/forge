"""
Side panel widget with tabbed interface for Explorer, Search, and Ask Repo.
"""

from typing import TYPE_CHECKING

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from forge.ui.file_explorer_widget import FileExplorerWidget

if TYPE_CHECKING:
    from forge.ui.branch_workspace import BranchWorkspace


class SidePanelWidget(QWidget):
    """
    Side panel with tabs for Explorer, Search, and Ask Repo.

    All three tools are always available without opening dialogs.
    """

    # Forward signals from file explorer
    file_open_requested = Signal(str)  # filepath
    context_toggle_requested = Signal(str, bool)  # filepath, add_to_context

    # Forward signals from search and ask
    search_file_selected = Signal(str, int)  # filepath, line_number
    ask_file_selected = Signal(str, int, int)  # filepath, start_line, end_line

    def __init__(
        self,
        workspace: "BranchWorkspace",
        api_key: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self._api_key = api_key

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the tabbed interface"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.tabs = QTabWidget()
        self.tabs.setTabPosition(QTabWidget.TabPosition.North)

        # Explorer tab
        self._file_explorer = FileExplorerWidget(self.workspace)
        self._file_explorer.file_open_requested.connect(self.file_open_requested.emit)
        self._file_explorer.context_toggle_requested.connect(self.context_toggle_requested.emit)
        self.tabs.addTab(self._file_explorer, "📁 Explorer")

        # Search tab
        from forge.ui.search_widget import SearchWidget

        self._search = SearchWidget(self.workspace)
        self._search.file_selected.connect(self.search_file_selected.emit)
        self.tabs.addTab(self._search, "🔍 Search")

        # Ask Repo tab
        from forge.ui.ask_widget import AskWidget

        self._ask = AskWidget(self.workspace, self._api_key)
        self._ask.file_selected.connect(self.ask_file_selected.emit)
        self.tabs.addTab(self._ask, "💬 Ask")

        layout.addWidget(self.tabs)

    def refresh(self) -> None:
        """Refresh the file explorer"""
        self._file_explorer.refresh()

    def set_context_files(self, context_files: set[str]) -> None:
        """Update which files are shown as being in AI context"""
        self._file_explorer.set_context_files(context_files)

    def focus_search(self) -> None:
        """Switch to search tab and focus the input"""
        self.tabs.setCurrentWidget(self._search)
        self._search.focus_input()

    def focus_ask(self) -> None:
        """Switch to ask tab and focus the input"""
        self.tabs.setCurrentWidget(self._ask)
        self._ask.focus_input()

    def set_summaries(self, summaries: dict[str, str]) -> None:
        """Pass repository summaries to the Ask widget"""
        self._ask.set_summaries(summaries)
