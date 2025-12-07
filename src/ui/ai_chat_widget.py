"""
AI chat widget with markdown/LaTeX rendering
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QTextEdit, QLineEdit, QPushButton, QHBoxLayout
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtCore import QUrl, Signal
import markdown
import json
import uuid
from pathlib import Path


class AIChatWidget(QWidget):
    """AI chat interface with rich markdown rendering"""
    
    session_updated = Signal()  # Emitted when session state changes
    
    def __init__(self, session_id=None, session_data=None):
        super().__init__()
        self.session_id = session_id or str(uuid.uuid4())
        self.branch_name = f"forge/session/{self.session_id}"
        self.messages = []
        
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
        if not text:
            return
            
        # Add user message
        self.add_message("user", text)
        self.input_field.clear()
        
        # TODO: Send to LLM and get response
        # For now, just echo
        self.add_message("assistant", f"Echo: {text}")
        
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
    def load_session(session_file: Path):
        """Load session from file"""
        with open(session_file, 'r') as f:
            session_data = json.load(f)
        
        return AIChatWidget(
            session_id=session_data.get("session_id"),
            session_data=session_data
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
