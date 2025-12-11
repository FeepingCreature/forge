"""
AI chat widget with markdown/LaTeX rendering
"""

import json
import uuid
from pathlib import Path
from typing import Any

import markdown
from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QTextEdit, QVBoxLayout, QWidget

from ..config.settings import Settings
from ..git_backend.repository import ForgeRepository
from ..llm.client import LLMClient
from ..session.manager import SessionManager


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

        # Initialize session manager (repo and settings are required)
        assert repo is not None, "Repository is required for AIChatWidget"
        assert settings is not None, "Settings are required for AIChatWidget"
        self.session_manager = SessionManager(repo, self.session_id, self.branch_name, settings)

        # Streaming worker
        self.stream_thread: QThread | None = None
        self.stream_worker: StreamWorker | None = None

        # Load existing session or start fresh
        if session_data:
            self.messages = session_data.get("messages", [])
            self.branch_name = session_data.get("branch_name", self.branch_name)
            if "active_files" in session_data:
                for filepath in session_data["active_files"]:
                    self.session_manager.add_active_file(filepath)

        self._setup_ui()
        self._update_chat_display()

    def _setup_ui(self) -> None:
        """Setup the chat UI"""
        layout = QVBoxLayout(self)

        # Chat display area (using QWebEngineView for markdown/LaTeX)
        self.chat_view = QWebEngineView()
        self._update_chat_display()

        layout.addWidget(self.chat_view)

        # Input area
        input_layout = QHBoxLayout()

        self.input_field = QTextEdit()
        self.input_field.setMaximumHeight(100)
        self.input_field.setPlaceholderText("Type your message...")

        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self._send_message)
        self.send_button.setMaximumWidth(80)

        input_layout.addWidget(self.input_field)
        input_layout.addWidget(self.send_button)

        layout.addLayout(input_layout)

    def _send_message(self) -> None:
        """Send user message to AI"""
        text = self.input_field.toPlainText().strip()
        if not text or self.is_processing:
            return

        # Add user message  
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
        if not api_key:
            self.add_message(
                "assistant",
                "Error: No API key configured. Please set OPENROUTER_API_KEY or configure in Settings.",
            )
            self._reset_input()
            return

        try:
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
                messages_with_context.insert(last_user_idx, {
                    "role": "system",
                    "content": context_message
                })

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

        except Exception as e:
            self.add_message("assistant", f"Error: {str(e)}")
            self._reset_input()

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
        try:
            commit_oid = self.session_manager.commit_ai_turn(self.messages)
            self.add_message("assistant", f"✅ Changes committed: {commit_oid[:8]}")
        except Exception as e:
            self.add_message("assistant", f"⚠️ Error committing changes: {str(e)}")

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
        try:
            model = self.session_manager.settings.get("llm.model", "anthropic/claude-3.5-sonnet")
            api_key = self.session_manager.settings.get_api_key()
            client = LLMClient(api_key, model)

            tools = self.session_manager.tool_manager.discover_tools()
            tool_manager = self.session_manager.tool_manager

            for tool_call in tool_calls:
                tool_name = tool_call["function"]["name"]
                tool_args = json.loads(tool_call["function"]["arguments"])

                # Display tool execution
                self.add_message(
                    "assistant",
                    f"🔧 Calling tool: `{tool_name}`\n```json\n{json.dumps(tool_args, indent=2)}\n```",
                )

                # Execute tool
                result = tool_manager.execute_tool(tool_name, tool_args)

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

        except Exception as e:
            self.add_message("assistant", f"Error executing tools: {str(e)}")
            self._reset_input()

    def _handle_llm_response(
        self, response: dict[str, Any], client: LLMClient, tools: list[dict[str, Any]] | None
    ) -> None:
        """Handle non-streaming LLM response (used for tool follow-ups)"""
        try:
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
                try:
                    commit_oid = self.session_manager.commit_ai_turn(self.messages)
                    self.add_message("assistant", f"✅ Changes committed: {commit_oid[:8]}")
                except Exception as e:
                    self.add_message("assistant", f"⚠️ Error committing changes: {str(e)}")

                self._reset_input()

        except Exception as e:
            self.add_message("assistant", f"Error processing response: {str(e)}")
            self._reset_input()

    def _reset_input(self) -> None:
        """Re-enable input after processing"""
        self.is_processing = False
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
            </style>
            <script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
        </head>
        <body>
        """
        ]

        for msg in self.messages:
            role = msg["role"]
            content = markdown.markdown(msg["content"], extensions=["fenced_code", "codehilite"])
            html_parts.append(f"""
            <div class="message {role}">
                <div class="role">{role.capitalize()}</div>
                <div class="content">{content}</div>
            </div>
            """)

        html_parts.append("</body></html>")

        self.chat_view.setHtml("".join(html_parts))
