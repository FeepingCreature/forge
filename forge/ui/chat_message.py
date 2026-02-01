"""
ChatMessage - Per-message rendering for AI chat.

Each message in the conversation is represented as a ChatMessage object
that knows how to render itself to HTML.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from forge.ui.tool_rendering import (
    render_completed_tool_html,
    render_markdown,
)


@dataclass
class ChatMessage:
    """Represents a single message in the chat conversation.

    This is a view-model class that wraps the raw message dict and
    provides rendering capabilities.
    """

    role: str
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_id: str | None = None  # For tool result messages
    is_ui_only: bool = False  # UI-only messages (not sent to LLM)
    is_mid_turn: bool = False  # Mid-turn user interruptions
    skip_display: bool = False  # Messages to hide from display
    inline_results: list[dict[str, Any]] | None = None  # Results for inline commands

    @classmethod
    def from_dict(cls, msg: dict[str, Any]) -> "ChatMessage":
        """Create a ChatMessage from a raw message dict."""
        return cls(
            role=msg.get("role", ""),
            content=msg.get("content", "") or "",
            tool_calls=msg.get("tool_calls", []),
            tool_call_id=msg.get("tool_call_id"),
            is_ui_only=msg.get("_ui_only", False),
            is_mid_turn=msg.get("_mid_turn", False),
            skip_display=msg.get("_skip_display", False),
            inline_results=msg.get("_inline_results"),
        )

    def should_display(self) -> bool:
        """Check if this message should be displayed in the UI."""
        return not self.skip_display

    def starts_new_turn(self) -> bool:
        """Check if this message starts a new conversation turn.

        A turn starts with a user message (unless it's a mid-turn interruption).
        """
        return self.role == "user" and not self.is_mid_turn

    def render_html(
        self,
        tool_results: dict[str, dict[str, Any]],
        handled_approvals: set[str],
        is_streaming: bool = False,
    ) -> str:
        """Render this message as HTML.

        Args:
            tool_results: Map of tool_call_id -> parsed result dict
            handled_approvals: Set of tool names that have been approved/rejected
            is_streaming: Whether this is the currently streaming message

        Returns:
            HTML string for this message
        """
        msg_id = 'id="streaming-message"' if is_streaming else ""

        # Process content for handled approvals (disable buttons)
        content_md = self.content
        for tool_name in handled_approvals:
            if (
                f"onclick=\"approveTool('{tool_name}'" in content_md
                or f"onclick=\"rejectTool('{tool_name}'" in content_md
            ):
                content_md = content_md.replace(
                    f"<button onclick=\"approveTool('{tool_name}', this)\">",
                    f"<button onclick=\"approveTool('{tool_name}', this)\" disabled>",
                )
                content_md = content_md.replace(
                    f"<button onclick=\"rejectTool('{tool_name}', this)\">",
                    f"<button onclick=\"rejectTool('{tool_name}', this)\" disabled>",
                )

        # Render tool calls if present
        tool_calls_html = ""
        if self.role == "assistant" and self.tool_calls:
            tool_calls_html = self._render_tool_calls(tool_results)

        # Render content with markdown, handling any <edit> blocks as diffs
        content = render_markdown(content_md, inline_results=self.inline_results)

        return f"""
        <div class="message {self.role}" {msg_id}>
            <div class="role">{self.role.capitalize()}</div>
            <div class="content">{content}</div>
            {tool_calls_html}
        </div>
        """

    def _render_tool_calls(self, tool_results: dict[str, dict[str, Any]]) -> str:
        """Render tool calls from this message as HTML.

        Args:
            tool_results: Map of tool_call_id -> parsed result dict
        """
        html_parts = []
        for tc in self.tool_calls:
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


def group_messages_into_turns(
    messages: list[ChatMessage],
) -> list[list[tuple[int, ChatMessage]]]:
    """Group messages into conversation turns.

    A "turn" is a user message followed by all AI responses until the next
    user message. Each turn gets Revert/Fork buttons at the bottom.

    Args:
        messages: List of ChatMessage objects

    Returns:
        List of turns, where each turn is a list of (index, message) tuples
    """
    turns: list[list[tuple[int, ChatMessage]]] = []
    current_turn: list[tuple[int, ChatMessage]] = []

    for i, msg in enumerate(messages):
        if not msg.should_display():
            continue

        # User message starts a new turn (except for mid-turn interruptions)
        if msg.starts_new_turn() and current_turn:
            turns.append(current_turn)
            current_turn = []

        current_turn.append((i, msg))

    # Don't forget the last turn
    if current_turn:
        turns.append(current_turn)

    return turns


def build_tool_results_lookup(messages: list[ChatMessage]) -> dict[str, dict[str, Any]]:
    """Build a lookup of tool_call_id -> parsed result for rendering.

    Args:
        messages: List of ChatMessage objects

    Returns:
        Dict mapping tool_call_id to parsed result dict
    """
    tool_results: dict[str, dict[str, Any]] = {}

    for msg in messages:
        if msg.role == "tool" and msg.tool_call_id:
            content = msg.content
            try:
                tool_results[msg.tool_call_id] = json.loads(content) if content else {}
            except json.JSONDecodeError:
                tool_results[msg.tool_call_id] = {}

    return tool_results
