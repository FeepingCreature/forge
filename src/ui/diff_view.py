"""
Diff view utilities for streaming search/replace visualization.

Provides HTML/CSS/JS for rendering a live diff that updates as
the LLM streams the search and replace parameters.
"""

import difflib
import html
import json


def get_diff_styles() -> str:
    """Return CSS styles for the diff view"""
    return """
        .diff-view {
            font-family: "Courier New", Consolas, monospace;
            font-size: 12px;
            background: #1e1e1e;
            border-radius: 6px;
            overflow: hidden;
            margin: 8px 0;
        }
        .diff-header {
            background: #2d2d2d;
            color: #9cdcfe;
            padding: 8px 12px;
            font-weight: bold;
            border-bottom: 1px solid #404040;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .diff-header .filepath {
            color: #dcdcaa;
        }
        .diff-header .status {
            color: #808080;
            font-weight: normal;
            font-size: 11px;
        }
        .diff-content {
            padding: 0;
            margin: 0;
            overflow-x: auto;
        }
        .diff-line {
            display: flex;
            min-height: 20px;
            line-height: 20px;
        }
        .diff-line-number {
            width: 50px;
            min-width: 50px;
            text-align: right;
            padding-right: 8px;
            color: #606060;
            background: #1a1a1a;
            user-select: none;
            border-right: 1px solid #333;
        }
        .diff-line-content {
            flex: 1;
            padding-left: 12px;
            white-space: pre;
        }
        .diff-line.deletion {
            background: rgba(248, 81, 73, 0.15);
        }
        .diff-line.deletion .diff-line-content {
            color: #f85149;
        }
        .diff-line.deletion .diff-line-number {
            background: rgba(248, 81, 73, 0.2);
            color: #f85149;
        }
        .diff-line.addition {
            background: rgba(63, 185, 80, 0.15);
        }
        .diff-line.addition .diff-line-content {
            color: #3fb950;
        }
        .diff-line.addition .diff-line-number {
            background: rgba(63, 185, 80, 0.2);
            color: #3fb950;
        }
        .diff-line.context {
            background: transparent;
        }
        .diff-line.context .diff-line-content {
            color: #d4d4d4;
        }
        .diff-cursor {
            animation: diff-blink 1s step-end infinite;
            color: #569cd6;
        }
        @keyframes diff-blink {
            0%, 100% { opacity: 1; }
            50% { opacity: 0; }
        }
        .diff-streaming-indicator {
            color: #808080;
            font-style: italic;
            padding: 4px 12px 8px;
            font-size: 11px;
        }
    """


def parse_partial_json(json_str: str) -> dict[str, str]:
    """
    Parse a potentially incomplete JSON object.

    Returns whatever fields we can extract, even from partial JSON.
    This handles the streaming case where we might have:
    - {"filepath": "foo.py", "search": "hello
    - {"filepath": "foo.py", "search": "hello", "replace": "wor
    """
    result: dict[str, str] = {}

    # First try complete JSON parse
    try:
        parsed = json.loads(json_str)
        if isinstance(parsed, dict):
            for key in ("filepath", "search", "replace"):
                if key in parsed and isinstance(parsed[key], str):
                    result[key] = parsed[key]
        return result
    except json.JSONDecodeError:
        pass

    # Incomplete JSON - extract what we can
    # Look for each field pattern: "fieldname": "value or "fieldname":"value
    for field in ("filepath", "search", "replace"):
        # Find the start of this field
        patterns = [f'"{field}": "', f'"{field}":"']
        start_idx = -1
        for pattern in patterns:
            idx = json_str.find(pattern)
            if idx != -1:
                start_idx = idx + len(pattern)
                break

        if start_idx == -1:
            continue

        # Now find the end - look for unescaped quote
        value_chars = []
        i = start_idx
        while i < len(json_str):
            char = json_str[i]
            if char == "\\" and i + 1 < len(json_str):
                # Escaped character - include both
                value_chars.append(json_str[i : i + 2])
                i += 2
            elif char == '"':
                # End of string
                break
            else:
                value_chars.append(char)
                i += 1

        # Unescape the value
        raw_value = "".join(value_chars)
        try:
            # Use json.loads to properly unescape
            result[field] = json.loads(f'"{raw_value}"')
        except json.JSONDecodeError:
            # Fallback - basic unescaping
            result[field] = raw_value.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')

    return result


