"""
Widget for reviewing and approving tool changes
"""

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class ToolApprovalWidget(QWidget):
    """Widget for reviewing a tool change with diff display"""

    approved = Signal(str)  # Emits tool name when approved
    rejected = Signal(str)  # Emits tool name when rejected

    def __init__(
        self, tool_name: str, tool_code: str, is_new: bool, old_code: str | None = None
    ) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.tool_code = tool_code
        self.is_new = is_new
        self.old_code = old_code

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the approval widget UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # Frame for visual separation
        frame = QFrame()
        frame.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)
        frame.setLineWidth(2)
        frame_layout = QVBoxLayout(frame)

        # Header
        header_layout = QHBoxLayout()
        status_text = "New Tool" if self.is_new else "Modified Tool"
        header_label = QLabel(f"🔧 {status_text}: <b>{self.tool_name}</b>")
        header_label.setStyleSheet("font-size: 14px; color: #d35400;")
        header_layout.addWidget(header_label)
        header_layout.addStretch()
        frame_layout.addLayout(header_layout)

        # Warning message
        warning = QLabel(
            "⚠️ Review this tool carefully before approving. "
            "Once approved, it will run autonomously without further review."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #e74c3c; font-weight: bold; padding: 5px;")
        frame_layout.addWidget(warning)

        # Diff display
        diff_label = QLabel("Tool Code:" if self.is_new else "Changes:")
        diff_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        frame_layout.addWidget(diff_label)

        self.diff_display = QTextEdit()
        self.diff_display.setReadOnly(True)
        self.diff_display.setMaximumHeight(300)
        self.diff_display.setStyleSheet(
            "font-family: monospace; background-color: #f8f8f8; border: 1px solid #ccc;"
        )

        if self.is_new:
            # Show full code for new tools
            self.diff_display.setPlainText(self.tool_code)
        else:
            # Show diff for modified tools
            diff_text = self._generate_diff()
            self.diff_display.setPlainText(diff_text)

        frame_layout.addWidget(self.diff_display)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        reject_btn = QPushButton("✗ Reject")
        reject_btn.setStyleSheet(
            "background-color: #e74c3c; color: white; font-weight: bold; padding: 8px 20px;"
        )
        reject_btn.clicked.connect(self._on_reject)

        accept_btn = QPushButton("✓ Accept")
        accept_btn.setStyleSheet(
            "background-color: #27ae60; color: white; font-weight: bold; padding: 8px 20px;"
        )
        accept_btn.clicked.connect(self._on_approve)

        button_layout.addWidget(reject_btn)
        button_layout.addWidget(accept_btn)

        frame_layout.addLayout(button_layout)

        layout.addWidget(frame)

    def _generate_diff(self) -> str:
        """Generate a simple diff between old and new code"""
        if not self.old_code:
            return self.tool_code

        old_lines = self.old_code.splitlines()
        new_lines = self.tool_code.splitlines()

        diff_lines = []
        diff_lines.append(f"--- {self.tool_name} (old)")
        diff_lines.append(f"+++ {self.tool_name} (new)")
        diff_lines.append("")

        # Simple line-by-line diff
        max_len = max(len(old_lines), len(new_lines))
        for i in range(max_len):
            old_line = old_lines[i] if i < len(old_lines) else None
            new_line = new_lines[i] if i < len(new_lines) else None

            if old_line != new_line:
                if old_line is not None:
                    diff_lines.append(f"- {old_line}")
                if new_line is not None:
                    diff_lines.append(f"+ {new_line}")
            else:
                if old_line is not None:
                    diff_lines.append(f"  {old_line}")

        return "\n".join(diff_lines)

    def _on_approve(self) -> None:
        """Handle approval"""
        self.approved.emit(self.tool_name)
        self.setEnabled(False)
        self.diff_display.setStyleSheet(
            "font-family: monospace; background-color: #d4edda; border: 1px solid #c3e6cb;"
        )

    def _on_reject(self) -> None:
        """Handle rejection"""
        self.rejected.emit(self.tool_name)
        self.setEnabled(False)
        self.diff_display.setStyleSheet(
            "font-family: monospace; background-color: #f8d7da; border: 1px solid #f5c6cb;"
        )
