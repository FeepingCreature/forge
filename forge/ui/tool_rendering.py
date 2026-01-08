"""
Tool visualization utilities for the AI chat widget.

Provides HTML/CSS rendering for built-in tools:
- search_replace: Live diff view as LLM streams search/replace params
- write_file: File creation/overwrite indicator
- delete_file: File deletion indicator
- update_context: Context modification summary
- grep_open: Search results with match counts
- get_lines: Line excerpt display

Local/user tools use default JSON rendering.
"""

import difflib
import html
import json
import re


def get_diff_styles() -> str:
    """Return CSS styles for the diff view (light theme to match chat UI)"""
    return """
        .diff-view {
            font-family: "Courier New", Consolas, monospace;
            font-size: 12px;
            background: #f8f8f8;
            border: 1px solid #e0e0e0;
            border-radius: 6px;
            overflow: hidden;
            margin: 8px 0;
        }
        .diff-header {
            background: #f0f0f0;
            color: #333;
            padding: 8px 12px;
            font-weight: bold;
            border-bottom: 1px solid #e0e0e0;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .diff-header .filepath {
            color: #795e26;
        }
        .diff-header .status {
            color: #888;
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
            color: #999;
            background: #f0f0f0;
            user-select: none;
            border-right: 1px solid #e0e0e0;
        }
        .diff-line-content {
            flex: 1;
            padding-left: 12px;
            white-space: pre;
        }
        .diff-line.deletion {
            background: #ffebe9;
        }
        .diff-line.deletion .diff-line-content {
            color: #b31d28;
        }
        .diff-line.deletion .diff-line-number {
            background: #ffd7d5;
            color: #b31d28;
        }
        .diff-line.addition {
            background: #e6ffec;
        }
        .diff-line.addition .diff-line-content {
            color: #22863a;
        }
        .diff-line.addition .diff-line-number {
            background: #cdffd8;
            color: #22863a;
        }
        .diff-line.context {
            background: transparent;
        }
        .diff-line.context .diff-line-content {
            color: #333;
        }
        .diff-separator {
            background: #f0f0f0;
            border-top: 1px solid #e0e0e0;
            border-bottom: 1px solid #e0e0e0;
            padding: 4px 12px;
            color: #888;
            font-size: 11px;
            text-align: center;
            user-select: none;
        }
        .diff-cursor {
            animation: diff-blink 1s step-end infinite;
            color: #0066cc;
        }
        @keyframes diff-blink {
            0%, 100% { opacity: 1; }
            50% { opacity: 0; }
        }
        .diff-streaming-indicator {
            color: #888;
            font-style: italic;
            padding: 4px 12px 8px;
            font-size: 11px;
        }

        /* Tool call card styles (shared by all tools) */
        .tool-card {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            font-size: 13px;
            background: #f8f9fa;
            border: 1px solid #e0e0e0;
            border-radius: 6px;
            overflow: hidden;
            margin: 8px 0;
        }
        .tool-card-header {
            background: #f0f0f0;
            color: #333;
            padding: 8px 12px;
            font-weight: 600;
            border-bottom: 1px solid #e0e0e0;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .tool-card-header .tool-icon {
            font-size: 16px;
        }
        .tool-card-header .tool-name {
            color: #1976d2;
        }
        .tool-card-body {
            padding: 10px 12px;
        }
        .tool-card-body code {
            background: #e8e8e8;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: "Courier New", Consolas, monospace;
            font-size: 12px;
        }
        .tool-card-body .filepath {
            color: #795e26;
            font-weight: 500;
        }
        .tool-card-body .file-list {
            margin: 6px 0;
            padding-left: 20px;
        }
        .tool-card-body .file-list li {
            margin: 3px 0;
            color: #555;
        }
        .tool-card-body .match-count {
            color: #888;
            font-size: 12px;
        }
        .tool-card-body .line-excerpt {
            background: #fff;
            border: 1px solid #ddd;
            border-radius: 4px;
            padding: 8px;
            margin-top: 8px;
            font-family: "Courier New", Consolas, monospace;
            font-size: 12px;
            overflow-x: auto;
            white-space: pre;
        }
        .tool-card-body .line-target {
            background: #fff3cd;
            display: block;
        }
        .tool-card-body .stats {
            color: #666;
            font-size: 12px;
            margin-top: 6px;
        }
        .tool-card-body .error-msg {
            color: #d32f2f;
            font-weight: 500;
        }
        .tool-card-body .success-msg {
            color: #388e3c;
            font-weight: 500;
        }
        .tool-card.streaming .tool-card-header::after {
            content: " ▋";
            animation: diff-blink 1s step-end infinite;
            color: #1976d2;
        }

        /* Think tool foldout styles */
        .think-foldout {
            margin: 6px 0;
        }
        .think-toggle-input {
            display: none;
        }
        .think-toggle-label {
            cursor: pointer;
            color: #666;
            font-size: 12px;
            user-select: none;
        }
        .think-toggle-label:hover {
            color: #333;
        }
        .think-toggle-label::before {
            content: "▶ ";
            font-size: 10px;
        }
        .think-toggle-input:checked + .think-toggle-label::before {
            content: "▼ ";
        }
        .think-scratchpad-wrapper {
            margin-top: 6px;
        }
        /* Outer container with fixed max-height, uses flex column-reverse for auto-scroll */
        .think-scratchpad-outer {
            max-height: 5.5em;  /* ~4 lines - collapsed by default */
            overflow-y: auto;
            display: flex;
            flex-direction: column-reverse;
            background: #fff;
            border: 1px solid #ddd;
            border-radius: 4px;
        }
        /* Inner content - flex-direction: column-reverse makes this stick to bottom */
        .think-scratchpad {
            padding: 8px;
            margin: 0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            font-size: 12px;
            white-space: pre-wrap;
            word-wrap: break-word;
            color: #555;
        }
        /* Expanded: larger height */
        .think-toggle-input:checked ~ .think-scratchpad-wrapper .think-scratchpad-outer {
            max-height: 300px;
        }
    """


