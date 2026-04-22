"""
edit tool — Edit files via search/replace or whole-file write.

Inline syntax (what the LLM emits):

    Surgical edit:
        <replace file="path/to/file.py">
        <old>
        exact text to find
        </old>
        <new>
        replacement text
        </new>
        </replace>

    Whole-file write (create or overwrite):
        <write file="path/to/new_file.py">
        complete file content here
        </write>

Nonced form (use when body contains literal </replace>, </old>, </new>, </write>):
    <replace_NONCE file="path/to/file.py">
    <old_NONCE>...</old_NONCE>
    <new_NONCE>...</new_NONCE>
    </replace_NONCE>

    <write_NONCE file="...">...</write_NONCE>

NONCE is any sequence of word characters (letters/digits/underscore). The same
nonce must appear on the outer tag and all inner tags within one block. Pick a
nonce that does not appear as a literal closing tag inside the body.
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
            best_pos = i

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


# Regex patterns for the inline XML syntax.
#
# REPLACE_PATTERN matches surgical edits:
#     <replace file="..."><old>...</old><new>...</new></replace>
# WRITE_PATTERN matches whole-file writes:
#     <write file="...">...</write>
#
# Both support an optional nonce suffix on every tag. The nonce is captured
# in group 1 and backreferenced (\1) into every closing tag, so the parser
# matches `<replace_x9k>...</replace_x9k>` but not `<replace_x9k>...</replace>`.
#
# The body lookahead in REPLACE_PATTERN uses `</replace\1>` which is unique
# enough that body content rarely collides — but the nonce-collision rule
# from the system prompt still applies.

REPLACE_PATTERN = re.compile(
    r'<replace(_\w+|)\s+file="([^"]+)"\s*>\s*'
    r"<old\1>\n?((?:(?!</replace\1>).)*?)\n?</old\1>\s*"
    r"<new\1>\n?((?:(?!</replace\1>).)*?)\n?</new\1>\s*"
    r"</replace\1>",
    re.DOTALL,
)

WRITE_PATTERN = re.compile(
    r'<write(_\w+|)\s+file="([^"]+)"\s*>\n?'
    r"((?:(?!</write\1>).)*?)"
    r"\n?</write\1>",
    re.DOTALL,
)

# Detector regexes that match the *opening* of a block — used to surface
# parse errors when an open tag appears but no full block matched.
REPLACE_OPEN_DETECTOR = re.compile(r'<replace(?:_\w+)?\s+file="[^"]+"\s*>')
WRITE_OPEN_DETECTOR = re.compile(r'<write(?:_\w+)?\s+file="[^"]+"\s*>')


def get_inline_pattern() -> re.Pattern[str]:
    """Return compiled regex for inline invocation (search/replace).

    Matches both plain <replace> and nonced <replace_NONCE> forms.
    """
    return REPLACE_PATTERN


def get_write_pattern() -> re.Pattern[str]:
    """Return compiled regex for whole-file write invocation.

    Matches both plain <write> and nonced <write_NONCE> forms.
    """
    return WRITE_PATTERN


def parse_inline_match(match: re.Match[str]) -> dict[str, Any]:
    """Parse a <replace> match into tool arguments.

    Group 1 is the suffix ('' or '_NONCE'); groups 2-4 are file/old/new.
    """
    return {
        "filepath": match.group(2),
        "search": match.group(3),
        "replace": match.group(4),
    }


def parse_write_match(match: re.Match[str]) -> dict[str, Any]:
    """Parse a <write> match into tool arguments."""
    return {
        "filepath": match.group(2),
        "content": match.group(3),
    }


def get_schema() -> dict[str, Any]:
    """Tool schema describing the edit tool to the LLM."""
    return {
        "type": "function",
        "invocation": "inline",
        "inline_syntax": '<replace file="path"><old>old text</old><new>new text</new></replace>',
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
    Parse <replace> blocks from assistant message content.

    Returns list of EditBlock objects in order of appearance. Whole-file writes
    are handled separately via WRITE_PATTERN in the inline command dispatcher.
    """
    return [
        EditBlock(
            file=match.group(2),
            search=match.group(3),
            replace=match.group(4),
            start_pos=match.start(),
            end_pos=match.end(),
        )
        for match in REPLACE_PATTERN.finditer(content)
    ]


def detect_unparsed_edit_blocks(
    content: str, parsed_spans: list[tuple[int, int]]
) -> list[tuple[int, str]]:
    """
    Find <replace ...> or <write ...> opening tags that did NOT get parsed
    as complete blocks.

    `parsed_spans` is the list of (start, end) ranges already claimed by
    successfully-parsed inline commands. Any open-tag occurrence outside
    those spans is suspect — likely malformed or missing its close.

    Returns list of (position, snippet) for surfacing to the AI.
    """
    results = []
    for detector in (REPLACE_OPEN_DETECTOR, WRITE_OPEN_DETECTOR):
        for match in detector.finditer(content):
            pos = match.start()
            # Skip if this position falls inside a successfully-parsed span
            if any(start <= pos < end for start, end in parsed_spans):
                continue
            # Grab a short snippet for the error message
            snippet = content[pos : pos + 200].replace("\n", " ")
            results.append((pos, snippet))
    return results


def execute(vfs: "VFS", args: dict[str, Any]) -> dict[str, Any]:
    """
    Execute a single search/replace edit against the VFS.

    Args:
        filepath: Path to the file to edit.
        search: Exact text to find (must match exactly, including whitespace).
        replace: Text to replace it with.

    Returns dict with success/error and optional diff.
    """
    filepath = args.get("filepath", "")
    search = args.get("search", "")
    replace = args.get("replace", "")

    if not filepath:
        return {"success": False, "error": "filepath is required"}

    # Whole-file write path — when called via <write>, the dispatcher routes
    # through execute_write below, so this branch is just for safety.
    if not search and "content" in args:
        return execute_write(vfs, {"filepath": filepath, "content": args["content"]})

    try:
        content = vfs.read_file(filepath)
    except FileNotFoundError:
        return {"success": False, "error": f"File not found: {filepath}"}

    # Empty search means create/overwrite — but require explicit <write> for that
    if not search:
        return {
            "success": False,
            "error": "search text is required for <replace> blocks; use <write> for whole-file writes",
        }

    if search not in content:
        # Try fuzzy match for diagnostics
        best_match, distance, _pos = _find_best_match(search, content)
        diff = _generate_diff(search, best_match) if best_match else ""
        return {
            "success": False,
            "error": f"Search text not found in {filepath}",
            "diff": diff,
            "fuzzy_distance": distance,
        }

    new_content = content.replace(search, replace, 1)
    vfs.write_file(filepath, new_content)

    return {
        "success": True,
        "filepath": filepath,
        "side_effects": [SideEffect.FILES_MODIFIED],
    }


def execute_write(vfs: "VFS", args: dict[str, Any]) -> dict[str, Any]:
    """
    Execute a whole-file write against the VFS.

    Args:
        filepath: Path to the file.
        content: Full file content (creates or overwrites).
    """
    filepath = args.get("filepath", "")
    content = args.get("content", "")

    if not filepath:
        return {"success": False, "error": "filepath is required"}

    existed = False
    try:
        vfs.read_file(filepath)
        existed = True
    except FileNotFoundError:
        pass

    vfs.write_file(filepath, content)

    return {
        "success": True,
        "filepath": filepath,
        "created": not existed,
        "side_effects": [SideEffect.FILES_MODIFIED if existed else SideEffect.NEW_FILES_CREATED],
    }
