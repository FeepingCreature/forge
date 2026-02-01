"""
AI chat widget with markdown/LaTeX rendering
"""

import json
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QPushButton,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from forge.ui.chat_helpers import ChatBridge, ExternalLinkPage
from forge.ui.chat_message import (
    ChatMessage,
    build_tool_results_lookup,
    group_messages_into_turns,
)
from forge.ui.chat_streaming import (
    build_queued_message_js,
    build_streaming_chunk_js,
    build_streaming_tool_calls_js,
)
from forge.ui.chat_styles import get_chat_scripts, get_chat_styles
from forge.ui.editor_widget import SearchBar
from forge.ui.tool_rendering import render_markdown

if TYPE_CHECKING:
    from forge.session.live_session import LiveSession, SessionEvent


class AIChatWidget(QWidget):
    """AI chat interface with rich markdown rendering.

    This is a VIEW over a SessionRunner. The runner owns:
    - messages: The conversation history
    - streaming_content: Current streaming text
    - streaming_tool_calls: Current streaming tool calls
    - is_streaming: Whether we're streaming

    This widget:
    - Renders the session state to HTML
    - Handles user input and forwards to runner
    - Manages UI-specific state (approvals, summaries UI)
    """

    # Signals for UI events only
    ai_turn_started = Signal()  # Emitted when AI turn begins (for status bar)
    ai_turn_finished = Signal(str)  # Emitted when AI turn ends (for status bar, git graph refresh)
    fork_requested = Signal(int)  # Emitted when user clicks Fork button (message_index)
    user_typing = Signal()  # Emitted when user types (to clear waiting indicator)

    def __init__(
        self,
        runner: "LiveSession",
        session_data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()

        # Runner is passed in, already initialized
        self.runner = runner

        # Tool approval tracking - initialize BEFORE any method calls
        self.pending_approvals: dict[str, dict[str, Any]] = {}  # tool_name -> tool_info
        self.handled_approvals: set[str] = set()  # Tools that have been approved/rejected

        # Web channel bridge for JavaScript communication
        self.bridge = ChatBridge(self)
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)

        # Setup UI BEFORE attaching to runner - the runner may emit events
        # that need UI elements (send_button, chat_view, etc.)
        self._setup_ui()

        # Restore request log if we have session data (UI concern)
        if session_data:
            self.runner.session_manager.restore_request_log(session_data)

        # Attach to the runner (we're the UI now)
        self._attach_to_runner()

        # Connect to SessionManager summary signals for UI updates
        self.runner.session_manager.summary_progress.connect(self._on_summaries_progress)
        self.runner.session_manager.summary_finished.connect(self._on_summaries_finished)
        self.runner.session_manager.summary_error.connect(self._on_summaries_error)

        # SessionManager auto-starts summary generation in __init__
        # We just need to show UI feedback if it's still running
        if not self.runner.session_manager.are_summaries_ready:
            self._add_system_message("🔍 Generating repository summaries in background...")
            self._summary_message_index = len(self.runner.messages) - 1
        # Note: SessionManager emits context_stats_updated when summaries finish,
        # no need to manually trigger here

        self._update_chat_display()
        self._check_for_unapproved_tools()

    def _attach_to_runner(self) -> None:
        """Attach to the SessionRunner and sync state."""
        # Get snapshot of current state
        messages, streaming_content, streaming_tool_calls, is_streaming, state = (
            self.runner.attach()
        )

        # Connect signals BEFORE draining buffer
        self._connect_runner_signals()

        # Drain any buffered events that occurred during attach
        buffered_events = self.runner.drain_buffer()
        for event in buffered_events:
            self._handle_runner_event(event)

        # Update UI state from snapshot if runner was mid-stream
        if is_streaming:
            self._set_processing_ui(True)

    def _connect_runner_signals(self) -> None:
        """Connect SessionRunner signals to our UI handlers."""
        # Streaming signals
        self.runner.chunk_received.connect(self._on_runner_chunk)
        self.runner.tool_call_delta.connect(self._on_runner_tool_call_delta)

        # Tool execution signals
        self.runner.tool_started.connect(self._on_runner_tool_started)
        self.runner.tool_finished.connect(self._on_runner_tool_finished)

        # State signals
        self.runner.state_changed.connect(self._on_runner_state_changed)
        self.runner.turn_finished.connect(self._on_runner_turn_finished)
        self.runner.error_occurred.connect(self._on_runner_error)

        # Message mutation signals (for UI sync)
        self.runner.message_added.connect(self._on_runner_message_added)
        self.runner.message_updated.connect(self._on_runner_message_updated)
        self.runner.messages_truncated.connect(self._on_runner_messages_truncated)

    def _handle_runner_event(self, event: "SessionEvent") -> None:
        """Handle a buffered event from the runner."""
        from forge.session.live_session import (
            ChunkEvent,
            ErrorEvent,
            MessageAddedEvent,
            MessagesTruncatedEvent,
            MessageUpdatedEvent,
            StateChangedEvent,
            ToolCallDeltaEvent,
            ToolFinishedEvent,
            ToolStartedEvent,
            TurnFinishedEvent,
        )

        if isinstance(event, ChunkEvent):
            self._on_runner_chunk(event.chunk)
        elif isinstance(event, ToolCallDeltaEvent):
            self._on_runner_tool_call_delta(event.index, event.tool_call)
        elif isinstance(event, ToolStartedEvent):
            self._on_runner_tool_started(event.tool_name, event.tool_args)
        elif isinstance(event, ToolFinishedEvent):
            self._on_runner_tool_finished(
                event.tool_call_id, event.tool_name, event.tool_args, event.result
            )
        elif isinstance(event, StateChangedEvent):
            self._on_runner_state_changed(event.state)
        elif isinstance(event, TurnFinishedEvent):
            self._on_runner_turn_finished(event.commit_oid)
        elif isinstance(event, ErrorEvent):
            self._on_runner_error(event.error)
        elif isinstance(event, MessageAddedEvent):
            self._on_runner_message_added(event.message)
        elif isinstance(event, MessageUpdatedEvent):
            self._on_runner_message_updated(event.index, event.message)
        elif isinstance(event, MessagesTruncatedEvent):
            self._on_runner_messages_truncated(event.new_length)

    def detach_from_runner(self) -> None:
        """Detach from the runner (called when tab is closed)."""
        if hasattr(self, "runner"):
            self.runner.detach()

            # Disconnect signals
            try:
                self.runner.chunk_received.disconnect(self._on_runner_chunk)
                self.runner.tool_call_delta.disconnect(self._on_runner_tool_call_delta)
                self.runner.tool_started.disconnect(self._on_runner_tool_started)
                self.runner.tool_finished.disconnect(self._on_runner_tool_finished)
                self.runner.state_changed.disconnect(self._on_runner_state_changed)
                self.runner.turn_finished.disconnect(self._on_runner_turn_finished)
                self.runner.error_occurred.disconnect(self._on_runner_error)
                self.runner.message_added.disconnect(self._on_runner_message_added)
                self.runner.message_updated.disconnect(self._on_runner_message_updated)
                self.runner.messages_truncated.disconnect(self._on_runner_messages_truncated)
            except RuntimeError:
                pass  # Already disconnected

    # === Runner signal handlers ===

    def _on_runner_chunk(self, chunk: str) -> None:
        """Handle streaming chunk from runner."""
        self._append_streaming_chunk(chunk)

    def _on_runner_tool_call_delta(self, index: int, tool_call: dict[str, Any]) -> None:
        """Handle streaming tool call update from runner."""
        self._update_streaming_tool_calls()

    def _on_runner_tool_started(self, tool_name: str, tool_args: dict[str, Any]) -> None:
        """Handle tool execution starting."""
        # Could show a "running..." indicator here if desired
        pass

    def _on_runner_tool_finished(
        self, tool_call_id: str, tool_name: str, tool_args: dict[str, Any], result: dict[str, Any]
    ) -> None:
        """Handle individual tool completion from runner."""
        # Display tool result (system messages for failures, etc.)
        self._display_tool_result(tool_name, tool_args, result)

    def _on_runner_state_changed(self, state: str) -> None:
        """Handle runner state change."""
        from forge.session.live_session import SessionState

        if state == SessionState.RUNNING:
            self.ai_turn_started.emit()
            self._set_processing_ui(True)
        elif state in (SessionState.IDLE, SessionState.ERROR):
            self._set_processing_ui(False)
            self._check_for_unapproved_tools()

    def _on_runner_turn_finished(self, commit_oid: str) -> None:
        """Handle turn completion from runner."""
        self._add_system_message(f"✅ Changes committed: {commit_oid[:8]}")
        self.ai_turn_finished.emit(commit_oid)
        self._notify_turn_complete(commit_oid)
        # Note: Context stats are emitted by SessionManager when files change,
        # no need to manually trigger here

        # Generate summaries for newly created files
        if self.runner._newly_created_files:
            sm = self.runner.session_manager
            for filepath in self.runner._newly_created_files:
                sm.generate_summary_for_file(filepath)
            sm.summaries_ready.emit(sm.repo_summaries)

    def _on_runner_error(self, error_msg: str) -> None:
        """Handle error from runner."""
        self._add_system_message(f"❌ {error_msg}")
        self.ai_turn_finished.emit("")

    def _on_runner_message_added(self, message: dict[str, Any]) -> None:
        """Handle message added to runner."""
        self._update_chat_display(scroll_to_bottom=True)

    def _on_runner_message_updated(self, index: int, message: dict[str, Any]) -> None:
        """Handle message updated in runner."""
        # For streaming, we use direct JS updates, but this catches other cases
        if not self.runner.is_streaming:
            self._update_chat_display()

    def _on_runner_messages_truncated(self, new_length: int) -> None:
        """Handle messages truncated in runner."""
        self._update_chat_display()

    def _set_processing_ui(self, processing: bool) -> None:
        """Update UI for processing state."""
        if processing:
            self.send_button.setEnabled(False)
            self.send_button.hide()
            self.cancel_button.setEnabled(True)
            self.cancel_button.show()
            self.input_field.setPlaceholderText("Type here to queue message for next turn...")
        else:
            self.cancel_button.setEnabled(False)
            self.cancel_button.hide()
            self.send_button.show()
            self.send_button.setEnabled(True)
            self.input_field.setPlaceholderText(
                "Type your message... (Enter to send, Shift+Enter for new line)"
            )

    def _display_tool_result(
        self, tool_name: str, tool_args: dict[str, Any], result: dict[str, Any]
    ) -> None:
        """Display tool result in chat (system messages for failures, etc.)."""
        # Built-in tools with native rendering don't need system messages on success
        builtin_tools_with_native_rendering = {
            "search_replace",
            "delete_file",
            "update_context",
            "grep_open",
            "get_lines",
            "compact",
            "commit",
            "think",
            "run_tests",
        }

        if tool_name in builtin_tools_with_native_rendering and result.get("success"):
            # Success case - tool call is already rendered inline
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
            sm = self.runner.session_manager
            if filepath and filepath not in sm.active_files:
                sm.add_active_file(filepath)
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

        # Search bar (hidden by default, at bottom of chat view)
        self.search_bar = SearchBar()
        self.search_bar.hide()
        self.search_bar.closed.connect(self._close_search)
        self.search_bar.find_next.connect(self._find_next)
        self.search_bar.find_prev.connect(self._find_prev)
        layout.addWidget(self.search_bar)

        # Search shortcut (Ctrl+F)
        self._find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        self._find_shortcut.activated.connect(self._show_search)

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
            self.runner.messages
        ):
            self.runner.messages[self._summary_message_index]["content"] = progress_text
            self._update_chat_display()

    def _on_summaries_finished(self, count: int) -> None:
        """Handle summary generation completion (UI update only - SessionManager handles logic)"""
        # Update the progress message to show completion
        if hasattr(self, "_summary_message_index") and self._summary_message_index < len(
            self.runner.messages
        ):
            self.runner.messages[self._summary_message_index]["content"] = (
                f"✅ Generated summaries for {count} files"
            )
            self._update_chat_display(scroll_to_bottom=True)
        else:
            self._add_system_message(f"✅ Generated summaries for {count} files")

    def _on_summaries_error(self, error_msg: str) -> None:
        """Handle summary generation error (UI update only)"""
        self._add_system_message(f"❌ Error generating summaries: {error_msg}")

    def _check_for_unapproved_tools(self) -> None:
        """Check for unapproved tools and show approval requests in chat"""
        tool_manager = self.runner.session_manager.tool_manager
        unapproved = tool_manager.get_unapproved_tools()

        if unapproved:
            for tool_name, current_code, is_new, old_code in unapproved:
                # Check if this tool has already been handled in this session
                # by checking if it's in the approved_tools.json file
                if tool_manager.is_tool_approved(tool_name):
                    # Already handled, mark it so buttons render disabled
                    self.handled_approvals.add(tool_name)
                    continue

                # Skip if we already have a pending approval for this tool
                # (prevents duplicate approval messages if method is called multiple times)
                if tool_name in self.pending_approvals:
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

        tool_manager = self.runner.session_manager.tool_manager
        if approved:
            tool_manager.approve_tool(tool_name)
            self.add_message("system", f"✅ Approved tool: `{tool_name}`")
        else:
            tool_manager.reject_tool(tool_name)
            self.add_message("system", f"❌ Rejected tool: `{tool_name}`")

        del self.pending_approvals[tool_name]
        self._update_blocking_state()

        # If all approvals done, commit them
        if not self.pending_approvals:
            new_commit_oid = tool_manager.commit_pending_approvals()
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

            # Emit typing signal for any key press (to clear waiting indicator)
            self.user_typing.emit()

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
        from forge.session.live_session import SessionState

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
            if self.runner.state != SessionState.RUNNING:
                self.input_field.setEnabled(True)
                self.send_button.setEnabled(True)
                self.input_field.setPlaceholderText(
                    "Type your message... (Enter to send, Shift+Enter for new line)"
                )

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

        sm = self.runner.session_manager

        # Check if we're working on the checked-out branch
        if not sm.is_on_checked_out_branch():
            # Not working on the checked-out branch, no workdir concerns
            return True

        # Check if workdir is clean
        if sm.is_workdir_clean():
            return True

        # Workdir has uncommitted changes - warn user
        changes = sm.get_workdir_changes()
        change_count = len(changes)

        reply = QMessageBox.warning(
            self,
            "Uncommitted Working Directory Changes",
            f"The working directory has {change_count} uncommitted change(s).\n\n"
            f"You're working on '{self.runner.branch_name}' which is currently checked out. "
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
        from forge.session.live_session import SessionState

        text = self.input_field.toPlainText().strip()
        if not text:
            return

        # If processing, queue the message instead of sending
        if self.runner.state == SessionState.RUNNING:
            # Queue in runner
            self.runner._queued_message = text
            self.input_field.clear()
            # Add queued message indicator via JavaScript to avoid disrupting streaming
            self._append_queued_message_indicator(text)
            return

        # Block if summaries are still being generated
        if not self.runner.session_manager.are_summaries_ready:
            self._add_system_message(
                "⏳ Please wait for repository summaries to finish generating..."
            )
            return

        # Check for unsaved changes if callback is set
        if self.unsaved_changes_check is not None and not self.unsaved_changes_check():
            return  # User cancelled or needs to save first

        # Check working directory state if on checked-out branch
        if not self._check_workdir_state():
            return  # User cancelled or workdir has uncommitted changes

        self.input_field.clear()

        # Send message through runner - it handles everything
        # Skip workdir check since we already did it above with UI
        if not self.runner.send_message(text, _skip_workdir_check=True):
            self._add_system_message("⚠️ Cannot send message - session is busy")
            return

    def _update_streaming_tool_calls(self) -> None:
        """Update the streaming message to show tool call progress"""
        if not self.runner.streaming_tool_calls:
            return

        js_code = build_streaming_tool_calls_js(self.runner.streaming_tool_calls)
        if js_code:
            self.chat_view.page().runJavaScript(js_code)

    def _cancel_ai_turn(self) -> None:
        """Cancel the current AI turn - abort streaming/tool execution and discard changes"""
        from forge.session.live_session import SessionState

        if self.runner.state != SessionState.RUNNING:
            return

        # Delegate to runner - it handles all the cleanup
        self.runner.cancel()

        # Add cancellation notice
        self._add_system_message("🛑 AI turn cancelled by user")

        # Emit signal that AI turn is finished (no commit)
        self.ai_turn_finished.emit("")

        self._update_chat_display()

    def _reset_input(self) -> None:
        """Re-enable input after processing (if no pending approvals)"""

        # Hide cancel button, show send button
        self.cancel_button.setEnabled(False)
        self.cancel_button.hide()
        self.send_button.show()

        # Restore placeholder text
        self.input_field.setPlaceholderText(
            "Type your message... (Enter to send, Shift+Enter for new line)"
        )

        # Check for new unapproved tools after AI response
        self._check_for_unapproved_tools()

        # Only enable input if no pending approvals
        if not self.pending_approvals:
            self.input_field.setEnabled(True)
            self.send_button.setEnabled(True)

            # Check if there's a queued message to send
            if self.runner._queued_message:
                queued = self.runner._queued_message
                self.runner._queued_message = None
                # Auto-send the queued message
                self.input_field.setPlainText(queued)
                self._send_message()

    def add_message(self, role: str, content: str) -> None:
        """Add a message to the chat (becomes part of conversation history)"""
        self.runner.add_message({"role": role, "content": content})
        self._update_chat_display(scroll_to_bottom=True)

    def _add_system_message(self, content: str) -> None:
        """Add a system/UI feedback message (display only, not sent to LLM)"""
        # Use a special marker to distinguish UI messages from real system messages
        self.runner.add_message({"role": "system", "content": content, "_ui_only": True})
        self._update_chat_display(scroll_to_bottom=True)

    def _get_conversation_messages(self) -> list[dict[str, Any]]:
        """Get messages that are part of the actual conversation (excludes UI-only messages)"""
        return [msg for msg in self.runner.messages if not msg.get("_ui_only", False)]

    def _append_queued_message_indicator(self, text: str) -> None:
        """Append a queued message indicator via JavaScript without disrupting streaming"""
        js_code = build_queued_message_js(text)
        self.chat_view.page().runJavaScript(js_code)

    def _append_streaming_chunk(self, chunk: str) -> None:
        """Append a raw text chunk to the streaming message, rendering <edit> blocks as diffs"""
        # streaming_content is already updated in _on_stream_chunk before this is called
        js_code = build_streaming_chunk_js(self.runner.streaming_content)
        self.chat_view.page().runJavaScript(js_code)

    def _finalize_streaming_content(self) -> None:
        """Convert accumulated streaming text to markdown (called once at end)"""
        if not self.runner.streaming_content:
            return

        # Convert markdown to HTML, preserving <edit> blocks as diff views
        from forge.ui.chat_streaming import escape_for_js

        content_html = render_markdown(self.runner.streaming_content)
        escaped_html = escape_for_js(content_html)

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
            <style>{get_chat_styles()}</style>
            <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
            <script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
            <script>{get_chat_scripts()}</script>
        </head>
        <body>
            <div id="messages-container"></div>
        </body>
        </html>
        """
        self.chat_view.setHtml(html)

    def _build_messages_html(self) -> str:
        """Build HTML for all messages, grouped by turn.

        A "turn" is a user message followed by all AI responses until the next user message.
        Each turn gets Revert/Fork buttons at the bottom.
        """
        # Convert raw message dicts to ChatMessage objects
        chat_messages = [ChatMessage.from_dict(msg) for msg in self.runner.messages]

        # Build tool results lookup
        tool_results = build_tool_results_lookup(chat_messages)

        # Group messages into turns
        turns = group_messages_into_turns(chat_messages)

        html_parts = []

        # Render each turn
        for turn_idx, turn_messages in enumerate(turns):
            # Check if this turn is currently streaming (last turn and we're streaming)
            is_current_turn = turn_idx == len(turns) - 1
            turn_is_streaming = is_current_turn and self.runner.is_streaming
            first_msg_idx = turn_messages[0][0]

            # Start turn wrapper with clickable marker
            html_parts.append(f'<div class="turn" data-turn="{turn_idx}">')
            html_parts.append(
                f'<div class="turn-marker" onclick="scrollTurn({turn_idx})" '
                f'title="Click to scroll"></div>'
            )

            # Add turn actions at TOP - but not for streaming turns or first turn
            if not turn_is_streaming and turn_idx > 0:
                html_parts.append(f"""
                <div class="turn-actions turn-actions-top">
                    <button class="turn-btn revert-btn" onclick="revertTurn({first_msg_idx})" title="Revert this turn and all later turns">
                        ⏪ Revert this
                    </button>
                    <button class="turn-btn fork-btn" onclick="forkBeforeTurn({first_msg_idx})" title="Fork from before this turn">
                        🔀 Fork before
                    </button>
                </div>
                """)

            for i, msg in turn_messages:
                # Check if this is the currently streaming message
                is_streaming_msg = (
                    self.runner.is_streaming
                    and i == len(self.runner.messages) - 1
                    and msg.role == "assistant"
                )

                # Render the message
                html_parts.append(
                    msg.render_html(tool_results, self.handled_approvals, is_streaming_msg)
                )

            # Add turn actions at bottom - but not for streaming turns or first turn
            if not turn_is_streaming and turn_idx > 0:
                html_parts.append(f"""
                <div class="turn-actions turn-actions-bottom">
                    <button class="turn-btn revert-btn" onclick="revertToTurn({first_msg_idx})" title="Revert to after this turn (undo later turns)">
                        ⏪ Revert to here
                    </button>
                    <button class="turn-btn fork-btn" onclick="forkAfterTurn({first_msg_idx})" title="Fork from after this turn">
                        🔀 Fork after
                    </button>
                </div>
                """)

            # Close turn wrapper
            html_parts.append("</div>")

        return "".join(html_parts)

    def _handle_rewind(self, message_index: int) -> None:
        """Handle rewind request - truncate conversation to message_index"""
        if self.runner.rewind_to_message(message_index):
            self._add_system_message(f"⏪ Rewound conversation to message {message_index + 1}")
            self._update_chat_display()
        else:
            self._add_system_message("⚠️ Cannot rewind while AI is processing")

    def _handle_rewind_to_commit(self, commit_oid: str) -> None:
        """Handle rewind to a specific commit - reset VFS and reload session"""
        # This is more complex - would need to reset VFS to that commit
        # For now, just show a message
        self._add_system_message(f"⏪ Rewind to commit {commit_oid[:8]} not yet implemented")

    def _handle_rewind_to_message(self, message_index: int) -> None:
        """Alias for _handle_rewind"""
        self._handle_rewind(message_index)

    def _handle_revert_turn(self, first_message_index: int) -> None:
        """Handle reverting a turn and all following turns."""
        if self.runner.revert_turn(first_message_index):
            self._add_system_message("⏪ Reverted to before this turn")
            self._update_chat_display()
        else:
            self._add_system_message("⚠️ Cannot revert while AI is processing")

    def _handle_revert_to_turn(self, first_message_index: int) -> None:
        """Handle reverting TO a turn (keep this turn, undo later turns)."""
        if self.runner.revert_to_turn(first_message_index):
            self._add_system_message("⏪ Reverted to after this turn")
            self._update_chat_display()
        else:
            self._add_system_message("⚠️ Cannot revert while AI is processing")

    def _handle_fork_from_turn(self, first_message_index: int, before: bool = True) -> None:
        """Handle forking from a turn.

        Args:
            first_message_index: Index of first message in the turn
            before: If True, fork from before this turn. If False, fork from after.

        This emits a signal that MainWindow should handle to create a new branch.
        """
        from forge.session.live_session import SessionState

        # Don't allow fork while processing
        if self.runner.state == SessionState.RUNNING:
            self._add_system_message("⚠️ Cannot fork while AI is processing")
            return

        # For "fork after", we need to find the end of this turn
        messages = self.runner.messages
        if not before:
            # Find end of turn (next user message or end)
            end_idx = len(messages)
            for i in range(first_message_index + 1, len(messages)):
                if messages[i].get("role") == "user" and not messages[i].get("_ui_only"):
                    end_idx = i
                    break
            # Emit the end index so fork includes this turn
            self.fork_requested.emit(end_idx)
        else:
            # Fork before - use the first message index
            self.fork_requested.emit(first_message_index)

    def _notify_turn_complete(self, commit_oid: str) -> None:
        """Show system notification when AI turn completes"""
        # Check if window is focused - no notification needed if user is watching
        if self.window() and self.window().isActiveWindow():
            return

        # Check if system tray is available
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        # Create tray icon if needed (lazy initialization)
        if not hasattr(self, "_tray_icon"):
            self._tray_icon = QSystemTrayIcon(self)
            # Use application icon if available
            app = QApplication.instance()
            if app is not None and isinstance(app, QApplication):
                self._tray_icon.setIcon(app.windowIcon())

        # Show notification
        title = "Forge - AI Complete"
        if commit_oid:
            message = f"AI turn finished → {commit_oid[:8]}"
        else:
            message = "AI turn finished (no changes)"

        self._tray_icon.showMessage(
            title,
            message,
            QSystemTrayIcon.MessageIcon.Information,
            3000,  # 3 seconds
        )

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

    # -------------------------------------------------------------------------
    # Search functionality
    # -------------------------------------------------------------------------

    def _show_search(self) -> None:
        """Show the search bar"""
        self.search_bar.show()
        self.search_bar.focus_input()

    def _close_search(self) -> None:
        """Hide the search bar and clear highlights"""
        self.search_bar.hide()
        # Clear any active search highlighting in the web view
        self.chat_view.findText("")
        self.input_field.setFocus()

    def _find_next(self, text: str) -> None:
        """Find next occurrence of text in chat"""
        if text:
            self.chat_view.findText(text)

    def _find_prev(self, text: str) -> None:
        """Find previous occurrence of text in chat"""
        if text:
            self.chat_view.findText(text, QWebEnginePage.FindFlag.FindBackward)