def parse_partial_json(json_str: str) -> dict[str, object]:
    """
    Parse a potentially incomplete JSON object.

    Returns whatever fields we can extract, even from partial JSON.
    This handles the streaming case where we might have:
    - {"filepath": "foo.py", "search": "hello
    - {"filepath": "foo.py", "search": "hello", "replace": "wor
    """
    result: dict[str, object] = {}

    # First try complete JSON parse
    try:
        parsed = json.loads(json_str)
        if isinstance(parsed, dict):
            for key in ("filepath", "search", "replace", "scratchpad", "conclusion"):
                if key in parsed and isinstance(parsed[key], str):
                    result[key] = parsed[key]
        return result
    except json.JSONDecodeError:
        pass

    # Incomplete JSON - extract what we can
    # Look for each field pattern: "fieldname": "value or "fieldname":"value
    for field in ("filepath", "search", "replace", "scratchpad", "conclusion"):
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
        # Only have search text - show as normal/context (not red yet)
        # Red only appears once we have replacement text to contrast with
        search_lines = search.split("\n")
        for i, line in enumerate(search_lines):
            escaped_line = html.escape(line)
            is_last = i == len(search_lines) - 1
            cursor = '<span class="diff-cursor">▋</span>' if is_streaming and is_last else ""
            lines.append('<div class="diff-line context">')
            lines.append('<span class="diff-line-number"></span>')
            lines.append(f'<span class="diff-line-content">{escaped_line}{cursor}</span>')
            lines.append("</div>")
    else:
        # Have both - compute and show actual diff
        search_lines = search.split("\n")
        replace_lines = replace.split("\n")

        # Use difflib to get a proper unified diff with 2 lines of context
        diff = list(
            difflib.unified_diff(
                search_lines,
                replace_lines,
                lineterm="",
                n=2,  # 2 context lines before/after changes
            )
        )

        # Process diff output, handling @@ hunk headers as separators
        # Skip the --- and +++ header lines
        diff_lines = [d for d in diff if not d.startswith(("---", "+++"))]

        if not diff_lines:
            # No actual differences (shouldn't happen, but handle it)
            for i, line in enumerate(replace_lines):
                escaped_line = html.escape(line)
                lines.append('<div class="diff-line context">')
                lines.append(f'<span class="diff-line-number">{i + 1}</span>')
                lines.append(f'<span class="diff-line-content">{escaped_line}</span>')
                lines.append("</div>")
        else:
            # When streaming, find the last contiguous block of deletions
            # (deletions not followed by any additions). These should show
            # as context/normal since we haven't seen their replacements yet.
            # Only consider actual diff lines, not @@ headers
            actual_diff_lines = [d for d in diff_lines if not d.startswith("@@")]
            trailing_deletion_start = -1
            if is_streaming and streaming_phase == "replace":
                # Walk backwards to find where trailing deletions start
                last_addition_idx = -1
                for i in range(len(actual_diff_lines) - 1, -1, -1):
                    if actual_diff_lines[i] and actual_diff_lines[i][0] == "+":
                        last_addition_idx = i
                        break
                # All deletions after the last addition are "trailing"
                if last_addition_idx < len(actual_diff_lines) - 1:
                    trailing_deletion_start = last_addition_idx + 1

            # Track whether we've seen any content yet (to skip separator before first hunk)
            first_hunk = True
            # Track position in actual_diff_lines for trailing deletion detection
            actual_line_idx = 0

            # Render diff lines
            for diff_line in diff_lines:
                if not diff_line:
                    continue

                # Handle @@ hunk headers - render as separators between hunks
                if diff_line.startswith("@@"):
                    if not first_hunk:
                        # Add a visual separator between hunks
                        lines.append('<div class="diff-separator">⋯</div>')
                    first_hunk = False
                    continue

                prefix = diff_line[0] if diff_line else " "
                content = diff_line[1:] if len(diff_line) > 1 else ""
                escaped_content = html.escape(content)

                is_last = actual_line_idx == len(actual_diff_lines) - 1
                cursor = (
                    '<span class="diff-cursor">▋</span>'
                    if is_streaming and streaming_phase == "replace" and is_last
                    else ""
                )

                # Check if this deletion is in the trailing block (show as context)
                is_trailing_deletion = (
                    prefix == "-"
                    and trailing_deletion_start != -1
                    and actual_line_idx >= trailing_deletion_start
                )

                if prefix == "-" and not is_trailing_deletion:
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
                    # Context lines, or trailing deletions shown as context
                    lines.append('<div class="diff-line context">')
                    lines.append('<span class="diff-line-number"></span>')
                    lines.append(
                        f'<span class="diff-line-content">{escaped_content}{cursor}</span>'
                    )
                    lines.append("</div>")

                actual_line_idx += 1

    lines.append("</div>")  # diff-content
    lines.append("</div>")  # diff-view

    return "\n".join(lines)


