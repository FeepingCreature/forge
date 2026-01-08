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
    

Syntax:
    <edit file="path/to/file.py">
    <search>
    exact text to find
    </search>
    <replace>
    replacement text
    </replace>
    </edit>
"""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.vfs.base import VFS


@dataclass
class EditBlock:
    """A parsed edit block from assistant message text."""

    file: str
    search: str
    replace: str
    # Position in original text for truncation on failure
    start_pos: int
    end_pos: int


# Regex to match <edit file="...">...</edit> blocks
# Using DOTALL so . matches newlines
EDIT_PATTERN = re.compile(
    r'<edit\s+file="([^"]+)">\s*'
    r"<search>\n?(.*?)\n?</search>\s*"
    r"<replace>\n?(.*?)\n?</replace>\s*"
    r"</edit>",
    re.DOTALL,
)


def parse_edits(content: str) -> list[EditBlock]:
    """
    Parse <edit> blocks from assistant message content.

    Returns list of EditBlock objects in order of appearance.
    """
    edits = []
    for match in EDIT_PATTERN.finditer(content):
        filepath = match.group(1)
        search = match.group(2)
        replace = match.group(3)

        edits.append(
            EditBlock(
                file=filepath,
                search=search,
                replace=replace,
                start_pos=match.start(),
                end_pos=match.end(),
            )
        )

    return edits


def execute_edit(vfs: "VFS", edit: EditBlock) -> dict[str, Any]:
    """
    Execute a single edit block.

    Returns a result dict similar to search_replace tool:
    - {"success": True, "message": "..."} on success
    - {"success": False, "error": "..."} on failure
    """
    filepath = edit.file
    search = edit.search
    replace = edit.replace

    # Read current state from VFS
    try:
        content = vfs.read_file(filepath)
    except FileNotFoundError:
        return {"success": False, "error": f"File not found: {filepath}"}

    if search not in content:
        # Import the fuzzy matching logic from search_replace
        from forge.tools.builtin.search_replace import _find_best_match, _generate_diff

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
