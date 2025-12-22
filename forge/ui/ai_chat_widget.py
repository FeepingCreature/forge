"""
AI chat widget with markdown/LaTeX rendering
"""

import json
from typing import TYPE_CHECKING, Any

import markdown
from PySide6.QtCore import QEvent, QObject, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QKeyEvent
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from forge.git_backend.repository import ForgeRepository
from forge.llm.client import LLMClient
from forge.session.manager import SessionManager
from forge.ui.diff_view import (
    get_diff_styles,
    render_completed_tool_html,
    render_streaming_tool_html,
)

if TYPE_CHECKING:
    from forge.config.settings import Settings


class SummaryWorker(QObject):
    """Worker for generating repository summaries in background"""

    finished = Signal(int)  # Emitted with number of summaries generated
    error = Signal(str)  # Emitted on error
    progress = Signal(int, int, str)  # Emitted for progress (current, total, filepath)

    def __init__(self, session_manager: SessionManager) -> None:
        super().__init__()
        self.session_manager = session_manager

    def run(self) -> None:
        """Generate summaries"""
        try:
            self.session_manager.generate_repo_summaries(
                progress_callback=lambda cur, total, fp: self.progress.emit(cur, total, fp)
            )
            count = len(self.session_manager.repo_summaries)
            self.finished.emit(count)
        except Exception as e:
            self.error.emit(str(e))