def render_completed_diff_html(filepath: str, search: str, replace: str) -> str:
    """
    Render a completed diff view (no cursor, all deletions shown as red).

    Args:
        filepath: Path to the file being edited
        search: The search text (content being replaced)
        replace: The replace text (new content)

    Returns:
        HTML string for the completed diff view
    """
    return render_diff_html(
        filepath=filepath,
        search=search,
        replace=replace,
        is_streaming=False,
        streaming_phase="replace",
    )


# =============================================================================
# Native rendering for all built-in tools
# =============================================================================


def render_write_file_html(args: dict[str, object], is_streaming: bool = False) -> str:
    """Render write_file tool call as HTML."""
    filepath = args.get("filepath", "")
    content = args.get("content", "")

    escaped_filepath = html.escape(str(filepath)) if filepath else "..."
    streaming_class = " streaming" if is_streaming else ""

    # Show line count and size
    if content and isinstance(content, str):
        line_count = content.count("\n") + 1
        byte_count = len(content)
        stats = f"{line_count} lines, {byte_count} bytes"
    else:
        stats = "..."

    return f"""
    <div class="tool-card{streaming_class}">
        <div class="tool-card-header">
            <span class="tool-icon">📝</span>
            <span class="tool-name">write_file</span>
        </div>
        <div class="tool-card-body">
            <code class="filepath">{escaped_filepath}</code>
            <div class="stats">{stats}</div>
        </div>
    </div>
    """


def render_delete_file_html(args: dict[str, object], is_streaming: bool = False) -> str:
    """Render delete_file tool call as HTML."""
    filepath = args.get("filepath", "")

    escaped_filepath = html.escape(str(filepath)) if filepath else "..."
    streaming_class = " streaming" if is_streaming else ""

    return f"""
    <div class="tool-card{streaming_class}">
        <div class="tool-card-header">
            <span class="tool-icon">🗑️</span>
            <span class="tool-name">delete_file</span>
        </div>
        <div class="tool-card-body">
            <code class="filepath">{escaped_filepath}</code>
        </div>
    </div>
    """


