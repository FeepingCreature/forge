"""
AI chat widget with markdown/LaTeX rendering
"""

import json
import uuid
from typing import TYPE_CHECKING, Any

import markdown
from PySide6.QtCore import QEvent, QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QKeyEvent
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..git_backend.repository import ForgeRepository
from ..llm.client import LLMClient
from ..session.manager import SessionManager

if TYPE_CHECKING:
    from ..config.settings import Settings


class StreamWorker(QObject):
    """Worker for handling streaming LLM responses in a separate thread"""

    chunk_received = Signal(str)  # Emitted for each text chunk
    tool_call_received = Signal(dict)  # Emitted when tool call is complete
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

            # Emit final result
            result = {
                "content": self.current_content if self.current_content else None,
                "tool_calls": self.current_tool_calls if self.current_tool_calls else None,
            }
            self.finished.emit(result)

        except Exception as e:
            self.error.emit(str(e))


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

    # Note: session_updated signal removed - sessions persist via git commits, not filesystem

    def __init__(
        self,
        session_id: str | None = None,
        session_data: dict[str, Any] | None = None,
        settings: "Settings | None" = None,
        repo: ForgeRepository | None = None,
    ) -> None:
        super().__init__()
        self.session_id = session_id or str(uuid.uuid4())
        self.branch_name = f"forge/session/{self.session_id}"
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

        # Web channel bridge for JavaScript communication
        self.bridge = ChatBridge(self)
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)

        # Initialize session manager (repo and settings are required)
        assert repo is not None, "Repository is required for AIChatWidget"
        assert settings is not None, "Settings are required for AIChatWidget"
        self.session_manager = SessionManager(repo, self.session_id, self.branch_name, settings)

        # Load existing session or start fresh
        if session_data:
            self.messages = session_data.get("messages", [])
            self.branch_name = session_data.get("branch_name", self.branch_name)
            if "active_files" in session_data:
                for filepath in session_data["active_files"]:
                    self.session_manager.add_active_file(filepath)

        # Setup UI BEFORE any operations that might call add_message()
        self._setup_ui()

        # Generate repository summaries on session creation (if not already done)
        if not self.session_manager.repo_summaries:
            self.add_message("system", "🔍 Generating repository summaries...")
            self._update_chat_display()
            self.session_manager.generate_repo_summaries()
            self.add_message("system", f"✅ Generated summaries for {len(self.session_manager.repo_summaries)} files")

        self._update_chat_display()
        self._check_for_unapproved_tools()

    def _setup_ui(self) -> None:
        """Setup the chat UI"""
        layout = QVBoxLayout(self)

        # Chat display area (using QWebEngineView for markdown/LaTeX)
        self.chat_view = QWebEngineView()

        # Set up web channel for JavaScript communication
        self.chat_view.page().setWebChannel(self.channel)

        # Pre-initialize with minimal HTML to avoid flash on first load
        self.chat_view.setHtml(
            """
            <!DOCTYPE html>
            <html>
            <head>
                <style>
                    body {
                        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                        padding: 20px;
                        background: #ffffff;
                    }
                </style>
            </head>
            <body></body>
            </html>
            """
        )
        # Update with actual content after initialization
        self._update_chat_display()

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

        input_layout.addWidget(self.input_field)
        input_layout.addWidget(self.send_button)

        layout.addLayout(input_layout)

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

    def _send_message(self) -> None:
        """Send user message to AI"""
        text = self.input_field.toPlainText().strip()
        if not text or self.is_processing:
            return

        # Normal message flow
        self.add_message("user", text)
        self.input_field.clear()

        # Disable input while processing
        self.is_processing = True
        self.input_field.setEnabled(False)
        self.send_button.setEnabled(False)
        self.send_button.setText("Thinking...")

        # Send to LLM
        self._process_llm_request()

    def _process_llm_request(self) -> None:
        """Process LLM request with streaming support"""
        api_key = self.session_manager.settings.get_api_key()
        # Initialize LLM client
        model = self.session_manager.settings.get("llm.model", "anthropic/claude-3.5-sonnet")
        client = LLMClient(api_key, model)

        # Build context with summaries and active files
        context = self.session_manager.build_context()
        context_message = ""

        # Add repository summaries (loop on possibly-empty dict)
        for filepath, summary in context["summaries"].items():
            if not context_message:
                context_message += "# Repository Files\n\n"
            context_message += f"- {filepath}: {summary}\n"
        if context["summaries"]:
            context_message += "\n"

        # Add active files with full content (loop on possibly-empty dict)
        for filepath, content in context["active_files"].items():
            if not any(f in context_message for f in ["# Active Files"]):
                context_message += "# Active Files (Full Content)\n\n"
            context_message += f"## {filepath}\n\n```\n{content}\n```\n\n"

        # Prepend context to messages if we have any
        messages_with_context = self.messages.copy()
        if context_message:
            # Insert context before the last user message
            last_user_idx = len(messages_with_context) - 1
            messages_with_context.insert(
                last_user_idx, {"role": "system", "content": context_message}
            )

        # Discover available tools
        tools = self.session_manager.tool_manager.discover_tools()

        # Start streaming in a separate thread
        self.streaming_content = ""
        self._start_streaming_message()

        self.stream_thread = QThread()
        self.stream_worker = StreamWorker(client, messages_with_context, tools or None)
        self.stream_worker.moveToThread(self.stream_thread)

        # Connect signals
        self.stream_worker.chunk_received.connect(self._on_stream_chunk)
        self.stream_worker.finished.connect(self._on_stream_finished)
        self.stream_worker.error.connect(self._on_stream_error)
        self.stream_thread.started.connect(self.stream_worker.run)

        # Start the thread
        self.stream_thread.start()

    def _start_streaming_message(self) -> None:
        """Add a placeholder message for streaming content"""
        self.messages.append({"role": "assistant", "content": ""})
        self._update_chat_display()

    def _on_stream_chunk(self, chunk: str) -> None:
        """Handle a streaming chunk"""
        self.streaming_content += chunk
        # Update the last message with accumulated content
        if self.messages and self.messages[-1]["role"] == "assistant":
            self.messages[-1]["content"] = self.streaming_content
            self._update_chat_display()

    def _on_stream_finished(self, result: dict[str, Any]) -> None:
        """Handle stream completion"""
        # Clean up thread
        if self.stream_thread:
            self.stream_thread.quit()
            self.stream_thread.wait()
            self.stream_thread = None
            self.stream_worker = None

        # Update final message
        if result.get("content") and self.messages and self.messages[-1]["role"] == "assistant":
            self.messages[-1]["content"] = result["content"]

        # Handle tool calls if present
        if result.get("tool_calls"):
            # Remove the streaming message if it was empty
            if (
                self.messages
                and self.messages[-1]["role"] == "assistant"
                and not self.messages[-1]["content"]
            ):
                self.messages.pop()

            # Add assistant message with tool calls
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": result.get("content")}
            assistant_msg["tool_calls"] = result["tool_calls"]
            self.messages.append(assistant_msg)

            # Execute tools - don't commit yet, AI will respond again
            self._execute_tool_calls(result["tool_calls"])
            return

        # This is a final text response with no tool calls - commit now
        commit_oid = self.session_manager.commit_ai_turn(self.messages)
        self.add_message("assistant", f"✅ Changes committed: {commit_oid[:8]}")

        self._update_chat_display()
        self._reset_input()

    def _on_stream_error(self, error_msg: str) -> None:
        """Handle streaming error"""
        # Clean up thread
        if self.stream_thread:
            self.stream_thread.quit()
            self.stream_thread.wait()
            self.stream_thread = None
            self.stream_worker = None

        self.add_message("assistant", f"Error: {error_msg}")
        self._reset_input()
        return

    def _execute_tool_calls(self, tool_calls: list[dict[str, Any]]) -> None:
        """Execute tool calls and continue conversation"""
        model = self.session_manager.settings.get("llm.model", "anthropic/claude-3.5-sonnet")
        api_key = self.session_manager.settings.get_api_key()
        client = LLMClient(api_key, model)

        tools = self.session_manager.tool_manager.discover_tools()
        tool_manager = self.session_manager.tool_manager

        for tool_call in tool_calls:
            tool_name = tool_call["function"]["name"]
            arguments_str = tool_call["function"]["arguments"]

            # Handle empty arguments (LLM may send empty string for no-arg tools)
            try:
                tool_args = json.loads(arguments_str) if arguments_str else {}
            except json.JSONDecodeError as e:
                # LLM sent invalid JSON - show error and skip this tool
                self.add_message(
                    "assistant",
                    f"❌ Error parsing tool arguments for `{tool_name}`: {e}\n"
                    f"Arguments string: `{arguments_str}`"
                )
                continue

            # Display tool execution
            self.add_message(
                "assistant",
                f"🔧 Calling tool: `{tool_name}`\n```json\n{json.dumps(tool_args, indent=2)}\n```",
            )

            # Execute tool (pass session_manager for context management)
            result = tool_manager.execute_tool(tool_name, tool_args, self.session_manager)

            # Add tool result to messages
            tool_message = {
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": json.dumps(result),
            }
            self.messages.append(tool_message)

            # Display result
            self.add_message(
                "assistant", f"📋 Tool result:\n```json\n{json.dumps(result, indent=2)}\n```"
            )

        # Continue conversation with tool results (non-streaming for now)
        follow_up = client.chat(self.messages, tools=tools or None)
        self._handle_llm_response(follow_up, client, tools)

    def _handle_llm_response(
        self, response: dict[str, Any], client: LLMClient, tools: list[dict[str, Any]] | None
    ) -> None:
        """Handle non-streaming LLM response (used for tool follow-ups)"""
        choice = response["choices"][0]
        message = choice["message"]

        # Check if there are tool calls
        if "tool_calls" in message and message["tool_calls"]:
            # Add assistant message with tool calls
            self.messages.append(message)
            # Continue with tool execution - don't commit yet
            self._execute_tool_calls(message["tool_calls"])
        else:
            # Regular text response - this is the final response, commit now
            content = message.get("content", "")
            if content:
                self.add_message("assistant", content)

            # Commit AI turn - this is the ONLY place we commit - once per AI turn
            commit_oid = self.session_manager.commit_ai_turn(self.messages)
            self.add_message("assistant", f"✅ Changes committed: {commit_oid[:8]}")

            self._reset_input()

    def _reset_input(self) -> None:
        """Re-enable input after processing (if no pending approvals)"""
        self.is_processing = False

        # Check for new unapproved tools after AI response
        self._check_for_unapproved_tools()

        # Only enable input if no pending approvals
        if not self.pending_approvals:
            self.input_field.setEnabled(True)
            self.send_button.setEnabled(True)
        self.send_button.setText("Send")

    def add_message(self, role: str, content: str) -> None:
        """Add a message to the chat"""
        self.messages.append({"role": role, "content": content})
        self._update_chat_display()

    def get_session_data(self) -> dict[str, Any]:
        """Get session data for persistence (used by SessionManager for git commits)"""
        return {
            "session_id": self.session_id,
            "branch_name": self.branch_name,
            "messages": self.messages,
            "active_files": list(self.session_manager.active_files),
        }

    def _update_chat_display(self) -> None:
        """Update the chat display with all messages"""
        html_parts = [
            """
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                    padding: 20px;
                    background: #ffffff;
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
            </style>
            <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
            <script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
            <script>
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
            </script>
        </head>
        <body>
        """
        ]

        for msg in self.messages:
            role = msg["role"]
            content_md = msg["content"]

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

            content = markdown.markdown(content_md, extensions=["fenced_code", "codehilite"])
            html_parts.append(f"""
            <div class="message {role}">
                <div class="role">{role.capitalize()}</div>
                <div class="content">{content}</div>
            </div>
            """)

        html_parts.append("</body></html>")

        self.chat_view.setHtml("".join(html_parts))
