"""
Inline edit parsing and execution for flow-text edits.

This module handles <edit> blocks that appear in assistant message text,
allowing edits to be made without tool calls (avoiding round-trip costs
when the AI narrates after edits).

Syntax:
    <edit file="path/to/file.py">
    <search>
    exact text to find
    </search>
    <replace>
    replacement text
    </replace>
    </edit>

For files containing XML-like syntax, use escape="html":
    <edit file="path/to/file.py" escape="html">
    <search>
    content with &lt;tags&gt;
    </search>
    <replace>
    new content
    </replace>
    </edit>
"""

import difflib
import html
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.vfs.base import VFS


def _find_best_match(search: str, content: str) -> tuple[str, int, int]:
    """
    Find the closest matching text in content to the search string.

    Returns (best_match, edit_distance, position).
    """
    search_lines = search.split("\n")
    content_lines = content.split("\n")
    search_len = len(search_lines)

    if not content_lines:
        return ("", len(search), 0)

    best_match = ""
    best_distance = float("inf")
    best_pos = 0

    # Sliding window over content lines
    for i in range(max(1, len(content_lines) - search_len + 1)):
        window = "\n".join(content_lines[i : i + search_len])

        # Use SequenceMatcher for similarity
        matcher = difflib.SequenceMatcher(None, search, window)
        # Convert ratio to edit distance approximation
        ratio = matcher.ratio()
        distance = int((1 - ratio) * max(len(search), len(window)))

        if distance < best_distance:
            best_distance = distance
            best_match = window
            # Calculate character position
            best_pos = sum(len(line) + 1 for line in content_lines[:i])

    return (best_match, int(best_distance), best_pos)


def _generate_diff(search: str, actual: str) -> str:
    """Generate a unified diff between search text and actual text."""
    search_lines = search.splitlines(keepends=True)
    actual_lines = actual.splitlines(keepends=True)

    diff = difflib.unified_diff(search_lines, actual_lines, fromfile="expected", tofile="actual")

    return "".join(diff)


@dataclass
class EditBlock:
    """A parsed edit block from assistant message text."""

    file: str
    search: str
    replace: str
    # Position in original text for truncation on failure
    start_pos: int
    end_pos: int
    # Whether HTML entities should be unescaped
    escape_html: bool = False


# Regex to match <edit file="...">...</edit> blocks
# Using DOTALL so . matches newlines
# Key: use negative lookahead to prevent matching across </edit> boundaries
# The (?:(?!</edit>).)*? pattern matches any char that's not the start of </edit>
# Optional escape="html" attribute for editing files containing XML-like syntax
EDIT_PATTERN = re.compile(
    r'<edit\s+file="([^"]+)"(?:\s+escape="(html)")?\s*>\s*'
    r"<search>\n?((?:(?!</edit>).)*?)\n?</search>\s*"
    r"<replace>\n?((?:(?!</edit>).)*?)\n?</replace>\s*"
    r"</edit>",
    re.DOTALL,
)


def get_inline_pattern() -> re.Pattern[str]:
    """Return compiled regex for inline invocation."""
    return EDIT_PATTERN


def parse_inline_match(match: re.Match[str]) -> dict[str, Any]:
    """Parse regex match into tool arguments."""
    return {
        "filepath": match.group(1),
        "escape_html": match.group(2) == "html",
        "search": match.group(3),
        "replace": match.group(4),
    }


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "invocation": "inline",
        "inline_syntax": '<edit file="path"><search>old</search><replace>new</replace></edit>',
        "function": {
            "name": "edit",
            "description": "Edit a file by searching for exact text and replacing it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string"},
                    "search": {"type": "string"},
                    "replace": {"type": "string"},
                    "escape_html": {"type": "boolean"},
                },
                "required": ["filepath", "search", "replace"],
            },
        },
    }


def parse_edits(content: str) -> list[EditBlock]:
    """
    Parse <edit> blocks from assistant message content.

    Returns list of EditBlock objects in order of appearance.
    """
    edits = []
    for match in EDIT_PATTERN.finditer(content):
        filepath = match.group(1)
        escape_attr = match.group(2)  # "html" or None
        search = match.group(3)
        replace = match.group(4)

        edits.append(
            EditBlock(
                file=filepath,
                search=search,
                replace=replace,
                start_pos=match.start(),
                end_pos=match.end(),
                escape_html=(escape_attr == "html"),
            )
        )

    return edits