def render_update_context_html(args: dict[str, object], is_streaming: bool = False) -> str:
    """Render update_context tool call as HTML."""
    add_files = args.get("add", [])
    remove_files = args.get("remove", [])

    streaming_class = " streaming" if is_streaming else ""

    body_parts = []

    if add_files and isinstance(add_files, list):
        body_parts.append("<div><strong>Adding:</strong></div>")
        body_parts.append('<ul class="file-list">')
        for f in add_files:
            escaped = html.escape(str(f))
            body_parts.append(f"<li><code>{escaped}</code></li>")
        body_parts.append("</ul>")

    if remove_files and isinstance(remove_files, list):
        body_parts.append("<div><strong>Removing:</strong></div>")
        body_parts.append('<ul class="file-list">')
        for f in remove_files:
            escaped = html.escape(str(f))
            body_parts.append(f"<li><code>{escaped}</code></li>")
        body_parts.append("</ul>")

    if not body_parts:
        body_parts.append("<div>No changes specified...</div>")

    body_html = "".join(body_parts)

    return f"""
    <div class="tool-card{streaming_class}">
        <div class="tool-card-header">
            <span class="tool-icon">📂</span>
            <span class="tool-name">update_context</span>
        </div>
        <div class="tool-card-body">
            {body_html}
        </div>
    </div>
    """


def render_grep_open_html(
    args: dict[str, object],
    is_streaming: bool = False,
    result: dict[str, object] | None = None,
) -> str:
    """Render grep_open tool call as HTML."""
    pattern = args.get("pattern", "")
    include_extensions = args.get("include_extensions", [])

    escaped_pattern = html.escape(str(pattern)) if pattern else "..."
    streaming_class = " streaming" if is_streaming else ""

    filter_info = ""
    if include_extensions and isinstance(include_extensions, list):
        exts = ", ".join(str(e) for e in include_extensions)
        filter_info = f'<div class="stats">Filtering: {html.escape(exts)}</div>'

    # Show results if we have them (completed tool call)
    results_html = ""
    if result and not is_streaming:
        matches = result.get("matches", [])
        if matches and isinstance(matches, list):
            results_html = "<div><strong>Files added to context:</strong></div>"
            results_html += '<ul class="file-list">'
            for match in matches:
                if isinstance(match, dict):
                    filepath = match.get("filepath", "")
                    match_count = match.get("match_count", 0)
                    escaped_fp = html.escape(str(filepath))
                    results_html += f'<li><code>{escaped_fp}</code> <span class="match-count">({match_count} matches)</span></li>'
            results_html += "</ul>"
        elif result.get("message"):
            msg = html.escape(str(result.get("message", "")))
            results_html = f'<div class="stats">{msg}</div>'

    return f"""
    <div class="tool-card{streaming_class}">
        <div class="tool-card-header">
            <span class="tool-icon">🔍</span>
            <span class="tool-name">grep_open</span>
        </div>
        <div class="tool-card-body">
            <div>Pattern: <code>{escaped_pattern}</code></div>
            {filter_info}
            {results_html}
        </div>
    </div>
    """


def render_get_lines_html(args: dict[str, object], is_streaming: bool = False) -> str:
    """Render get_lines tool call as HTML."""
    filepath = args.get("filepath", "")
    line = args.get("line", "")
    context = args.get("context", 10)

    escaped_filepath = html.escape(str(filepath)) if filepath else "..."
    streaming_class = " streaming" if is_streaming else ""

    line_info = f"Line {line}" if line else "..."
    if context and context != 10:
        line_info += f" (±{context} context)"

    return f"""
    <div class="tool-card{streaming_class}">
        <div class="tool-card-header">
            <span class="tool-icon">📋</span>
            <span class="tool-name">get_lines</span>
        </div>
        <div class="tool-card-body">
            <code class="filepath">{escaped_filepath}</code>
            <div class="stats">{line_info}</div>
        </div>
    </div>
    """