def render_diff_html(
    filepath: str,
    search: str,
    replace: str,
    is_streaming: bool = True,
    streaming_phase: str = "search",  # "search" or "replace"
) -> str:
    """
    Render a diff view as HTML.

    Args:
        filepath: Path to the file being edited
        search: The search text (content being replaced)
        replace: The replace text (new content)
        is_streaming: Whether we're still streaming
        streaming_phase: Which parameter is currently streaming

    Returns:
        HTML string for the diff view
    """
    lines = []

    # Header
    status = ""
    if is_streaming:
        if streaming_phase == "search":
            status = "receiving search text..."
        else:
            status = "receiving replacement..."

    escaped_filepath = html.escape(filepath) if filepath else "..."
    lines.append('<div class="diff-view">')
    lines.append('<div class="diff-header">')
    lines.append("<span>📝</span>")
    lines.append(f'<span class="filepath">{escaped_filepath}</span>')
    if status:
        lines.append(f'<span class="status">{status}</span>')
    lines.append("</div>")
    lines.append('<div class="diff-content">')

    if not search and not replace:
        # Nothing to show yet
        lines.append('<div class="diff-streaming-indicator">Waiting for content...</div>')
    elif not replace:
        # Only have search text - show as deletions (preview what will be replaced)
        search_lines = search.split("\n")
        for i, line in enumerate(search_lines):
            escaped_line = html.escape(line)
            is_last = i == len(search_lines) - 1
            cursor = '<span class="diff-cursor">▋</span>' if is_streaming and is_last else ""
            lines.append('<div class="diff-line deletion">')
            lines.append('<span class="diff-line-number">-</span>')
            lines.append(f'<span class="diff-line-content">{escaped_line}{cursor}</span>')
            lines.append("</div>")
    else:
        # Have both - compute and show actual diff
        search_lines = search.split("\n")
        replace_lines = replace.split("\n")

        # Use difflib to get a proper unified diff
        diff = list(
            difflib.unified_diff(
                search_lines,
                replace_lines,
                lineterm="",
                n=0,  # No context lines - show all changes
            )
        )

        # Skip the header lines (---, +++, @@)
        diff_lines = [d for d in diff if not d.startswith(("---", "+++", "@@"))]

        if not diff_lines:
            # No actual differences (shouldn't happen, but handle it)
            for i, line in enumerate(replace_lines):
                escaped_line = html.escape(line)
                lines.append('<div class="diff-line context">')
                lines.append(f'<span class="diff-line-number">{i + 1}</span>')
                lines.append(f'<span class="diff-line-content">{escaped_line}</span>')
                lines.append("</div>")
        else:
            # Render diff lines
            for i, diff_line in enumerate(diff_lines):
                if not diff_line:
                    continue

                prefix = diff_line[0] if diff_line else " "
                content = diff_line[1:] if len(diff_line) > 1 else ""
                escaped_content = html.escape(content)

                is_last = i == len(diff_lines) - 1
                cursor = (
                    '<span class="diff-cursor">▋</span>'
                    if is_streaming and streaming_phase == "replace" and is_last
                    else ""
                )

                if prefix == "-":
                    lines.append('<div class="diff-line deletion">')
                    lines.append('<span class="diff-line-number">-</span>')
                    lines.append(f'<span class="diff-line-content">{escaped_content}</span>')
                    lines.append("</div>")
                elif prefix == "+":
                    lines.append('<div class="diff-line addition">')
                    lines.append('<span class="diff-line-number">+</span>')
                    lines.append(
                        f'<span class="diff-line-content">{escaped_content}{cursor}</span>'
                    )
                    lines.append("</div>")
                else:
                    lines.append('<div class="diff-line context">')
                    lines.append('<span class="diff-line-number"></span>')
                    lines.append(f'<span class="diff-line-content">{escaped_content}</span>')
                    lines.append("</div>")

    lines.append("</div>")  # diff-content
    lines.append("</div>")  # diff-view

    return "\n".join(lines)


def render_streaming_tool_html(tool_call: dict[str, object]) -> str | None:
    """
    Render a streaming tool call as HTML.

    For search_replace, returns a diff view.
    For other tools, returns None (use default rendering).

    Args:
        tool_call: The tool call dict with function.name and function.arguments

    Returns:
        HTML string for special rendering, or None for default
    """
    func = tool_call.get("function", {})
    if not isinstance(func, dict):
        return None

    name = func.get("name", "")
    args_str = func.get("arguments", "")

    if name != "search_replace" or not isinstance(args_str, str):
        return None

    # Parse the streaming arguments
    parsed = parse_partial_json(args_str)

    filepath = parsed.get("filepath", "")
    search = parsed.get("search", "")
    replace = parsed.get("replace", "")

    # Determine streaming phase
    if "replace" in parsed:
        streaming_phase = "replace"
    elif "search" in parsed:
        streaming_phase = "search"
    else:
        streaming_phase = "search"

    return render_diff_html(
        filepath=filepath,
        search=search,
        replace=replace,
        is_streaming=True,
        streaming_phase=streaming_phase,
    )
