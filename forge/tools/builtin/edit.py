"""
Inline edit parsing and execution for flow-text edits.

This module handles <edit> blocks that appear in assistant message text,
allowing edits to be made without tool calls (avoiding round-trip costs
when the AI narrates after edits).

Standard syntax:
    <edit file="path/to/file.py">
    <search>
    exact text to find
    </search>
    <replace>
    replacement text
    </replace>
    </edit>

Nonced syntax (use when search/replace bodies contain edit-related XML):
    <edit_NONCE file="path/to/file.py">
    <search_NONCE>
    text containing </search>, </edit>, etc.
    </search_NONCE>
    <replace_NONCE>
    replacement
    </replace_NONCE>
    </edit_NONCE>

NONCE can be any sequence of word characters (letters/digits/underscore).
The same nonce must appear on edit, search, and replace tags within one block.
"""

import difflib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from forge.tools.side_effects import SideEffect

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


# --- Patterns ---------------------------------------------------------------
#
# A single regex handles both plain and nonced forms. Group 1 captures the
# suffix on the opening tag — either an empty string (plain form) or the
# literal "_NONCE" (nonced form). The same suffix is backreferenced into all
# closing tags via `\1`, so:
#   plain:  <edit ...> ... </edit>
#   nonced: <edit_x9k ...> ... </edit_x9k>
# both match the same pattern.
#
# The suffix uses `(_\w+|)` rather than `(_\w+)?` so that group 1 always
# participates in the match (Python regex backreferences fail when the
# referenced group didn't participate, even with a default empty string).
#
# The body uses `(?!</edit\1>)` as a negative lookahead so that a stray
# closing tag inside the body terminates the block cleanly. In the plain
# form this means </edit> can't appear inside a body — use the nonced
# form for those cases. In the nonced form the lookahead is </edit_NONCE>
# which is unique enough that body content won't collide.

EDIT_PATTERN = re.compile(
    r'<edit(_\w+|)\s+file="([^"]+)"\s*>\s*'
    r"<search\1>\n?((?:(?!</edit\1>).)*?)\n?</search\1>\s*"
    r"<replace\1>\n?((?:(?!</edit\1>).)*?)\n?</replace\1>\s*"
    r"</edit\1>",
    re.DOTALL,
)

WRITE_PATTERN = re.compile(
    r'<edit(_\w+|)\s+file="([^"]+)"\s*>\n?'
    r"((?:(?!<search\1>|</edit\1>).)*?)"
    r"\n?</edit\1>",
    re.DOTALL,
)

# Loose detector: anything that *looks* like an opening <edit ...> tag,
# plain or nonced. Used to detect blocks that opened but didn't parse
# cleanly, so we can surface a parse error to the AI instead of silently
# dropping the edit.
EDIT_OPEN_DETECTOR = re.compile(r'<edit(?:_\w+)?\s+file="[^"]+"\s*>')


def get_inline_pattern() -> re.Pattern[str]:
    """Return compiled regex for inline invocation (search/replace).

    Matches both plain <edit> and nonced <edit_NONCE> forms.
    """
    return EDIT_PATTERN


def get_write_pattern() -> re.Pattern[str]:
    """Return compiled regex for whole-file write invocation.

    Matches both plain <edit> and nonced <edit_NONCE> forms.
    """
    return WRITE_PATTERN


def parse_inline_match(match: re.Match[str]) -> dict[str, Any]:
    """Parse a search/replace match into tool arguments.

    Group 1 is the suffix ('' or '_NONCE'); groups 2-4 are file/search/replace.
    """
    return {
        "filepath": match.group(2),
        "search": match.group(3),
        "replace": match.group(4),
    }


def parse_write_match(match: re.Match[str]) -> dict[str, Any]:
    """Parse a whole-file write match into tool arguments.

    Group 1 is the suffix ('' or '_NONCE'); groups 2-3 are file/content.
    """
    return {
        "filepath": match.group(2),
        "content": match.group(3),
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
                },
                "required": ["filepath", "search", "replace"],
            },
        },
    }


