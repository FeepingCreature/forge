"""
BranchTabWidget - Container for file tabs + AI chat within a single branch
"""

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QSplitter, QTabWidget, QVBoxLayout, QWidget

from forge.ui.branch_workspace import BranchWorkspace
from forge.ui.editor_widget import EditorWidget
from forge.ui.open_files_cache import get_open_files, save_open_files
from forge.ui.quick_open import QuickOpenWidget
from forge.ui.side_panel import SidePanelWidget

if TYPE_CHECKING:
    from forge.config.settings import Settings


class BranchTabWidget(QWidget):
    """
    Container widget for a single branch's workspace.

    Contains:
    - File tabs (QTabWidget) with AI chat as first tab
    - Routes all file operations through VFS
    - Manages open files within the branch
    """

    # Signals
    file_modified = Signal(str)  # Emitted when a file is modified (filepath)
    file_saved = Signal(str, str)  # Emitted when saved (filepath, commit_oid)
    file_opened = Signal(str)  # Emitted when a file is opened (filepath) - for AI context sync
    file_closed = Signal(
        str
    )  # Emitted when a file is closed (filepath) - NOT used for context removal
    context_file_added = Signal(str)  # Emitted when file added to context via explorer
    context_file_removed = Signal(str)  # Emitted when file removed from context via explorer
    ai_turn_started = Signal()  # Forwarded from AI chat
    ai_turn_finished = Signal(str)  # Forwarded from AI chat (commit_oid)

    def __init__(
        self,
        workspace: BranchWorkspace,
        settings: "Settings",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.settings = settings

        # Track editor widgets by filepath
        self._editors: dict[str, EditorWidget] = {}

        # Track modified state per file
        self._modified_files: set[str] = set()

        # Track AI chat widget for signals
        self._chat_widget: QWidget | None = None

        # Track if AI is currently working
        self._ai_working: bool = False

        # Side panel widget (contains explorer, search, ask)
        self._side_panel: SidePanelWidget | None = None

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the tab widget UI with side panel"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Main splitter: side panel | file tabs
        self.splitter = QSplitter()

        # Side panel (left side) - contains explorer, search, ask tabs
        api_key = self.settings.get_api_key()
        self._side_panel = SidePanelWidget(self.workspace, api_key)
        self._side_panel.file_open_requested.connect(self.open_file)
        self._side_panel.context_toggle_requested.connect(self._on_context_toggle)
        self._side_panel.search_file_selected.connect(self._on_search_file_selected)
        self._side_panel.setMinimumWidth(150)
        self._side_panel.setMaximumWidth(400)
        self.splitter.addWidget(self._side_panel)

        # File tabs (right side - AI chat will be first tab, added by parent)
        self.file_tabs = QTabWidget()
        self.file_tabs.setTabsClosable(True)
        self.file_tabs.setMovable(True)
        self.file_tabs.tabCloseRequested.connect(self._on_tab_close_requested)
        self.file_tabs.currentChanged.connect(self._on_tab_changed)
        self.splitter.addWidget(self.file_tabs)

        # Set initial splitter sizes (file explorer: 200px, rest for tabs)
        self.splitter.setSizes([200, 800])

        layout.addWidget(self.splitter)

        # Quick open widget (hidden by default, positioned at top)
        self._quick_open: QuickOpenWidget | None = None

        # Note: Ctrl+E is handled by ActionRegistry in main_window
        # but we keep a local shortcut for when focus is in this widget
        quick_open_shortcut = QShortcut(QKeySequence("Ctrl+E"), self)
        quick_open_shortcut.activated.connect(self.show_quick_open)

    def add_ai_chat_tab(self, chat_widget: QWidget) -> int:
        """
        Add the AI chat as the first tab.

        Returns the tab index.
        """
        index = self.file_tabs.insertTab(0, chat_widget, "🤖 AI Chat")
        # AI chat tab is not closable
        self.file_tabs.tabBar().setTabButton(
            0, self.file_tabs.tabBar().ButtonPosition.RightSide, None
        )

        # Store reference and connect signals
        self._chat_widget = chat_widget

        # Connect AI turn signals if the widget has them
        if hasattr(chat_widget, "ai_turn_started"):
            chat_widget.ai_turn_started.connect(self._on_ai_turn_started)
        if hasattr(chat_widget, "ai_turn_finished"):
            chat_widget.ai_turn_finished.connect(self._on_ai_turn_finished)
        if hasattr(chat_widget, "mid_turn_commit"):
            chat_widget.mid_turn_commit.connect(self._on_mid_turn_commit)

        return index

    def _on_ai_turn_started(self) -> None:
        """Handle AI turn starting - lock file editors"""
        self._ai_working = True
        self.set_read_only(True)

        # Update AI chat tab to show working indicator (remove waiting indicator)
        self.file_tabs.setTabText(0, "🤖 AI Chat ⏳")

        # Forward signal
        self.ai_turn_started.emit()

    def clear_waiting_indicator(self) -> None:
        """Clear the waiting-for-input indicator (called when user starts typing)"""
        if not self._ai_working:
            self.file_tabs.setTabText(0, "🤖 AI Chat")

    def _on_ai_turn_finished(self, commit_oid: str) -> None:
        """Handle AI turn finishing - unlock file editors and refresh"""
        self._ai_working = False
        self.set_read_only(False)

        # Show "waiting for input" indicator on AI chat tab
        self.file_tabs.setTabText(0, "🤖 AI Chat 💬")

        # Refresh all open files from VFS (AI may have changed them)
        if commit_oid:
            self.refresh_all_files()

        # Forward signal
        self.ai_turn_finished.emit(commit_oid)

    def _on_mid_turn_commit(self, commit_oid: str) -> None:
        """Handle mid-turn commit from AI - refresh UI without unlocking editors"""
        # Refresh VFS and side panel to show new/removed files
        self.workspace.refresh_vfs()
        if self._side_panel:
            self._side_panel.refresh()

        # Refresh any open files that may have changed
        for filepath in list(self._editors.keys()):
            self.refresh_file(filepath)

    def open_file(self, filepath: str) -> int:
        """
        Open a file in a new tab (or focus existing tab).

        Returns the tab index.
        """
        # Check if already open
        if filepath in self._editors:
            # Find and focus the tab
            for i in range(self.file_tabs.count()):
                if self.file_tabs.widget(i) == self._editors[filepath]:
                    self.file_tabs.setCurrentIndex(i)
                    return i

        # Create new editor
        editor = EditorWidget(filepath=filepath)

        # Load content from VFS
        try:
            content = self.workspace.get_file_content(filepath)
            editor.set_text(content)
        except FileNotFoundError:
            # New file - start empty
            editor.set_text("")

        # Connect text changed signal to track modifications
        editor.editor.textChanged.connect(lambda fp=filepath: self._on_file_modified(fp))

        # Add to tabs
        filename = Path(filepath).name
        index = self.file_tabs.addTab(editor, f"📄 {filename}")
        self.file_tabs.setCurrentIndex(index)

        # Track editor
        self._editors[filepath] = editor
        self.workspace.open_file(filepath)

        # Emit signal for AI context sync
        self.file_opened.emit(filepath)

        return index

    def close_file(self, filepath: str) -> bool:
        """
        Close a file tab.

        Returns True if closed, False if cancelled (e.g., unsaved changes).
        """
        if filepath not in self._editors:
            return False

        editor = self._editors[filepath]

        # Find tab index
        for i in range(self.file_tabs.count()):
            if self.file_tabs.widget(i) == editor:
                # Check for unsaved changes
                if filepath in self._modified_files:
                    # TODO: Prompt user to save
                    pass

                self.file_tabs.removeTab(i)
                break

        # Clean up
        del self._editors[filepath]
        self._modified_files.discard(filepath)
        self.workspace.close_file(filepath)

        # Emit signal for AI context sync
        self.file_closed.emit(filepath)

        return True

    def save_current_file(self) -> str | None:
        """
        Save the currently active file.

        Returns commit OID if saved, None if no file active or error.
        """
        current_widget = self.file_tabs.currentWidget()

        # Find which file this is
        filepath = None
        for fp, editor in self._editors.items():
            if editor == current_widget:
                filepath = fp
                break

        if filepath is None:
            return None

        return self.save_file(filepath)

    def save_file(self, filepath: str) -> str | None:
        """
        Save a specific file.

        Returns commit OID if saved, None on error.
        """
        if filepath not in self._editors:
            return None

        editor = self._editors[filepath]
        content = editor.get_text()

        # Write to VFS
        self.workspace.set_file_content(filepath, content)

        # Commit
        message = f"edit: {Path(filepath).name}"
        commit_oid = self.workspace.commit(message)

        # Clear modified state
        self._modified_files.discard(filepath)
        self._update_tab_title(filepath)

        # Emit signal
        self.file_saved.emit(filepath, commit_oid)

        return commit_oid

    def save_all_files(self) -> str | None:
        """
        Save all modified files in one commit.

        Returns commit OID if saved, None if nothing to save.
        """
        if not self._modified_files:
            return None

        # Write all modified files to VFS
        for filepath in list(self._modified_files):
            if filepath in self._editors:
                editor = self._editors[filepath]
                content = editor.get_text()
                self.workspace.set_file_content(filepath, content)

        # Generate commit message
        if len(self._modified_files) == 1:
            filepath = next(iter(self._modified_files))
            message = f"edit: {Path(filepath).name}"
        else:
            message = f"edit: {len(self._modified_files)} files"

        # Commit
        commit_oid = self.workspace.commit(message)

        # Clear all modified states
        for filepath in list(self._modified_files):
            self._modified_files.discard(filepath)
            self._update_tab_title(filepath)

        return commit_oid

    def refresh_file(self, filepath: str) -> None:
        """
        Refresh a file's content from VFS.

        Use after AI makes changes.
        """
        if filepath not in self._editors:
            return

        editor = self._editors[filepath]

        # Block signals while updating to avoid triggering modified state
        editor.editor.blockSignals(True)
        try:
            content = self.workspace.get_file_content(filepath)
            editor.set_text(content)
        finally:
            editor.editor.blockSignals(False)

        # Clear modified state (content is now from VFS)
        self._modified_files.discard(filepath)
        self._update_tab_title(filepath)

    def refresh_all_files(self) -> None:
        """Refresh all open files from VFS"""
        # First refresh the VFS to pick up new commits
        self.workspace.refresh_vfs()

        # Then refresh each editor
        for filepath in list(self._editors.keys()):
            self.refresh_file(filepath)

        # Also refresh the side panel (AI may have added/removed files)
        if self._side_panel:
            self._side_panel.refresh()

    def set_read_only(self, read_only: bool) -> None:
        """
        Set all file editors to read-only mode.

        Used during AI turns.
        """
        for editor in self._editors.values():
            editor.editor.setReadOnly(read_only)

    def has_unsaved_changes(self) -> bool:
        """Check if any files have unsaved changes"""
        return bool(self._modified_files)

    def get_modified_files(self) -> list[str]:
        """Get list of modified file paths"""
        return list(self._modified_files)

    def _on_file_modified(self, filepath: str) -> None:
        """Handle file modification"""
        if filepath not in self._modified_files:
            self._modified_files.add(filepath)
            self._update_tab_title(filepath)
            self.file_modified.emit(filepath)

    def _update_tab_title(self, filepath: str) -> None:
        """Update tab title to show modified state"""
        if filepath not in self._editors:
            return

        editor = self._editors[filepath]
        filename = Path(filepath).name

        # Find tab and update title
        for i in range(self.file_tabs.count()):
            if self.file_tabs.widget(i) == editor:
                if filepath in self._modified_files:
                    self.file_tabs.setTabText(i, f"📄 {filename} •")
                else:
                    self.file_tabs.setTabText(i, f"📄 {filename}")
                break

    def _on_tab_close_requested(self, index: int) -> None:
        """Handle tab close request"""
        widget = self.file_tabs.widget(index)

        # Find which file this is
        filepath = None
        for fp, editor in self._editors.items():
            if editor == widget:
                filepath = fp
                break

        if filepath:
            self.close_file(filepath)

    def _on_context_toggle(self, filepath: str, add_to_context: bool) -> None:
        """Handle context toggle from file explorer"""
        if add_to_context:
            self.context_file_added.emit(filepath)
        else:
            self.context_file_removed.emit(filepath)

    def _on_search_file_selected(self, filepath: str, line_num: int) -> None:
        """Handle file selection from search widget"""
        self.open_file(filepath)
        # TODO: Scroll to line_num in the editor

    def update_context_display(self, active_files: set[str]) -> None:
        """Update side panel to show which files are in context"""
        if self._side_panel:
            self._side_panel.set_context_files(active_files)

    def _on_tab_changed(self, index: int) -> None:
        """Handle tab change"""
        # Update workspace's active tab index
        self.workspace.active_tab_index = index

    def update_file_tab_tooltip(self, filepath: str, tokens: int) -> None:
        """Update a file tab's tooltip to show token count"""
        if filepath not in self._editors:
            return

        editor = self._editors[filepath]
        for i in range(self.file_tabs.count()):
            if self.file_tabs.widget(i) == editor:
                self.file_tabs.setTabToolTip(i, f"{filepath}\n~{tokens:,} tokens in context")
                break

    def show_quick_open(self) -> None:
        """Show the quick open popup (Ctrl+E)"""
        # Create quick open widget lazily
        if self._quick_open is None:
            self._quick_open = QuickOpenWidget(self.workspace, self)
            self._quick_open.file_selected.connect(self.open_file)
            self._quick_open.hide()

        # Position at top center of the file tabs area (in global coordinates)
        tabs_global_pos = self.file_tabs.mapToGlobal(self.file_tabs.rect().topLeft())
        tabs_width = self.file_tabs.width()

        popup_width = min(500, tabs_width - 40)
        popup_x = tabs_global_pos.x() + (tabs_width - popup_width) // 2
        popup_y = tabs_global_pos.y() + 10

        self._quick_open.setFixedWidth(popup_width)
        self._quick_open.move(popup_x, popup_y)
        self._quick_open.show()
        self._quick_open.raise_()

    def get_open_file_paths(self) -> list[str]:
        """Get list of currently open file paths (for persistence)"""
        return list(self._editors.keys())

    def restore_open_files(self, repo_path: str) -> None:
        """Restore open files from XDG cache"""
        files = get_open_files(repo_path, self.workspace.branch_name)
        for filepath in files:
            # Only open if file exists in VFS
            if self.workspace.vfs.file_exists(filepath):
                self.open_file(filepath)

    def save_open_files_to_cache(self, repo_path: str) -> None:
        """Save currently open files to XDG cache"""
        files = self.get_open_file_paths()
        save_open_files(repo_path, self.workspace.branch_name, files)
