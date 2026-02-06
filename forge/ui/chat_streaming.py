"""
Streaming content helpers for AI chat widget.

These functions generate JavaScript to update the streaming message display
without requiring a full page re-render.
"""

import html
import re

from forge.ui.tool_rendering import render_streaming_edits, render_streaming_tool_html


def escape_for_js(text: str) -> str:
    """Escape text for safe inclusion in JavaScript string literals."""
    return (
        text.replace("\\", "\\\\")
        .replace("`", "\\`")
        .replace("$", "\\$")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def _detect_mermaid_blocks(text: str) -> list[dict]:
    """Detect mermaid fenced code blocks in streaming text.

    Returns a list of segments: either plain text or mermaid blocks.
    Handles incomplete (still-streaming) mermaid blocks by auto-closing them.

    Each segment is a dict with:
        - type: "text" or "mermaid"
        - content: the text content
        - complete: bool (for mermaid blocks, whether the closing ``` was found)
    """
    segments: list[dict] = []
    # Match ```mermaid with optional leading whitespace
    pattern = re.compile(r"^[ \t]*```mermaid\s*$", re.MULTILINE)

    pos = 0
    for match in pattern.finditer(text):
        # Add text before this mermaid block
        if match.start() > pos:
            segments.append({"type": "text", "content": text[pos : match.start()]})

        # Find the closing ```
        block_content_start = match.end()
        # Look for closing ``` on its own line
        close_pattern = re.compile(r"^[ \t]*```\s*$", re.MULTILINE)
        close_match = close_pattern.search(text, block_content_start)

        if close_match:
            # Complete block
            mermaid_content = text[block_content_start : close_match.start()].strip()
            segments.append({"type": "mermaid", "content": mermaid_content, "complete": True})
            pos = close_match.end()
        else:
            # Incomplete block (still streaming) — auto-close it
            mermaid_content = text[block_content_start:].strip()
            if mermaid_content:
                segments.append({"type": "mermaid", "content": mermaid_content, "complete": False})
            pos = len(text)

    # Add remaining text after last mermaid block
    if pos < len(text):
        segments.append({"type": "text", "content": text[pos:]})

    return segments


def _render_streaming_mermaid_html(segments: list[dict]) -> str:
    """Render streaming content that contains mermaid blocks.

    Text segments are HTML-escaped and wrapped in <span> tags.
    Mermaid segments become <pre><code class="language-mermaid"> blocks
    so renderStreamingMermaid() can pick them up.

    Incomplete mermaid blocks get a streaming indicator.
    """
    parts: list[str] = []
    for seg in segments:
        if seg["type"] == "text":
            escaped = html.escape(seg["content"])
            parts.append(f'<span class="streaming-text">{escaped}</span>')
        else:
            # Mermaid block — render as code block for JS to process
            escaped_content = html.escape(seg["content"])
            indicator = "" if seg["complete"] else ' data-streaming="true"'
            parts.append(
                f'<pre{indicator}><code class="language-mermaid">{escaped_content}</code></pre>'
            )
    return "".join(parts)


def build_streaming_chunk_js(streaming_content: str) -> str:
    """Build JavaScript to update the streaming message with new content.

    Args:
        streaming_content: The accumulated streaming content so far

    Returns:
        JavaScript code to execute in the web view
    """
    # Strip [id N] prefix that the model might echo back
    display_content = re.sub(r"^\[id \d+\]\s*", "", streaming_content)

    # Check if we have any <edit> blocks in the accumulated content
    if "<edit" in display_content:
        # Render inline edits as diff views
        rendered_html = render_streaming_edits(display_content)
        escaped_html = escape_for_js(rendered_html)

        # Update the entire content with rendered edits
        return f"""
        (function() {{
            var streamingMsg = document.getElementById('streaming-message');
            if (streamingMsg) {{
                var scrollThreshold = 50;
                var wasAtBottom = (window.innerHeight + window.scrollY) >= (document.body.scrollHeight - scrollThreshold);

                var content = streamingMsg.querySelector('.content');
                if (content) {{
                    content.innerHTML = `{escaped_html}`;
                    content.style.whiteSpace = 'pre-wrap';
                }}

                if (wasAtBottom) {{
                    window.scrollTo(0, document.body.scrollHeight);
                }}
            }}
        }})();
        """

    # Check for mermaid code blocks (```mermaid)
    mermaid_segments = _detect_mermaid_blocks(display_content)
    has_mermaid = any(seg["type"] == "mermaid" for seg in mermaid_segments)

    if has_mermaid:
        rendered_html = _render_streaming_mermaid_html(mermaid_segments)
        escaped_html = escape_for_js(rendered_html)

        return f"""
        (function() {{
            var streamingMsg = document.getElementById('streaming-message');
            if (streamingMsg) {{
                var scrollThreshold = 50;
                var wasAtBottom = (window.innerHeight + window.scrollY) >= (document.body.scrollHeight - scrollThreshold);

                var content = streamingMsg.querySelector('.content');
                if (content) {{
                    content.innerHTML = `{escaped_html}`;
                    content.style.whiteSpace = 'pre-wrap';
                }}

                renderStreamingMermaid();

                if (wasAtBottom) {{
                    window.scrollTo(0, document.body.scrollHeight);
                }}
            }}
        }})();
        """
    else:
        # No edit blocks or mermaid - replace entire content with stripped version
        escaped_content = escape_for_js(display_content)

        return f"""
        (function() {{
            var streamingMsg = document.getElementById('streaming-message');
            if (streamingMsg) {{
                var scrollThreshold = 50;
                var wasAtBottom = (window.innerHeight + window.scrollY) >= (document.body.scrollHeight - scrollThreshold);

                var content = streamingMsg.querySelector('.content');
                if (content) {{
                    content.innerText = `{escaped_content}`;
                }}

                if (wasAtBottom) {{
                    window.scrollTo(0, document.body.scrollHeight);
                }}
            }}
        }})();
        """


def build_streaming_tool_calls_js(tool_calls: list[dict]) -> str:
    """Build JavaScript to update the streaming tool calls display.

    Args:
        tool_calls: List of streaming tool call dicts

    Returns:
        JavaScript code to execute in the web view
    """
    if not tool_calls:
        return ""

    # Build HTML for streaming tool calls
    tool_html_parts = []
    for tc in tool_calls:
        func = tc.get("function", {})
        name = func.get("name", "")
        args = func.get("arguments", "")

        if not name:
            continue

        # Check for special rendering (search_replace gets a diff view)
        special_html = render_streaming_tool_html(tc)
        if special_html:
            tool_html_parts.append(special_html)
        else:
            # Default rendering for other tools
            tool_html_parts.append('<div class="streaming-tool-call">')
            tool_html_parts.append(f'<span class="tool-name">🔧 {name}</span>')

            # Show arguments as they stream (may be partial JSON)
            if args:
                escaped_args = args.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                tool_html_parts.append(
                    f'<pre class="tool-args">{escaped_args}<span class="cursor">▋</span></pre>'
                )

            tool_html_parts.append("</div>")

    tool_html = "".join(tool_html_parts)
    escaped_html = escape_for_js(tool_html)

    return f"""
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


def build_queued_message_js(text: str) -> str:
    """Build JavaScript to show a queued message indicator.

    Args:
        text: The queued message text

    Returns:
        JavaScript code to execute in the web view
    """
    escaped_preview = escape_for_js(text).replace("\\n", "<br>")

    return f"""
    (function() {{
        // Check if we already have a queued indicator
        var existing = document.getElementById('queued-message-indicator');
        if (existing) {{
            existing.remove();
        }}

        // Create the indicator element
        var indicator = document.createElement('div');
        indicator.id = 'queued-message-indicator';
        indicator.className = 'message system';
        indicator.style.cssText = 'background: #e8f5e9; border: 2px solid #4caf50; margin: 0 10%;';
        indicator.innerHTML = '<div class="role">Queued</div><div class="content">📝 Message queued (will be sent after current turn):<br><em>"{escaped_preview}"</em></div>';

        // Append to messages container
        var container = document.getElementById('messages-container');
        if (container) {{
            container.appendChild(indicator);
            window.scrollTo(0, document.body.scrollHeight);
        }}
    }})();
    """