def render_streaming_tool_html(tool_call: dict[str, object]) -> str | None:
    """
    Render a streaming tool call as HTML.

    Returns native HTML for all built-in tools.
    Returns None for unknown tools (use default rendering).

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

    if not isinstance(args_str, str):
        return None

    # Parse arguments (may be partial JSON during streaming)
    parsed = parse_partial_json(args_str)

    # Try full JSON parse for complete arguments
    try:
        full_args = json.loads(args_str) if args_str else {}
        if isinstance(full_args, dict):
            parsed = full_args
    except json.JSONDecodeError:
        pass  # Use partial parse result

    # Route to appropriate renderer
    if name == "search_replace":
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
            filepath=str(filepath),
            search=str(search),
            replace=str(replace),
            is_streaming=True,
            streaming_phase=streaming_phase,
        )
    elif name == "write_file":
        return render_write_file_html(parsed, is_streaming=True)
    elif name == "delete_file":
        return render_delete_file_html(parsed, is_streaming=True)
    elif name == "update_context":
        return render_update_context_html(parsed, is_streaming=True)
    elif name == "grep_open":
        return render_grep_open_html(parsed, is_streaming=True)
    elif name == "get_lines":
        return render_get_lines_html(parsed, is_streaming=True)
    elif name == "think":
        # Think tool - show conclusion during streaming
        return render_think_html(parsed, result=None)
    elif name == "run_tests":
        # Run tests - show as streaming card
        return render_run_tests_html(parsed, result=None)
    elif name in ("say", "done"):
        # These are "in-flow" tools - displayed as assistant text, not tool cards
        return ""
    else:
        return None  # Unknown tool - use default rendering


def render_completed_tool_html(
    name: str, args: dict[str, object], result: dict[str, object] | None = None
) -> str | None:
    """
    Render a completed tool call as HTML.

    Returns native HTML for all built-in tools.
    Returns None for unknown tools (use default rendering).

    Args:
        name: Tool name
        args: Parsed tool arguments
        result: Tool execution result (optional, for tools like grep_open that show results)

    Returns:
        HTML string for special rendering, or None for default
    """
    if name == "search_replace":
        filepath = args.get("filepath", "")
        search = args.get("search", "")
        replace = args.get("replace", "")
        return render_completed_diff_html(str(filepath), str(search), str(replace))
    elif name == "write_file":
        return render_write_file_html(args, is_streaming=False)
    elif name == "delete_file":
        return render_delete_file_html(args, is_streaming=False)
    elif name == "update_context":
        return render_update_context_html(args, is_streaming=False)
    elif name == "grep_open":
        return render_grep_open_html(args, is_streaming=False, result=result)
    elif name == "get_lines":
        return render_get_lines_html(args, is_streaming=False)
    elif name == "compact":
        return render_compact_html(args, result)
    elif name == "commit":
        return render_commit_html(args, result)
    elif name == "think":
        return render_think_html(args, result)
    elif name == "run_tests":
        return render_run_tests_html(args, result)
    elif name in ("say", "done"):
        # These are "in-flow" tools - displayed as assistant text, not tool cards
        return ""
    else:
        return None  # Unknown tool - use default rendering


def render_compact_html(args: dict[str, object], result: dict[str, object] | None = None) -> str:
    """Render compact tool call as HTML."""
    tool_call_ids = args.get("tool_call_ids", [])
    summary = args.get("summary", "")

    escaped_summary = html.escape(str(summary)) if summary else "..."

    # Count how many were compacted
    count = len(tool_call_ids) if isinstance(tool_call_ids, list) else 0
    status = ""
    if result:
        if result.get("success"):
            compacted = result.get("compacted", count)
            status = f'<span class="success-msg">✓ Compacted {compacted} tool result(s)</span>'
        else:
            error = result.get("error", "Unknown error")
            status = f'<span class="error-msg">✗ {html.escape(str(error))}</span>'

    return f"""
    <div class="tool-card">
        <div class="tool-card-header">
            <span class="tool-icon">📦</span>
            <span class="tool-name">compact</span>
        </div>
        <div class="tool-card-body">
            <div><strong>Summary:</strong> {escaped_summary}</div>
            <div class="stats">Tool calls: {count}</div>
            {status}
        </div>
    </div>
    """


def render_commit_html(args: dict[str, object], result: dict[str, object] | None = None) -> str:
    """Render commit tool call as HTML."""
    message = args.get("message", "")

    escaped_message = html.escape(str(message)) if message else "..."

    status = ""
    if result:
        if result.get("success"):
            commit_oid = result.get("commit", "")
            msg = result.get("message", "")
            status = f'<span class="success-msg">✓ {html.escape(str(msg))} → {commit_oid}</span>'
        else:
            error = result.get("error", "Unknown error")
            status = f'<span class="error-msg">✗ {html.escape(str(error))}</span>'

    return f"""
    <div class="tool-card">
        <div class="tool-card-header">
            <span class="tool-icon">💾</span>
            <span class="tool-name">commit</span>
        </div>
        <div class="tool-card-body">
            <div><code>{escaped_message}</code></div>
            {status}
        </div>
    </div>
    """


def render_think_html(args: dict[str, object], result: dict[str, object] | None = None) -> str:
    """Render think tool call as HTML."""
    scratchpad = args.get("scratchpad", "")
    # Conclusion can come from args, or from result if args was compacted
    conclusion = args.get("conclusion", "")
    if not conclusion and result:
        conclusion = result.get("conclusion", "")

    is_streaming = result is None
    streaming_class = " streaming" if is_streaming else ""

    scratchpad_str = str(scratchpad) if scratchpad else ""
    escaped_conclusion = html.escape(str(conclusion)) if conclusion else ""

    # Scratchpad foldout with outer container for scroll, inner for content
    # Uses flex-direction: column-reverse on outer to auto-scroll to bottom
    scratchpad_html = ""
    if scratchpad_str:
        word_count = len(scratchpad_str.split())
        escaped_full = html.escape(scratchpad_str)

        # Generate unique ID for each think call (allows multiple on page)
        import random

        toggle_id = f"think-toggle-{random.randint(0, 999999)}"

        scratchpad_html = f"""
        <div class="think-foldout">
            <input type="checkbox" id="{toggle_id}" class="think-toggle-input">
            <label for="{toggle_id}" class="think-toggle-label">Scratchpad ({word_count} words)</label>
            <div class="think-scratchpad-wrapper">
                <div class="think-scratchpad-outer">
                    <div class="think-scratchpad">{escaped_full}</div>
                </div>
            </div>
        </div>
        """

    # Conclusion section
    conclusion_html = ""
    if escaped_conclusion:
        conclusion_html = f"<div><strong>Conclusion:</strong> {escaped_conclusion}</div>"
    elif is_streaming:
        conclusion_html = "<div><strong>Conclusion:</strong> ...</div>"

    status = ""
    if result:
        if result.get("success"):
            status = '<span class="success-msg">✓</span>'
        else:
            error = result.get("error", "Unknown error")
            status = f'<span class="error-msg">✗ {html.escape(str(error))}</span>'

    return f"""
    <div class="tool-card{streaming_class}">
        <div class="tool-card-header">
            <span class="tool-icon">💭</span>
            <span class="tool-name">think</span>
        </div>
        <div class="tool-card-body">
            {scratchpad_html}
            {conclusion_html}
            {status}
        </div>
    </div>
    """


def render_run_tests_html(args: dict[str, object], result: dict[str, object] | None = None) -> str:
    """Render run_tests tool call as HTML."""
    pattern = args.get("pattern", "")
    verbose = args.get("verbose", False)

    streaming_class = ""
    if result is None:
        streaming_class = " streaming"

    args_info = ""
    if pattern:
        args_info += f"<div>Pattern: <code>{html.escape(str(pattern))}</code></div>"
    if verbose:
        args_info += "<div>Verbose: on</div>"

    status = ""
    output_html = ""
    if result:
        if result.get("success"):
            passed = result.get("passed", 0)
            failed = result.get("failed", 0)
            if failed:
                status = f'<span class="error-msg">✗ {passed} passed, {failed} failed</span>'
            else:
                status = f'<span class="success-msg">✓ {passed} passed</span>'
        else:
            error = result.get("error", "Unknown error")
            status = f'<span class="error-msg">✗ {html.escape(str(error))}</span>'

        # Show test output (handle linebreaks properly)
        output = result.get("output", "")
        if output:
            escaped_output = html.escape(str(output))
            output_html = f'<pre class="line-excerpt" style="max-height: 300px; overflow-y: auto;">{escaped_output}</pre>'

    return f"""
    <div class="tool-card{streaming_class}">
        <div class="tool-card-header">
            <span class="tool-icon">🧪</span>
            <span class="tool-name">run_tests</span>
        </div>
        <div class="tool-card-body">
            {args_info}
            {status}
            {output_html}
        </div>
    </div>
    """


# =============================================================================
# Inline <edit> block rendering for flow-text edits
# =============================================================================

# Regex patterns for parsing edit blocks in streaming content
EDIT_BLOCK_PATTERN = re.compile(
    r'<edit\s+file="([^"]+)">\s*'
    r"<search>\n?(.*?)\n?</search>\s*"
    r"<replace>\n?(.*?)\n?</replace>\s*"
    r"</edit>",
    re.DOTALL,
)

# Partial patterns for streaming detection
EDIT_START_PATTERN = re.compile(r'<edit\s+file="([^"]*)"?', re.DOTALL)
SEARCH_START_PATTERN = re.compile(r"<search>\n?", re.DOTALL)
SEARCH_END_PATTERN = re.compile(r"\n?</search>", re.DOTALL)
REPLACE_START_PATTERN = re.compile(r"<replace>\n?", re.DOTALL)
REPLACE_END_PATTERN = re.compile(r"\n?</replace>", re.DOTALL)


def render_inline_edits(content: str, is_streaming: bool = True) -> str:
    """
    Render <edit> blocks in content as diff views.

    Replaces <edit>...</edit> blocks with rendered HTML diff views.
    Handles partial/incomplete blocks during streaming.

    Args:
        content: Text content that may contain <edit> blocks
        is_streaming: Whether content is still being streamed

    Returns:
        Content with <edit> blocks replaced by HTML diff views
    """
    result_parts = []
    last_end = 0

    # First, find and render complete edit blocks
    for match in EDIT_BLOCK_PATTERN.finditer(content):
        # Add text before this edit block
        result_parts.append(html.escape(content[last_end : match.start()]))

        # Render the complete edit block as a diff
        filepath = match.group(1)
        search = match.group(2)
        replace = match.group(3)

        diff_html = render_diff_html(
            filepath=filepath,
            search=search,
            replace=replace,
            is_streaming=False,  # Complete block
            streaming_phase="replace",
        )
        result_parts.append(diff_html)
        last_end = match.end()

    # Check for partial edit block at the end (streaming case)
    remaining = content[last_end:]

    if is_streaming and remaining:
        partial_html = _render_partial_edit(remaining)
        if partial_html:
            # Find where the partial edit starts
            edit_start = EDIT_START_PATTERN.search(remaining)
            if edit_start:
                # Add text before the partial edit
                result_parts.append(html.escape(remaining[: edit_start.start()]))
                result_parts.append(partial_html)
            else:
                result_parts.append(html.escape(remaining))
        else:
            result_parts.append(html.escape(remaining))
    else:
        result_parts.append(html.escape(remaining))

    return "".join(result_parts)


def _render_partial_edit(content: str) -> str | None:
    """
    Try to render a partial/incomplete <edit> block during streaming.

    Returns HTML if a partial edit is detected, None otherwise.
    """
    # Check if we have the start of an edit block
    edit_match = EDIT_START_PATTERN.search(content)
    if not edit_match:
        return None

    # Extract filepath (may be incomplete)
    filepath = edit_match.group(1) if edit_match.group(1) else ""

    # Find the content after <edit file="...">
    after_edit = content[edit_match.end() :]

    # Look for <search> tag
    search_start = SEARCH_START_PATTERN.search(after_edit)
    if not search_start:
        # Haven't seen <search> yet
        return render_diff_html(
            filepath=filepath,
            search="",
            replace="",
            is_streaming=True,
            streaming_phase="search",
        )

    after_search_tag = after_edit[search_start.end() :]

    # Look for </search>
    search_end = SEARCH_END_PATTERN.search(after_search_tag)
    if not search_end:
        # Still streaming search content
        search_content = after_search_tag
        return render_diff_html(
            filepath=filepath,
            search=search_content,
            replace="",
            is_streaming=True,
            streaming_phase="search",
        )

    # We have complete search, extract it
    search_content = after_search_tag[: search_end.start()]
    after_search = after_search_tag[search_end.end() :]

    # Look for <replace>
    replace_start = REPLACE_START_PATTERN.search(after_search)
    if not replace_start:
        # Between </search> and <replace>
        return render_diff_html(
            filepath=filepath,
            search=search_content,
            replace="",
            is_streaming=True,
            streaming_phase="replace",
        )

    after_replace_tag = after_search[replace_start.end() :]

    # Look for </replace>
    replace_end = REPLACE_END_PATTERN.search(after_replace_tag)
    if not replace_end:
        # Still streaming replace content
        replace_content = after_replace_tag
        return render_diff_html(
            filepath=filepath,
            search=search_content,
            replace=replace_content,
            is_streaming=True,
            streaming_phase="replace",
        )

    # Complete replace but no </edit> yet - still render as streaming
    replace_content = after_replace_tag[: replace_end.start()]
    return render_diff_html(
        filepath=filepath,
        search=search_content,
        replace=replace_content,
        is_streaming=True,
        streaming_phase="replace",
    )