def execute(vfs: "VFS", args: dict[str, Any]) -> dict[str, Any]:
    """
    Execute an edit from parsed arguments.

    This is the standard tool execute interface.
    """
    filepath = args.get("filepath", "")
    search = args.get("search", "")
    replace = args.get("replace", "")
    escape_html = args.get("escape_html", False)

    # Unescape HTML entities if escape="html" was specified
    if escape_html:
        search = html.unescape(search)
        replace = html.unescape(replace)

    return _do_edit(vfs, filepath, search, replace)


def execute_edit(vfs: "VFS", edit: EditBlock) -> dict[str, Any]:
    """
    Execute a single edit block (legacy interface).

    Returns a result dict similar to search_replace tool:
    - {"success": True, "message": "..."} on success
    - {"success": False, "error": "..."} on failure
    """
    search = edit.search
    replace = edit.replace

    # Unescape HTML entities if escape="html" was specified
    # This allows editing files that contain <search>, <edit>, etc.
    if edit.escape_html:
        search = html.unescape(search)
        replace = html.unescape(replace)

    return _do_edit(vfs, edit.file, search, replace)


def _do_edit(vfs: "VFS", filepath: str, search: str, replace: str) -> dict[str, Any]:
    """
    Core edit logic shared by execute() and execute_edit().
    """
    if not filepath or not isinstance(filepath, str):
        return {"success": False, "error": "filepath must be a non-empty string"}

    # Read current state from VFS
    try:
        content = vfs.read_file(filepath)
    except FileNotFoundError:
        return {"success": False, "error": f"File not found: {filepath}"}

    if search not in content:
        # Find the closest matching text to help diagnose the issue
        best_match, distance, pos = _find_best_match(search, content)

        # Calculate similarity percentage
        max_len = max(len(search), len(best_match))
        similarity = ((max_len - distance) / max_len * 100) if max_len > 0 else 0

        # Generate a diff to show what's different
        diff = _generate_diff(search, best_match)

        # Find the line number of the best match
        line_num = content[:pos].count("\n") + 1

        # Build focused error message
        if similarity >= 95:
            hint = (
                "VERY CLOSE MATCH - check the diff carefully for:\n"
                "  • Trailing whitespace or newlines\n"
                "  • Small typos\n"
                "  • Indentation differences"
            )
        elif similarity >= 80:
            hint = (
                "CLOSE MATCH - the text exists but has differences.\n"
                "Check the diff below to see exactly what's different."
            )
        else:
            hint = (
                "NO CLOSE MATCH - the text may have been modified or doesn't exist.\n"
                "You may need to reload the file to see current content."
            )

        # Show just first/last few lines of the match for context
        match_lines = best_match.split("\n")
        if len(match_lines) > 6:
            match_preview = "\n".join(match_lines[:3] + ["    ..."] + match_lines[-2:])
        else:
            match_preview = best_match

        error_msg = (
            f"Search text not found in {filepath}.\n\n"
            f"**{hint}**\n\n"
            f"Found {similarity:.1f}% similar text at line {line_num} "
            f"(edit distance: {distance}):\n\n"
            f"```\n{match_preview}\n```\n\n"
            f"Diff (--- your search vs +++ actual file):\n"
            f"```diff\n{diff}\n```"
        )

        return {"success": False, "error": error_msg}

    # Replace first occurrence
    new_content = content.replace(search, replace, 1)

    # Write back to VFS
    vfs.write_file(filepath, new_content)

    return {"success": True, "message": f"Replaced in {filepath}"}


def execute_edits(vfs: "VFS", edits: list[EditBlock]) -> tuple[list[dict[str, Any]], int | None]:
    """
    Execute a list of edit blocks sequentially.

    Stops on first failure (like tool chain behavior).

    Returns:
        (results, failed_index) where:
        - results: list of result dicts for executed edits
        - failed_index: index of first failed edit, or None if all succeeded
    """
    results = []
    failed_index = None

    for i, edit in enumerate(edits):
        result = execute_edit(vfs, edit)
        results.append(result)

        if not result.get("success"):
            failed_index = i
            break

    return results, failed_index