def parse_edits(content: str) -> list[EditBlock]:
    """
    Parse <edit> blocks from assistant message content.

    Returns list of EditBlock objects in order of appearance. The unified
    EDIT_PATTERN matches both plain <edit> and nonced <edit_NONCE> forms.
    Whole-file writes are not returned by this function (they have no
    search/replace) — see WRITE_PATTERN handling in the inline command
    dispatcher.
    """
    return [
        EditBlock(
            file=match.group(2),
            search=match.group(3),
            replace=match.group(4),
            start_pos=match.start(),
            end_pos=match.end(),
        )
        for match in EDIT_PATTERN.finditer(content)
    ]


def detect_unparsed_edit_blocks(
    content: str, parsed_spans: list[tuple[int, int]]
) -> list[tuple[int, str]]:
    """
    Find <edit ...> opening tags that did NOT result in a successful parse.

    Args:
        content: Full assistant message text.
        parsed_spans: List of (start, end) spans that were successfully parsed
            as edit/write commands.

    Returns:
        List of (position, snippet) for each unparsed opening tag, where
        snippet is a short excerpt for diagnostics.
    """
    unparsed: list[tuple[int, str]] = []
    for m in EDIT_OPEN_DETECTOR.finditer(content):
        pos = m.start()
        # Skip if this opening tag is inside a successfully-parsed span.
        if any(start <= pos < end for start, end in parsed_spans):
            continue
        snippet = content[pos : pos + 120].replace("\n", " ")
        unparsed.append((pos, snippet))
    return unparsed


def execute(vfs: "VFS", args: dict[str, Any]) -> dict[str, Any]:
    """
    Execute an edit from parsed arguments.

    Supports both search/replace edits and whole-file writes.
    """
    filepath = args.get("filepath", "")

    # Whole-file write: 'content' present, no 'search'.
    if "content" in args and "search" not in args:
        return _do_write(vfs, filepath, args.get("content", ""))

    # Standard search/replace edit.
    return _do_edit(vfs, filepath, args.get("search", ""), args.get("replace", ""))


def execute_edit(vfs: "VFS", edit: EditBlock) -> dict[str, Any]:
    """Execute a single edit block (legacy interface)."""
    return _do_edit(vfs, edit.file, edit.search, edit.replace)


def _do_edit(vfs: "VFS", filepath: str, search: str, replace: str) -> dict[str, Any]:
    """Core edit logic shared by execute() and execute_edit()."""
    if not filepath or not isinstance(filepath, str):
        return {"success": False, "error": "filepath must be a non-empty string"}

    try:
        content = vfs.read_file(filepath)
    except FileNotFoundError:
        return {"success": False, "error": f"File not found: {filepath}"}

    if search not in content:
        best_match, distance, pos = _find_best_match(search, content)

        max_len = max(len(search), len(best_match))
        similarity = ((max_len - distance) / max_len * 100) if max_len > 0 else 0

        diff = _generate_diff(search, best_match)
        line_num = content[:pos].count("\n") + 1

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

    new_content = content.replace(search, replace, 1)
    vfs.write_file(filepath, new_content)

    return {
        "success": True,
        "message": f"Replaced in {filepath}",
        "modified_files": [filepath],
        "side_effects": [SideEffect.FILES_MODIFIED],
    }


def _do_write(vfs: "VFS", filepath: str, content: str) -> dict[str, Any]:
    """Write complete file content (create or overwrite)."""
    if not filepath or not isinstance(filepath, str):
        return {"success": False, "error": "filepath must be a non-empty string"}

    is_new = not vfs.file_exists(filepath)

    vfs.write_file(filepath, content)

    side_effects = [SideEffect.FILES_MODIFIED]
    result: dict[str, Any] = {
        "success": True,
        "message": f"Wrote {len(content)} bytes to {filepath}",
        "modified_files": [filepath],
        "side_effects": side_effects,
    }

    if is_new:
        result["new_files"] = [filepath]
        side_effects.append(SideEffect.NEW_FILES_CREATED)

    return result


def execute_edits(vfs: "VFS", edits: list[EditBlock]) -> tuple[list[dict[str, Any]], int | None]:
    """
    Execute a list of edit blocks sequentially. Stops on first failure.

    Returns (results, failed_index) where failed_index is None if all succeeded.
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