class StreamWorker(QObject):
    """Worker for handling streaming LLM responses in a separate thread"""

    chunk_received = Signal(str)  # Emitted for each text chunk
    tool_call_delta = Signal(int, dict)  # Emitted for tool call updates (index, current_state)
    finished = Signal(dict)  # Emitted when stream is complete
    error = Signal(str)  # Emitted on error

    def __init__(
        self,
        client: LLMClient,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> None:
        super().__init__()
        self.client = client
        self.messages = messages
        self.tools = tools
        self.current_content = ""
        self.current_tool_calls: list[dict[str, Any]] = []

    def run(self) -> None:
        """Run the streaming request"""
        try:
            for chunk in self.client.chat_stream(self.messages, self.tools):
                if "choices" not in chunk or not chunk["choices"]:
                    continue

                delta = chunk["choices"][0].get("delta", {})

                # Handle content chunks
                if "content" in delta and delta["content"]:
                    content = delta["content"]
                    self.current_content += content
                    self.chunk_received.emit(content)

                # Handle tool calls
                if "tool_calls" in delta:
                    for tool_call_delta in delta["tool_calls"]:
                        index = tool_call_delta.get("index", 0)

                        # Ensure we have enough tool calls in the list
                        while len(self.current_tool_calls) <= index:
                            self.current_tool_calls.append(
                                {
                                    "id": "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                }
                            )

                        # Update tool call
                        if "id" in tool_call_delta:
                            self.current_tool_calls[index]["id"] = tool_call_delta["id"]
                        if "function" in tool_call_delta:
                            func = tool_call_delta["function"]
                            if "name" in func:
                                self.current_tool_calls[index]["function"]["name"] = func["name"]
                            if "arguments" in func:
                                self.current_tool_calls[index]["function"]["arguments"] += func[
                                    "arguments"
                                ]

                        # Emit delta with current state of this tool call
                        self.tool_call_delta.emit(index, self.current_tool_calls[index].copy())

            # Emit final result
            result = {
                "content": self.current_content if self.current_content else None,
                "tool_calls": self.current_tool_calls if self.current_tool_calls else None,
            }
            self.finished.emit(result)

        except Exception as e:
            self.error.emit(str(e))


class ToolExecutionWorker(QObject):
    """Worker for executing tool calls in a background thread.

    Claims VFS thread ownership while running, releases when done.
    """

    tool_started = Signal(str, dict)  # Emitted when a tool starts (name, args)
    tool_finished = Signal(
        str, str, dict, dict
    )  # Emitted when a tool finishes (tool_call_id, name, args, result)
    all_finished = Signal(list)  # Emitted when all tools complete (list of results)
    error = Signal(str)  # Emitted on error

    def __init__(
        self,
        tool_calls: list[dict[str, Any]],
        tool_manager: Any,
        session_manager: Any,
    ) -> None:
        super().__init__()
        self.tool_calls = tool_calls
        self.tool_manager = tool_manager
        self.session_manager = session_manager
        self.results: list[dict[str, Any]] = []

    def run(self) -> None:
        """Execute all tool calls"""
        # Claim VFS for this thread
        self.session_manager.vfs.claim_thread()
        try:
            for tool_call in self.tool_calls:
                tool_name = tool_call["function"]["name"]
                arguments_str = tool_call["function"]["arguments"]

                # Parse arguments
                try:
                    tool_args = json.loads(arguments_str) if arguments_str else {}
                except json.JSONDecodeError as e:
                    result = {"error": f"Invalid JSON arguments: {e}"}
                    tool_call_id = tool_call.get("id", "")
                    # Still emit tool_finished so tool_result gets recorded
                    # (Anthropic requires every tool_use to have a tool_result)
                    self.tool_finished.emit(tool_call_id, tool_name, {}, result)
                    self.results.append(
                        {
                            "tool_call": tool_call,
                            "args": {},
                            "result": result,
                            "parse_error": True,
                        }
                    )
                    continue

                # Emit that we're starting this tool
                self.tool_started.emit(tool_name, tool_args)

                # Execute tool
                result = self.tool_manager.execute_tool(tool_name, tool_args, self.session_manager)

                # Get tool_call_id before emitting - it's right here in tool_call
                tool_call_id = tool_call.get("id", "")

                # Emit result with tool_call_id
                self.tool_finished.emit(tool_call_id, tool_name, tool_args, result)

                self.results.append(
                    {
                        "tool_call": tool_call,
                        "args": tool_args,
                        "result": result,
                    }
                )

            self.all_finished.emit(self.results)

        except Exception as e:
            self.error.emit(str(e))
        finally:
            # Always release VFS ownership
            self.session_manager.vfs.release_thread()


class ExternalLinkPage(QWebEnginePage):
    """Custom page that opens links in external browser instead of navigating in-place"""

    def acceptNavigationRequest(
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
    def handleToolApproval(self, tool_name: str, approved: bool) -> None:
        """Handle tool approval from JavaScript"""
        self.parent_widget._handle_approval(tool_name, approved)


class AIChatWidget(QWidget):
    """AI chat interface with rich markdown rendering"""

    # Signals for AI turn lifecycle
    ai_turn_started = Signal()  # Emitted when AI turn begins
    ai_turn_finished = Signal(str)  # Emitted when AI turn ends (commit_oid or empty string)
    context_changed = Signal(set)  # Emitted when active files change (set of filepaths)
    context_stats_updated = Signal(dict)  # Emitted with token counts for status bar

    def __init__(
        self,
        session_data: dict[str, Any] | None = None,
        settings: "Settings | None" = None,
        repo: ForgeRepository | None = None,
        branch_name: str | None = None,
    ) -> None:
        super().__init__()
        # Branch name is the session identity (no UUID needed)
        assert branch_name is not None, "branch_name is required for AIChatWidget"
        self.branch_name = branch_name
        self.messages = []
        self.settings = settings
        self.repo = repo
        self.is_processing = False
        self.streaming_content = ""

        # Tool approval tracking - initialize BEFORE any method calls
        self.pending_approvals: dict[str, dict[str, Any]] = {}  # tool_name -> tool_info
        self.handled_approvals: set[str] = set()  # Tools that have been approved/rejected

        # Streaming worker
        self.stream_thread: QThread | None = None
        self.stream_worker: StreamWorker | None = None
        self._is_streaming = False
        self._streaming_tool_calls: list[dict[str, Any]] = []  # Track streaming tool calls
        self._cancel_requested = False  # Track if user requested cancellation

        # Tool execution worker
        self.tool_thread: QThread | None = None
        self.tool_worker: ToolExecutionWorker | None = None

        # Summary worker
        self.summary_thread: QThread | None = None
        self.summary_worker: SummaryWorker | None = None

        # Web channel bridge for JavaScript communication
        self.bridge = ChatBridge(self)
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)

        # Initialize session manager (repo and settings are required)
        assert repo is not None, "Repository is required for AIChatWidget"
        assert settings is not None, "Settings are required for AIChatWidget"
        self.session_manager = SessionManager(repo, self.branch_name, settings)

        # Load existing session messages and restore prompt manager state
        if session_data:
            self.messages = session_data.get("messages", [])
            # Restore messages to prompt manager
            for msg in self.messages:
                if msg.get("_ui_only"):
                    continue  # Skip UI-only messages
                role = msg.get("role")
                content = msg.get("content", "")
                if role == "user":
                    self.session_manager.append_user_message(content)
                elif role == "assistant":
                    if "tool_calls" in msg:
                        # Pass both tool_calls and content (content may be the AI's reasoning)
                        self.session_manager.append_tool_call(msg["tool_calls"], content)

                        # Replay compact tool calls - apply compaction immediately
                        for tc in msg["tool_calls"]:
                            func = tc.get("function", {})
                            if func.get("name") == "compact":
                                try:
                                    args = json.loads(func.get("arguments", "{}"))
                                    compact_ids = args.get("tool_call_ids", [])
                                    summary = args.get("summary", "")
                                    compacted, _ = self.session_manager.compact_tool_results(
                                        compact_ids, summary
                                    )
                                    print(f"📦 Replayed compaction: {compacted} tool result(s)")
                                except (json.JSONDecodeError, TypeError):
                                    pass  # Malformed args, skip
                    elif content:
                        self.session_manager.append_assistant_message(content)
                elif role == "tool":
                    tool_call_id = msg.get("tool_call_id", "")
                    self.session_manager.append_tool_result(tool_call_id, content)
            # Note: active_files are restored by MainWindow opening file tabs
            # The file_opened signals will sync them to SessionManager

            # Ensure CLAUDE.md and AGENTS.md are always in context for restored sessions
            for instructions_file in ["CLAUDE.md", "AGENTS.md"]:
                if self.session_manager.vfs.file_exists(instructions_file):
                    self.session_manager.add_active_file(instructions_file)

        # Setup UI BEFORE any operations that might call add_message()
        self._setup_ui()

        # Generate repository summaries on session creation (if not already done)
        if not self.session_manager.repo_summaries:
            self._add_system_message("🔍 Generating repository summaries in background...")
            self._start_summary_generation()
        else:
            # Summaries already exist (restored session) - emit initial context stats
            self._emit_context_stats()

        self._update_chat_display()
        self._check_for_unapproved_tools()

    def _setup_ui(self) -> None:
        """Setup the chat UI"""
        layout = QVBoxLayout(self)

        # Chat display area (using QWebEngineView for markdown/LaTeX)
        self.chat_view = QWebEngineView()

        # Use custom page that opens links externally
        custom_page = ExternalLinkPage(self.chat_view)
        self.chat_view.setPage(custom_page)

        # Log JavaScript console messages to stdout for debugging
        self.chat_view.page().javaScriptConsoleMessage = self._on_js_console_message  # type: ignore

        # Set up web channel for JavaScript communication
        self.chat_view.page().setWebChannel(self.channel)

        # Initialize with stable HTML shell - content will be injected via JavaScript
        # Connect to loadFinished to know when we can safely call updateMessages()
        self._shell_ready = False
        self.chat_view.loadFinished.connect(self._on_shell_loaded)
        self._init_chat_shell()

        layout.addWidget(self.chat_view)

        # Input area
        input_layout = QHBoxLayout()

        self.input_field = QTextEdit()
        self.input_field.setMaximumHeight(60)  # Smaller, ~2-3 lines
        self.input_field.setPlaceholderText(
            "Type your message... (Enter to send, Shift+Enter for new line)"
        )
        # Install event filter to catch Enter key
        self.input_field.installEventFilter(self)

        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self._send_message)
        self.send_button.setMaximumWidth(80)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self._cancel_ai_turn)
        self.cancel_button.setMaximumWidth(80)
        self.cancel_button.setEnabled(False)
        self.cancel_button.hide()

        input_layout.addWidget(self.input_field)
        input_layout.addWidget(self.send_button)
        input_layout.addWidget(self.cancel_button)

        layout.addLayout(input_layout)

    def _start_summary_generation(self) -> None:
        """Start generating repository summaries in background thread"""
        self.summary_thread = QThread()
        self.summary_worker = SummaryWorker(self.session_manager)
        self.summary_worker.moveToThread(self.summary_thread)

        # Connect signals
        self.summary_worker.finished.connect(self._on_summaries_finished)
        self.summary_worker.error.connect(self._on_summaries_error)
        self.summary_worker.progress.connect(self._on_summaries_progress)
        self.summary_thread.started.connect(self.summary_worker.run)

        # Track the message index for in-place updates
        self._summary_message_index = len(self.messages) - 1  # Index of the "Generating..." message

        # Start the thread
        self.summary_thread.start()

    def _on_summaries_progress(self, current: int, total: int, filepath: str) -> None:
        """Handle summary generation progress update"""
        if total == 0:
            return

        # Build progress bar
        bar_width = 20
        filled = int(bar_width * current / total)
        bar = "█" * filled + "░" * (bar_width - filled)
        percent = int(100 * current / total)

        # Truncate filepath if too long
        display_path = filepath if len(filepath) <= 40 else "..." + filepath[-37:]

        progress_text = (
            f"🔍 Generating summaries [{bar}] {percent}% ({current}/{total})\n`{display_path}`"
        )

        # Update the existing message in place
        if hasattr(self, "_summary_message_index") and self._summary_message_index < len(
            self.messages
        ):
            self.messages[self._summary_message_index]["content"] = progress_text
            self._update_chat_display()

    def _on_summaries_finished(self, count: int) -> None:
        """Handle summary generation completion"""
        # Clean up thread
        if self.summary_thread:
            self.summary_thread.quit()
            self.summary_thread.wait()
            self.summary_thread = None
            self.summary_worker = None

        # Set summaries in prompt manager (one-time snapshot for this session)
        self.session_manager.prompt_manager.set_summaries(self.session_manager.repo_summaries)

        # Auto-add CLAUDE.md and AGENTS.md to context if they exist
        # These files contain important project-specific instructions for the AI
        for instructions_file in ["CLAUDE.md", "AGENTS.md"]:
            if self.session_manager.vfs.file_exists(instructions_file):
                self.session_manager.add_active_file(instructions_file)

        # Emit context changed signal if we added any files
        if self.session_manager.active_files:
            self.context_changed.emit(self.session_manager.active_files.copy())

        # Update the progress message to show completion
        if hasattr(self, "_summary_message_index") and self._summary_message_index < len(
            self.messages
        ):
            self.messages[self._summary_message_index]["content"] = (
                f"✅ Generated summaries for {count} files"
            )
            self._update_chat_display(scroll_to_bottom=True)
        else:
            self._add_system_message(f"✅ Generated summaries for {count} files")

        # Emit initial context stats now that summaries are ready
        self._emit_context_stats()

    def _on_summaries_error(self, error_msg: str) -> None:
        """Handle summary generation error"""
        # Clean up thread
        if self.summary_thread:
            self.summary_thread.quit()
            self.summary_thread.wait()
            self.summary_thread = None
            self.summary_worker = None

        self._add_system_message(f"❌ Error generating summaries: {error_msg}")

    def _check_for_unapproved_tools(self) -> None:
        """Check for unapproved tools and show approval requests in chat"""
        unapproved = self.session_manager.tool_manager.get_unapproved_tools()

        if unapproved:
            for tool_name, current_code, is_new, old_code in unapproved:
                # Check if this tool has already been handled in this session
                # by checking if it's in the approved_tools.json file
                if self.session_manager.tool_manager.is_tool_approved(tool_name):
                    # Already handled, mark it so buttons render disabled
                    self.handled_approvals.add(tool_name)
                    continue

                self.pending_approvals[tool_name] = {
                    "code": current_code,
                    "is_new": is_new,
                    "old_code": old_code,
                }

                # Add approval request to chat with interactive buttons
                status = "New Tool" if is_new else "Modified Tool"
                self.add_message(
                    "system",
                    f"⚠️ **{status} Requires Approval: `{tool_name}`**\n\n"
                    f"Review this tool carefully. Once approved, it will run autonomously.\n\n"
                    f"```python\n{current_code}\n```\n\n"
                    f'<div class="approval-buttons">'
                    f"<button onclick=\"approveTool('{tool_name}', this)\">✅ Approve</button>"
                    f"<button onclick=\"rejectTool('{tool_name}', this)\">❌ Reject</button>"
                    f"</div>",
                )

            self._update_blocking_state()

    def _handle_approval(self, tool_name: str, approved: bool) -> None:
        """Handle tool approval/rejection command"""
        if tool_name not in self.pending_approvals:
            self.add_message("system", f"❌ Unknown tool: {tool_name}")
            return

        self.input_field.clear()

        # Mark as handled so buttons render disabled on reload
        self.handled_approvals.add(tool_name)

        if approved:
            self.session_manager.tool_manager.approve_tool(tool_name)
            self.add_message("system", f"✅ Approved tool: `{tool_name}`")
        else:
            self.session_manager.tool_manager.reject_tool(tool_name)
            self.add_message("system", f"❌ Rejected tool: `{tool_name}`")

        del self.pending_approvals[tool_name]
        self._update_blocking_state()

        # If all approvals done, commit them
        if not self.pending_approvals:
            new_commit_oid = self.session_manager.tool_manager.commit_pending_approvals()
            if new_commit_oid:
                self.add_message(
                    "system", f"✅ Tool approvals committed: {str(new_commit_oid)[:8]}"
                )
            # Clear handled approvals for next batch
            self.handled_approvals.clear()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """Filter events to catch Enter key in input field"""
        if obj == self.input_field and event.type() == QEvent.Type.KeyPress:
            # Cast to QKeyEvent to access key-specific attributes
            assert isinstance(event, QKeyEvent)
            key_event = event
            # Check if it's Enter without Shift
            is_enter = key_event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            is_shift_pressed = bool(key_event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if is_enter and not is_shift_pressed:
                # Enter pressed without Shift - send message
                self._send_message()
                return True  # Event handled
        return super().eventFilter(obj, event)

    def _update_blocking_state(self) -> None:
        """Update UI blocking state based on pending approvals"""
        if self.pending_approvals:
            # Block input while approvals pending
            self.input_field.setEnabled(False)
            self.send_button.setEnabled(False)
            self.input_field.setPlaceholderText(
                f"⚠️ {len(self.pending_approvals)} tool(s) require approval. "
                "Click Approve or Reject buttons above."
            )
        else:
            # Unblock input (unless processing)
            if not self.is_processing:
                self.input_field.setEnabled(True)
                self.send_button.setEnabled(True)
                self.input_field.setPlaceholderText(
                    "Type your message... (Enter to send, Shift+Enter for new line)"
                )

    def add_file_to_context(self, filepath: str) -> None:
        """Add a file to the AI context"""
        # Never add session.json to context - it contains the conversation history
        # which would duplicate context and waste tokens
        if filepath == ".forge/session.json":
            return
        self.session_manager.add_active_file(filepath)
        self.context_changed.emit(self.session_manager.active_files.copy())
        self._emit_context_stats()

    def remove_file_from_context(self, filepath: str) -> None:
        """Remove a file from the AI context"""
        self.session_manager.remove_active_file(filepath)
        self.context_changed.emit(self.session_manager.active_files.copy())
        self._emit_context_stats()

    def get_active_files(self) -> set[str]:
        """Get the set of files currently in AI context"""
        return self.session_manager.active_files.copy()

    def _emit_context_stats(self) -> None:
        """Emit context stats for status bar updates"""
        stats = self.session_manager.get_active_files_with_stats()
        self.context_stats_updated.emit(stats)

    def get_context_stats(self) -> dict[str, Any]:
        """Get current context statistics"""
        return self.session_manager.get_active_files_with_stats()

    def check_unsaved_changes(self) -> bool:
        """
        Check if there are unsaved changes that should be saved before AI turn.

        Returns True if OK to proceed, False if should abort.
        Override point for parent widgets to inject save logic.
        """
        # Default implementation: always proceed
        # BranchTabWidget will connect to this
        return True

    # Callback for parent to set - returns True if OK to proceed
    unsaved_changes_check: Any = None

    def _check_workdir_state(self) -> bool:
        """
        Check if working directory is clean when working on the checked-out branch.

        If the target branch is currently checked out and has uncommitted changes
        in the working directory, we need to warn the user - those changes would
        be overwritten when we sync the workdir after committing.

        Returns True if OK to proceed, False to abort.
        """
        from PySide6.QtWidgets import QMessageBox

        # repo is guaranteed non-None by __init__ assertion
        assert self.repo is not None

        # Check if we're working on the checked-out branch
        checked_out = self.repo.get_checked_out_branch()
        if checked_out != self.branch_name:
            # Not working on the checked-out branch, no workdir concerns
            return True

        # Check if workdir is clean
        if self.repo.is_workdir_clean():
            return True

        # Workdir has uncommitted changes - warn user
        changes = self.repo.get_workdir_changes()
        change_count = len(changes)

        reply = QMessageBox.warning(
            self,
            "Uncommitted Working Directory Changes",
            f"The working directory has {change_count} uncommitted change(s).\n\n"
            f"You're working on '{self.branch_name}' which is currently checked out. "
            f"AI changes will update the working directory, which will OVERWRITE these uncommitted changes.\n\n"
            f"Options:\n"
            f"• Cancel and commit/stash your changes first\n"
            f"• Discard changes and proceed",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Discard,
            QMessageBox.StandardButton.Cancel,
        )

        return reply == QMessageBox.StandardButton.Discard

    def _send_message(self) -> None:
        """Send user message to AI"""
        text = self.input_field.toPlainText().strip()
        if not text or self.is_processing:
            return

        # Check for unsaved changes if callback is set
        if self.unsaved_changes_check is not None and not self.unsaved_changes_check():
            return  # User cancelled or needs to save first

        # Check working directory state if on checked-out branch
        if not self._check_workdir_state():
            return  # User cancelled or workdir has uncommitted changes

        # Normal message flow - add to both UI messages and prompt manager
        self.add_message("user", text)
        self.session_manager.append_user_message(text)
        self.input_field.clear()

        # Disable input while processing, show cancel button
        self.is_processing = True
        self._cancel_requested = False
        self.input_field.setEnabled(False)
        self.send_button.setEnabled(False)
        self.send_button.hide()
        self.cancel_button.setEnabled(True)
        self.cancel_button.show()

        # Emit signal that AI turn is starting
        self.ai_turn_started.emit()

        # Send to LLM
        self._process_llm_request()

    def _build_prompt_messages(self) -> list[dict[str, Any]]:
        """
        Build the complete prompt using PromptManager.

        The PromptManager maintains the prompt as an append-only stream,
        optimized for cache reuse. File contents are ordered so that
        recently-modified files are at the end.
        """
        # Sync prompt manager with current state (summaries, file contents)
        self.session_manager.sync_prompt_manager()

        # Get optimized messages from prompt manager
        return self.session_manager.get_prompt_messages()

    def _process_llm_request(self) -> None:
        """Process LLM request with streaming support"""
        api_key = self.session_manager.settings.get_api_key()
        # Initialize LLM client
        model = self.session_manager.settings.get("llm.model", "anthropic/claude-3.5-sonnet")
        client = LLMClient(api_key, model)

        # Build complete prompt with fresh context
        messages_with_context = self._build_prompt_messages()

        # Discover available tools
        tools = self.session_manager.tool_manager.discover_tools()

        # Start streaming in a separate thread
        self.streaming_content = ""
        self._streaming_tool_calls = []  # Reset streaming tool calls
        self._start_streaming_message()

        self.stream_thread = QThread()
        self.stream_worker = StreamWorker(client, messages_with_context, tools or None)
        self.stream_worker.moveToThread(self.stream_thread)

        # Connect signals
        self.stream_worker.chunk_received.connect(self._on_stream_chunk)
        self.stream_worker.tool_call_delta.connect(self._on_tool_call_delta)
        self.stream_worker.finished.connect(self._on_stream_finished)
        self.stream_worker.error.connect(self._on_stream_error)
        self.stream_thread.started.connect(self.stream_worker.run)

        # Start the thread
        self.stream_thread.start()

    def _start_streaming_message(self) -> None:
        """Add a placeholder message for streaming content"""
        self.messages.append({"role": "assistant", "content": ""})
        self._is_streaming = True
        self._update_chat_display()

    def _on_stream_chunk(self, chunk: str) -> None:
        """Handle a streaming chunk"""
        self.streaming_content += chunk
        # Update the last message with accumulated content (for final state)
        if self.messages and self.messages[-1]["role"] == "assistant":
            self.messages[-1]["content"] = self.streaming_content
        # Append raw chunk to streaming element - no markdown re-render
        self._append_streaming_chunk(chunk)

    def _on_tool_call_delta(self, index: int, tool_call: dict[str, Any]) -> None:
        """Handle a streaming tool call update"""
        # Ensure we have enough slots
        while len(self._streaming_tool_calls) <= index:
            self._streaming_tool_calls.append({})

        # Update the tool call at this index
        self._streaming_tool_calls[index] = tool_call

        # Update the streaming display to show tool call progress
        self._update_streaming_tool_calls()

    def _update_streaming_tool_calls(self) -> None:
        """Update the streaming message to show tool call progress"""
        if not self._streaming_tool_calls:
            return

        # Build HTML for streaming tool calls
        tool_html_parts = []
        for tc in self._streaming_tool_calls:
            func = tc.get("function", {})
            name = func.get("name", "")
            args = func.get("arguments", "")

            # Show tool name with spinning indicator
            if name:
                # Check for special rendering (search_replace gets a diff view)
                special_html = render_streaming_tool_html(tc)
                if special_html:
                    tool_html_parts.append(special_html)
                else:
                    # Default rendering for other tools
                    tool_html_parts.append('<div class="streaming-tool-call">')
                    tool_html_parts.append(f'<span class="tool-name">🔧 {name}</span>')

                    # Try to pretty-print arguments if they're valid JSON so far
                    if args:
                        # Show arguments as they stream (may be partial JSON)
                        # Escape for display
                        escaped_args = (
                            args.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                        )
                        tool_html_parts.append(
                            f'<pre class="tool-args">{escaped_args}<span class="cursor">▋</span></pre>'
                        )

                    tool_html_parts.append("</div>")

        tool_html = "".join(tool_html_parts)

        # Escape for JavaScript
        escaped_html = (
            tool_html.replace("\\", "\\\\")
            .replace("`", "\\`")
            .replace("$", "\\$")
            .replace("\n", "\\n")
        )

        # Update the streaming message to show tool calls
        js_code = f"""
        (function() {{
            var streamingMsg = document.getElementById('streaming-message');
            if (streamingMsg) {{
                // Check if user is at bottom before modifying content
                var scrollThreshold = 50;
                var wasAtBottom = (window.innerHeight + window.scrollY) >= (document.body.scrollHeight - scrollThreshold);

                // Find or create tool calls container
                var toolsContainer = streamingMsg.querySelector('.streaming-tools');
                if (!toolsContainer) {{
                    toolsContainer = document.createElement('div');
                    toolsContainer.className = 'streaming-tools';
                    streamingMsg.appendChild(toolsContainer);
                }}
                toolsContainer.innerHTML = `{escaped_html}`;

                // Only scroll if user was already at bottom
                if (wasAtBottom) {{
                    window.scrollTo(0, document.body.scrollHeight);
                }}
            }}
        }})();
        """
        self.chat_view.page().runJavaScript(js_code)

    def _on_stream_finished(self, result: dict[str, Any]) -> None:
        """Handle stream completion"""
        # Clean up thread
        if self.stream_thread:
            self.stream_thread.quit()
            self.stream_thread.wait()
            self.stream_thread = None
            self.stream_worker = None

        # Mark streaming as finished
        self._is_streaming = False
        self._streaming_tool_calls = []  # Clear streaming tool calls

        # Finalize streaming content with proper markdown rendering
        self._finalize_streaming_content()

        # Handle the streaming message - update with final content and tool_calls if present
        if self.messages and self.messages[-1]["role"] == "assistant":
            # Update content if present in result
            if result.get("content"):
                self.messages[-1]["content"] = result["content"]

            # Add tool_calls if present
            if result.get("tool_calls"):
                self.messages[-1]["tool_calls"] = result["tool_calls"]

        # Handle tool calls if present
        if result.get("tool_calls"):
            # Include content with tool calls so AI sees its own reasoning
            self.session_manager.append_tool_call(result["tool_calls"], result.get("content") or "")

            # Execute tools - don't commit yet, AI will respond again
            self._execute_tool_calls(result["tool_calls"])
            return

        # This is a final text response with no tool calls
        # Add to prompt manager
        if result.get("content"):
            self.session_manager.append_assistant_message(result["content"])

        # Emit updated context stats (conversation tokens changed)
        self._emit_context_stats()

        # Commit now
        commit_oid = self.session_manager.commit_ai_turn(self.messages)
        self._add_system_message(f"✅ Changes committed: {commit_oid[:8]}")

        # Emit signal that AI turn is finished
        self.ai_turn_finished.emit(commit_oid)

        self._reset_input()

    def _on_stream_error(self, error_msg: str) -> None:
        """Handle streaming error by feeding it back into the conversation"""
        # Clean up thread
        if self.stream_thread:
            self.stream_thread.quit()
            self.stream_thread.wait()
            self.stream_thread = None
            self.stream_worker = None

        # Mark streaming as finished
        self._is_streaming = False

        # Remove the empty streaming message placeholder if present
        if (
            self.messages
            and self.messages[-1].get("role") == "assistant"
            and not self.messages[-1].get("content")
        ):
            self.messages.pop()

        # Build error message for both display and LLM
        error_content = f"**Error from LLM provider:**\n\n```\n{error_msg}\n```\n\nPlease acknowledge this error and adjust your approach if needed."

        # Add to UI display
        self._add_system_message(f"❌ {error_msg}")

        # Add to conversation so AI sees it and can react
        # Using "user" role since that's what the AI will respond to
        self.add_message("user", error_content)
        self.session_manager.append_user_message(error_content)

        # Continue the conversation - AI will see the error and can respond
        self._process_llm_request()

    def _execute_tool_calls(self, tool_calls: list[dict[str, Any]]) -> None:
        """Execute tool calls in background thread and continue conversation"""
        # Store tools for later use in _on_tools_all_finished
        self._pending_tools = self.session_manager.tool_manager.discover_tools()

        # Create and start tool execution worker
        self.tool_thread = QThread()
        self.tool_worker = ToolExecutionWorker(
            tool_calls,
            self.session_manager.tool_manager,
            self.session_manager,
        )
        self.tool_worker.moveToThread(self.tool_thread)

        # Connect signals
        self.tool_worker.tool_started.connect(self._on_tool_started)
        self.tool_worker.tool_finished.connect(self._on_tool_finished)
        self.tool_worker.all_finished.connect(self._on_tools_all_finished)
        self.tool_worker.error.connect(self._on_tool_error)
        self.tool_thread.started.connect(self.tool_worker.run)

        # Start the thread
        self.tool_thread.start()

    def _on_tool_started(self, tool_name: str, tool_args: dict[str, Any]) -> None:
        """Handle tool execution starting"""
        # Could show a "running..." indicator here if desired
        pass

    def _on_tool_finished(
        self, tool_call_id: str, tool_name: str, tool_args: dict[str, Any], result: dict[str, Any]
    ) -> None:
        """Handle individual tool execution completion (called from main thread via signal)"""
        # This is called on the main thread, so UI updates are safe
        # tool_call_id is passed directly from the worker, no need to search

        # Add tool result to messages
        result_json = json.dumps(result)
        tool_message = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result_json,
            "_skip_display": True,
        }
        self.messages.append(tool_message)
        self.session_manager.append_tool_result(tool_call_id, result_json)

        # Track modified files - we'll update the prompt manager AFTER all tool results
        # are recorded. This avoids inserting FILE_CONTENT blocks between TOOL_RESULT
        # blocks, which would violate Anthropic's API requirement that tool_use must be
        # immediately followed by tool_result.
        if result.get("success") and tool_name in ("write_file", "search_replace", "delete_file"):
            filepath = tool_args.get("filepath")
            if filepath:
                if not hasattr(self, "_pending_file_updates"):
                    self._pending_file_updates = []
                self._pending_file_updates.append((filepath, tool_call_id))

        # Handle compact tool specially - it modifies the prompt manager
        if result.get("compact") and result.get("success"):
            compact_ids = result.get("tool_call_ids", [])
            summary = result.get("summary", "")
            compacted, missing_ids = self.session_manager.compact_tool_results(compact_ids, summary)
            print(f"📦 Compacted {compacted} tool result(s)")
            # Update the result to include any missing IDs as an error
            if missing_ids:
                result["error"] = f"IDs not found: {missing_ids}"
                result["success"] = False  # Partial failure

        # If tool modified context, emit signal to update UI
        if result.get("action") == "update_context":
            self.context_changed.emit(self.session_manager.active_files.copy())
            self._emit_context_stats()

        # Display tool result
        # Built-in tools with native rendering don't need system messages on success -
        # they're rendered inline in the assistant message via _render_tool_calls_html
        builtin_tools_with_native_rendering = {
            "search_replace",
            "write_file",
            "delete_file",
            "update_context",
            "grep_open",
            "get_lines",
        }

        if tool_name in builtin_tools_with_native_rendering and result.get("success"):
            # Success case - tool call is already rendered inline, no system message needed
            pass
        elif tool_name == "search_replace" and not result.get("success"):
            # search_replace failure - show error and add file to context
            tool_display_parts = [
                f"🔧 **Tool call:** `{tool_name}`",
                f"```json\n{json.dumps(tool_args, indent=2)}\n```",
                "**Result:**",
                f"```json\n{json.dumps(result, indent=2)}\n```",
            ]
            filepath = tool_args.get("filepath")
            if filepath and filepath not in self.session_manager.active_files:
                self.session_manager.add_active_file(filepath)
                tool_display_parts.append(
                    f"\n📂 Added `{filepath}` to context so you can see its actual content"
                )
            self._add_system_message("\n".join(tool_display_parts))
        elif tool_name in builtin_tools_with_native_rendering and not result.get("success"):
            # Other built-in tool failure - show error
            error_msg = result.get("error", "Unknown error")
            self._add_system_message(f"❌ **{tool_name}** failed: {error_msg}")
        else:
            # Unknown tool - show full JSON
            tool_display_parts = [
                f"🔧 **Tool call:** `{tool_name}`",
                f"```json\n{json.dumps(tool_args, indent=2)}\n```",
                "**Result:**",
                f"```json\n{json.dumps(result, indent=2)}\n```",
            ]
            self._add_system_message("\n".join(tool_display_parts))

    def _on_tools_all_finished(self, results: list[dict[str, Any]]) -> None:
        """Handle all tools completed - continue conversation"""
        # Clean up thread
        if self.tool_thread:
            self.tool_thread.quit()
            self.tool_thread.wait()
            self.tool_thread = None
            self.tool_worker = None

        # NOW it's safe to update file contents in the prompt manager.
        # All tool_result blocks have been recorded, so adding FILE_CONTENT
        # blocks won't break the tool_use -> tool_result adjacency requirement.
        if hasattr(self, "_pending_file_updates"):
            for filepath, tool_call_id in self._pending_file_updates:
                self.session_manager.file_was_modified(filepath, tool_call_id)
            self._pending_file_updates = []

        # Continue conversation with tool results
        tools = getattr(self, "_pending_tools", [])
        self._continue_after_tools(tools)

    def _on_tool_error(self, error_msg: str) -> None:
        """Handle tool execution error"""
        # Clean up thread
        if self.tool_thread:
            self.tool_thread.quit()
            self.tool_thread.wait()
            self.tool_thread = None
            self.tool_worker = None

        self._add_system_message(f"❌ Tool execution error: {error_msg}")

        # Emit signal that AI turn is finished (with empty string indicating no commit)
        self.ai_turn_finished.emit("")
        self._reset_input()

    def _continue_after_tools(self, tools: list[dict[str, Any]]) -> None:
        """Continue LLM conversation after tool execution (in background thread) with streaming"""
        model = self.session_manager.settings.get("llm.model", "anthropic/claude-3.5-sonnet")
        api_key = self.session_manager.settings.get_api_key()
        client = LLMClient(api_key, model)

        # Rebuild prompt with fresh context (in case update_context changed active files)
        messages_with_context = self._build_prompt_messages()

        # Start streaming (same as initial request)
        self.streaming_content = ""
        self._streaming_tool_calls = []  # Reset streaming tool calls
        self._start_streaming_message()

        self.stream_thread = QThread()
        self.stream_worker = StreamWorker(client, messages_with_context, tools or None)
        self.stream_worker.moveToThread(self.stream_thread)

        # Connect signals - use the same handlers as initial streaming
        self.stream_worker.chunk_received.connect(self._on_stream_chunk)
        self.stream_worker.tool_call_delta.connect(self._on_tool_call_delta)
        self.stream_worker.finished.connect(self._on_stream_finished)
        self.stream_worker.error.connect(self._on_stream_error)
        self.stream_thread.started.connect(self.stream_worker.run)

        # Start the thread
        self.stream_thread.start()

    def _cancel_ai_turn(self) -> None:
        """Cancel the current AI turn - abort streaming/tool execution and discard changes"""
        if not self.is_processing:
            return

        self._cancel_requested = True

        # Clean up streaming thread if active
        if self.stream_thread and self.stream_thread.isRunning():
            self.stream_thread.quit()
            self.stream_thread.wait(1000)  # Wait up to 1 second
            if self.stream_thread.isRunning():
                self.stream_thread.terminate()
            self.stream_thread = None
            self.stream_worker = None

        # Clean up tool thread if active
        if self.tool_thread and self.tool_thread.isRunning():
            self.tool_thread.quit()
            self.tool_thread.wait(1000)
            if self.tool_thread.isRunning():
                self.tool_thread.terminate()
            self.tool_thread = None
            self.tool_worker = None

        # Clear streaming state
        self._is_streaming = False
        self._streaming_tool_calls = []

        # Discard pending VFS changes
        self.session_manager.vfs.clear_pending_changes()

        # Remove the incomplete assistant message if present
        if self.messages and self.messages[-1].get("role") == "assistant":
            self.messages.pop()

        # Add cancellation notice
        self._add_system_message("🛑 AI turn cancelled by user")

        # Emit signal that AI turn is finished (no commit)
        self.ai_turn_finished.emit("")

        self._reset_input()
        self._update_chat_display()

    def _reset_input(self) -> None:
        """Re-enable input after processing (if no pending approvals)"""
        self.is_processing = False

        # Hide cancel button, show send button
        self.cancel_button.setEnabled(False)
        self.cancel_button.hide()
        self.send_button.show()

        # Check for new unapproved tools after AI response
        self._check_for_unapproved_tools()

        # Only enable input if no pending approvals
        if not self.pending_approvals:
            self.input_field.setEnabled(True)
            self.send_button.setEnabled(True)

    def add_message(self, role: str, content: str) -> None:
        """Add a message to the chat (becomes part of conversation history)"""
        self.messages.append({"role": role, "content": content})
        self._update_chat_display(scroll_to_bottom=True)

    def _add_system_message(self, content: str) -> None:
        """Add a system/UI feedback message (display only, not sent to LLM)"""
        # Use a special marker to distinguish UI messages from real system messages
        self.messages.append({"role": "system", "content": content, "_ui_only": True})
        self._update_chat_display(scroll_to_bottom=True)

    def _get_conversation_messages(self) -> list[dict[str, Any]]:
        """Get messages that are part of the actual conversation (excludes UI-only messages)"""
        return [msg for msg in self.messages if not msg.get("_ui_only", False)]

    def _append_streaming_chunk(self, chunk: str) -> None:
        """Append a raw text chunk to the streaming message (no markdown re-render)"""
        # Escape the chunk for JavaScript string
        escaped_chunk = (
            chunk.replace("\\", "\\\\")
            .replace("`", "\\`")
            .replace("$", "\\$")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
        )

        # Append raw text to streaming element - browser handles display
        # Only auto-scroll if user was already at bottom (within 50px threshold)
        js_code = f"""
        (function() {{
            var streamingMsg = document.getElementById('streaming-message');
            if (streamingMsg) {{
                // Check if user is at bottom before modifying content
                var scrollThreshold = 50;
                var wasAtBottom = (window.innerHeight + window.scrollY) >= (document.body.scrollHeight - scrollThreshold);

                var content = streamingMsg.querySelector('.content');
                if (content) {{
                    // Append to raw text accumulator
                    if (!content.dataset.rawText) content.dataset.rawText = '';
                    content.dataset.rawText += `{escaped_chunk}`;
                    // Display as preformatted text during streaming
                    content.innerText = content.dataset.rawText;
                }}

                // Only scroll if user was already at bottom
                if (wasAtBottom) {{
                    window.scrollTo(0, document.body.scrollHeight);
                }}
            }}
        }})();
        """
        self.chat_view.page().runJavaScript(js_code)

    def _finalize_streaming_content(self) -> None:
        """Convert accumulated streaming text to markdown (called once at end)"""
        if not self.streaming_content:
            return

        # Convert markdown to HTML
        content_html = markdown.markdown(
            self.streaming_content, extensions=["fenced_code", "codehilite"]
        )

        # Escape for JavaScript string
        escaped_html = content_html.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")

        # Replace streaming content with rendered markdown
        js_code = f"""
        (function() {{
            var streamingMsg = document.getElementById('streaming-message');
            if (streamingMsg) {{
                var content = streamingMsg.querySelector('.content');
                if (content) {{
                    content.innerHTML = `{escaped_html}`;
                    delete content.dataset.rawText;
                }}
            }}
        }})();
        """
        self.chat_view.page().runJavaScript(js_code)

    def get_session_data(self) -> dict[str, Any]:
        """Get session data for persistence (used by SessionManager for git commits)"""
        return {
            "messages": self._get_conversation_messages(),
            "active_files": list(self.session_manager.active_files),
        }

    def _get_chat_styles(self) -> str:
        """Return CSS styles for the chat display"""
        return (
            get_diff_styles()
            + """
            body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                padding: 20px;
                background: #ffffff;
                margin: 0;
            }
            #messages-container {
                /* Container for all messages - content is injected here */
            }
            .message {
                margin-bottom: 20px;
                padding: 15px;
                border-radius: 8px;
            }
            .user {
                background: #e3f2fd;
                margin-left: 20%;
            }
            .assistant {
                background: #f5f5f5;
                margin-right: 20%;
            }
            .system {
                background: #fff3cd;
                border: 2px solid #ffc107;
                margin: 0 10%;
            }
            .role {
                font-weight: bold;
                margin-bottom: 8px;
                color: #666;
            }
            code {
                background: #f0f0f0;
                padding: 2px 6px;
                border-radius: 3px;
                font-family: "Courier New", monospace;
            }
            pre {
                background: #f0f0f0;
                padding: 12px;
                border-radius: 6px;
                overflow-x: auto;
            }
            /* Streaming content shows as preformatted until finalized */
            #streaming-message .content {
                white-space: pre-wrap;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            }
            /* Streaming tool calls */
            .streaming-tools {
                margin-top: 12px;
                border-top: 1px solid #ddd;
                padding-top: 12px;
            }
            .streaming-tool-call {
                margin-bottom: 10px;
            }
            .streaming-tool-call .tool-name {
                font-weight: bold;
                color: #1976d2;
                font-size: 14px;
            }
            .streaming-tool-call .tool-args {
                background: #f5f5f5;
                border: 1px solid #e0e0e0;
                border-radius: 4px;
                padding: 8px 12px;
                margin-top: 6px;
                font-family: "Courier New", monospace;
                font-size: 12px;
                white-space: pre-wrap;
                word-break: break-all;
                max-height: 200px;
                overflow-y: auto;
            }
            .streaming-tool-call .cursor {
                animation: blink 1s step-end infinite;
                color: #1976d2;
            }
            @keyframes blink {
                0%, 100% { opacity: 1; }
                50% { opacity: 0; }
            }
            .approval-buttons {
                margin-top: 10px;
                display: flex;
                gap: 10px;
            }
            .approval-buttons button {
                padding: 8px 16px;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                font-size: 14px;
                font-weight: bold;
            }
            .approval-buttons button:first-child {
                background: #4caf50;
                color: white;
            }
            .approval-buttons button:first-child:hover {
                background: #45a049;
            }
            .approval-buttons button:last-child {
                background: #f44336;
                color: white;
            }
            .approval-buttons button:last-child:hover {
                background: #da190b;
            }
            .approval-buttons button:disabled {
                opacity: 0.5;
                cursor: not-allowed;
            }
        """
        )

    def _get_chat_scripts(self) -> str:
        """Return JavaScript for the chat display"""
        return """
            var bridge;

            // Initialize web channel
            new QWebChannel(qt.webChannelTransport, function(channel) {
                bridge = channel.objects.bridge;
            });

            function approveTool(toolName, buttonElement) {
                // Disable the button immediately
                buttonElement.disabled = true;

                if (bridge) {
                    bridge.handleToolApproval(toolName, true);
                    // Disable both buttons for this tool
                    disableToolButtons(toolName);
                }
            }

            function rejectTool(toolName, buttonElement) {
                // Disable the button immediately
                buttonElement.disabled = true;

                if (bridge) {
                    bridge.handleToolApproval(toolName, false);
                    // Disable both buttons for this tool
                    disableToolButtons(toolName);
                }
            }

            function disableToolButtons(toolName) {
                // Find the button that was clicked and disable both buttons in its container
                var buttons = document.querySelectorAll('.approval-buttons button');
                buttons.forEach(function(btn) {
                    var onclick = btn.getAttribute('onclick');
                    if (onclick && onclick.includes(toolName)) {
                        // Found a button for this tool - disable its parent container's buttons
                        var container = btn.closest('.approval-buttons');
                        if (container) {
                            var containerButtons = container.querySelectorAll('button');
                            containerButtons.forEach(function(b) {
                                b.disabled = true;
                            });
                        }
                    }
                });
            }

            // Update messages container content (called from Python)
            function updateMessages(html, scrollToBottom) {
                var container = document.getElementById('messages-container');
                if (container) {
                    container.innerHTML = html;
                    if (scrollToBottom) {
                        window.scrollTo(0, document.body.scrollHeight);
                    }
                }
            }
        """

    def _on_js_console_message(
        self,
        level: QWebEnginePage.JavaScriptConsoleMessageLevel,
        message: str,
        line: int,
        source: str,
    ) -> None:
        """Log JavaScript console messages to stdout for debugging"""
        level_str = ["DEBUG", "INFO", "WARNING", "ERROR"][min(level.value, 3)]
        print(f"[JS {level_str}] {message} (line {line}, {source})")

    def _on_shell_loaded(self, ok: bool) -> None:
        """Called when the HTML shell has finished loading"""
        if ok:
            self._shell_ready = True
            # Now it's safe to inject content
            self._update_chat_display()
        else:
            print("ERROR: Failed to load chat shell HTML")

    def _init_chat_shell(self) -> None:
        """Initialize the stable HTML shell for the chat display.

        This is called once at startup. All subsequent updates inject content
        into the #messages-container div via JavaScript, preserving scroll position.
        """
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>{self._get_chat_styles()}</style>
            <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
            <script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
            <script>{self._get_chat_scripts()}</script>
        </head>
        <body>
            <div id="messages-container"></div>
        </body>
        </html>
        """
        self.chat_view.setHtml(html)

    def _render_tool_calls_html(
        self, tool_calls: list[dict[str, Any]], tool_results: dict[str, dict[str, Any]]
    ) -> str:
        """Render tool calls from a historical message as HTML.

        Args:
            tool_calls: List of tool call objects from assistant message
            tool_results: Map of tool_call_id -> parsed result dict
        """
        html_parts = []
        for tc in tool_calls:
            func = tc.get("function", {})
            name = func.get("name", "")
            args_str = func.get("arguments", "")
            tool_call_id = tc.get("id", "")

            if not name:
                continue

            # Parse arguments
            try:
                args = json.loads(args_str) if args_str else {}
            except json.JSONDecodeError:
                args = {}

            # Get the result for this tool call (if available)
            result = tool_results.get(tool_call_id)

            # Try native rendering for built-in tools
            native_html = render_completed_tool_html(name, args, result)
            if native_html:
                html_parts.append(native_html)
            else:
                # Default rendering for unknown tools
                escaped_args = (
                    args_str.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                )
                html_parts.append(f"""
                <div class="tool-call-display">
                    <div class="tool-name">🔧 {name}</div>
                    <pre class="tool-args">{escaped_args}</pre>
                </div>
                """)

        return "".join(html_parts)

    def _build_messages_html(self) -> str:
        """Build HTML for all messages (to inject into the container)"""
        html_parts = []

        # Build a lookup of tool_call_id -> parsed result for rendering tool calls with results
        tool_results: dict[str, dict[str, Any]] = {}
        for msg in self.messages:
            if msg.get("role") == "tool" and "tool_call_id" in msg:
                tool_call_id = msg["tool_call_id"]
                content = msg.get("content", "")
                try:
                    tool_results[tool_call_id] = json.loads(content) if content else {}
                except json.JSONDecodeError:
                    tool_results[tool_call_id] = {}

        for i, msg in enumerate(self.messages):
            # Skip messages marked for no display (e.g., raw tool results)
            if msg.get("_skip_display"):
                continue

            role = msg["role"]
            content_md = msg["content"] or ""

            # Check if this is the currently streaming message (last assistant message while streaming)
            is_streaming_msg = (
                self._is_streaming and i == len(self.messages) - 1 and role == "assistant"
            )
            msg_id = 'id="streaming-message"' if is_streaming_msg else ""

            # Check if this message contains approval buttons that should be disabled
            # Look for tool names in the content
            for tool_name in self.handled_approvals:
                if (
                    f"onclick=\"approveTool('{tool_name}'" in content_md
                    or f"onclick=\"rejectTool('{tool_name}'" in content_md
                ):
                    # This message has buttons for a handled tool - mark them disabled
                    # Replace button tags to add disabled attribute
                    content_md = content_md.replace(
                        f"<button onclick=\"approveTool('{tool_name}', this)\">",
                        f"<button onclick=\"approveTool('{tool_name}', this)\" disabled>",
                    )
                    content_md = content_md.replace(
                        f"<button onclick=\"rejectTool('{tool_name}', this)\">",
                        f"<button onclick=\"rejectTool('{tool_name}', this)\" disabled>",
                    )

            # For assistant messages with tool_calls, render them specially
            tool_calls_html = ""
            if role == "assistant" and "tool_calls" in msg:
                tool_calls_html = self._render_tool_calls_html(msg["tool_calls"], tool_results)

            content = markdown.markdown(content_md, extensions=["fenced_code", "codehilite"])
            html_parts.append(f"""
            <div class="message {role}" {msg_id}>
                <div class="role">{role.capitalize()}</div>
                <div class="content">{content}</div>
                {tool_calls_html}
            </div>
            """)

        return "".join(html_parts)

    def _update_chat_display(self, scroll_to_bottom: bool = False) -> None:
        """Update the chat display with all messages.

        This injects content into the stable HTML shell via JavaScript,
        which naturally preserves scroll position.

        Args:
            scroll_to_bottom: If True, scroll to bottom after update.
                            If False, scroll position is preserved automatically.
        """
        # Don't try to update before the shell is ready
        if not self._shell_ready:
            return

        messages_html = self._build_messages_html()

        # Escape for JavaScript string
        escaped_html = messages_html.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")

        scroll_js = "true" if scroll_to_bottom else "false"

        # Inject content via JavaScript - scroll position preserved automatically
        self.chat_view.page().runJavaScript(f"updateMessages(`{escaped_html}`, {scroll_js});")
