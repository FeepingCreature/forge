"""
AI chat widget with markdown/LaTeX rendering
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QTextEdit, QLineEdit, QPushButton, QHBoxLayout
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtCore import QUrl, Signal, QThread
from PySide6.QtCore import Signal as pyqtSignal
import markdown
import json
import uuid
from pathlib import Path
from ..llm.client import LLMClient
from ..tools.manager import ToolManager


class AIChatWidget(QWidget):
    """AI chat interface with rich markdown rendering"""
    
    session_updated = Signal()  # Emitted when session state changes
    
    def __init__(self, session_id=None, session_data=None, settings=None, tools_dir="./tools"):
        super().__init__()
        self.session_id = session_id or str(uuid.uuid4())
        self.branch_name = f"forge/session/{self.session_id}"
        self.messages = []
        self.settings = settings
        self.is_processing = False
        
        # Initialize tool manager
        self.tool_manager = ToolManager(tools_dir)
        
        # Load existing session or start fresh
        if session_data:
            self.messages = session_data.get("messages", [])
            self.branch_name = session_data.get("branch_name", self.branch_name)
        
        self._setup_ui()
        self._update_chat_display()
        
    def _setup_ui(self):
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
        
    def _send_message(self):
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
    
    def _process_llm_request(self):
        """Process LLM request with tool support"""
        if not self.settings:
            self.add_message("assistant", "Error: Settings not configured")
            self._reset_input()
            return
        
        api_key = self.settings.get_api_key()
        if not api_key:
            self.add_message("assistant", "Error: No API key configured. Please set OPENROUTER_API_KEY or configure in Settings.")
            self._reset_input()
            return
        
        try:
            # Initialize LLM client
            model = self.settings.get('llm.model', 'anthropic/claude-3.5-sonnet')
            client = LLMClient(api_key, model)
            
            # Discover available tools
            tools = self.tool_manager.discover_tools()
            
            # Send request to LLM
            response = client.chat(self.messages, tools=tools if tools else None)
            
            # Process response
            self._handle_llm_response(response, client, tools)
            
        except Exception as e:
            self.add_message("assistant", f"Error: {str(e)}")
            self._reset_input()
    
    def _handle_llm_response(self, response, client, tools):
        """Handle LLM response, including tool calls"""
        try:
            choice = response['choices'][0]
            message = choice['message']
            
            # Check if there are tool calls
            if 'tool_calls' in message and message['tool_calls']:
                # Add assistant message with tool calls
                self.messages.append(message)
                
                # Execute each tool call
                for tool_call in message['tool_calls']:
                    tool_name = tool_call['function']['name']
                    tool_args = json.loads(tool_call['function']['arguments'])
                    
                    # Display tool execution
                    self.add_message("assistant", f"🔧 Calling tool: `{tool_name}`\n```json\n{json.dumps(tool_args, indent=2)}\n```")
                    
                    # Execute tool
                    result = self.tool_manager.execute_tool(tool_name, tool_args)
                    
                    # Add tool result to messages
                    tool_message = {
                        "role": "tool",
                        "tool_call_id": tool_call['id'],
                        "content": json.dumps(result)
                    }
                    self.messages.append(tool_message)
                    
                    # Display result
                    self.add_message("assistant", f"📋 Tool result:\n```json\n{json.dumps(result, indent=2)}\n```")
                
                # Continue conversation with tool results
                follow_up = client.chat(self.messages, tools=tools if tools else None)
                self._handle_llm_response(follow_up, client, tools)
                
            else:
                # Regular text response
                content = message.get('content', '')
                if content:
                    self.add_message("assistant", content)
                
                self._reset_input()
                
        except Exception as e:
            self.add_message("assistant", f"Error processing response: {str(e)}")
            self._reset_input()
    
    def _reset_input(self):
        """Re-enable input after processing"""
        self.is_processing = False
        self.input_field.setEnabled(True)
        self.send_button.setEnabled(True)
        self.send_button.setText("Send")
        
    def add_message(self, role, content):
        """Add a message to the chat"""
        self.messages.append({"role": role, "content": content})
        self._update_chat_display()
        self.session_updated.emit()
    
    def get_session_data(self):
        """Get session data for persistence"""
        return {
            "session_id": self.session_id,
            "branch_name": self.branch_name,
            "messages": self.messages
        }
    
    def save_session(self, sessions_dir: Path):
        """Save session to file"""
        sessions_dir.mkdir(parents=True, exist_ok=True)
        session_file = sessions_dir / f"{self.session_id}.json"
        
        with open(session_file, 'w') as f:
            json.dump(self.get_session_data(), f, indent=2)
        
        return session_file
    
    @staticmethod
    def load_session(session_file: Path, settings=None, tools_dir="./tools"):
        """Load session from file"""
        with open(session_file, 'r') as f:
            session_data = json.load(f)
        
        return AIChatWidget(
            session_id=session_data.get("session_id"),
            session_data=session_data,
            settings=settings,
            tools_dir=tools_dir
        )
        
    def _update_chat_display(self):
        """Update the chat display with all messages"""
        html_parts = ["""
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
        """]
        
        for msg in self.messages:
            role = msg["role"]
            content = markdown.markdown(msg["content"], extensions=['fenced_code', 'codehilite'])
            html_parts.append(f"""
            <div class="message {role}">
                <div class="role">{role.capitalize()}</div>
                <div class="content">{content}</div>
            </div>
            """)
            
        html_parts.append("</body></html>")
        
        self.chat_view.setHtml("".join(html_parts))
