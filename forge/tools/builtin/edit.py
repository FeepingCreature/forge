"""
edit tool — Edit files via search/replace or whole-file write.

Inline syntax (what the LLM emits):

    Surgical edit:
        REPLACE_OPEN file="path/to/file.py" >
        exact text to find
        WITH_SEP
        replacement text
        REPLACE_CLOSE

    where REPLACE_OPEN, WITH_SEP, REPLACE_CLOSE are the literal tags
    shown in the system prompt: <replace ...>, <with/>, </replace>.

    Whole-file write (create or overwrite):
        <write file="path/to/new_file.py">
        complete file content here
        </write>

Nonced form (use when body contains the literal closing tag or separator):
    <replace_NONCE file="path/to/file.py">
    text to find
    <with_NONCE />     (self-closing separator)
    replacement text
    </replace_NONCE>

    <write_NONCE file="...">...</write_NONCE>

NONCE is any sequence of word characters (letters/digits/underscore). The same
nonce must appear on the outer tag and on the with-separator within one block.
Pick a nonce that does not appear as a literal closing tag or separator inside
the body.

Why this shape: an earlier design used paired old/new children. Models had a
strong, persistent bias toward closing the second child with the first child's
closing tag (mirroring the most recent close they'd just emitted), producing
malformed blocks. Replacing the paired children with a single self-closing
separator removes the failure mode structurally: there is only one closing tag
in the whole block.
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
#     <replace file="...">old<with/>new</replace>
# WRITE_PATTERN matches whole-file writes:
#     <write file="...">...</write>
#
# Both support an optional nonce suffix on the outer tag. The nonce is
# captured in group 1 and backreferenced (\1) into the with-separator and
# the closing tag, so the parser matches a nonced open only against its
# matching nonced separator and close.
#
# The body lookaheads ensure the "to find" half stops at the first
# <with\1/> and the "replacement" half stops at the first </replace\1>.

REPLACE_PATTERN = re.compile(
    r'<replace(_\w+|)\s+file="([^"]+)"\s*>\n?'
    r"((?:(?!<with\1/>)(?!</replace\1>).)*?)\n?"
    r"<with\1/>\n?"
    r"((?:(?!</replace\1>).)*?)\n?"
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
    """Tool schema describing the edit tool to the LLM.

    The API form takes an `edits` array so multiple surgical edits and/or
    whole-file writes can be applied in a SINGLE tool call, applied
    sequentially with stop-on-first-failure. Each entry is one of:

      - replace: {"filepath", "search", "replace"} — find exact `search`
        text and replace its first occurrence with `replace`.
      - write:   {"filepath", "content"} — create or overwrite the file.

    An entry is treated as a write when it has a "content" key (and no
    "search"); otherwise it's a replace.
    """
    return {
        "type": "function",
        "invocation": "inline",
        "inline_syntax": (
            'replace: <replace file="path">OLD<sep/>NEW</replace_close> '
            'write: <write file="path">CONTENT</write_close> '
            "(multiple blocks per message allowed)"
        ),
        "function": {
            "name": "edit",
            "description": (
                "Edit files. Pass an `edits` array to apply multiple changes in one "
                "call (applied in order, stopping at the first failure). Each entry is "
                "either a surgical replace (filepath + search + replace) or a whole-file "
                "write (filepath + content). Use replace to change part of a file; use "
                "write to create a new file or overwrite an existing one entirely."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "edits": {
                        "type": "array",
                        "description": "List of edits to apply sequentially.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "filepath": {
                                    "type": "string",
                                    "description": "Path to the file to edit.",
                                },
                                "search": {
                                    "type": "string",
                                    "description": (
                                        "Exact text to find (replace entries). Must match "
                                        "exactly including whitespace. Omit for whole-file writes."
                                    ),
                                },
                                "replace": {
                                    "type": "string",
                                    "description": "Replacement text (replace entries).",
                                },
                                "content": {
                                    "type": "string",
                                    "description": (
                                        "Full file content (write entries). Presence of this "
                                        "key marks the entry as a whole-file write."
                                    ),
                                },
                            },
                            "required": ["filepath"],
                        },
                    },
                },
                "required": ["edits"],
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
    Execute file edits against the VFS.

    Two call shapes are supported:

      - API form: {"edits": [ {entry}, ... ]} — apply multiple edits
        sequentially, stopping at the first failure. Each entry is either a
        replace (filepath + search + replace) or a write (filepath + content).

      - Single-edit form: {"filepath", "search", "replace"} or
        {"filepath", "content"} — used by the inline command dispatcher, which
        parses one block at a time.

    Returns a dict with success/error. For the multi-edit API form, an
    aggregated result is returned with the union of modified/new files.
    """
    if "edits" in args:
        return _execute_edits(vfs, args["edits"])

    return _execute_single(vfs, args)


def _execute_single(vfs: "VFS", args: dict[str, Any]) -> dict[str, Any]:
    """Apply one edit entry (replace or write) to the VFS."""
    filepath = args.get("filepath", "")
    search = args.get("search", "")
    replace = args.get("replace", "")

    if not filepath:
        return {"success": False, "error": "filepath is required"}

    # Whole-file write path: an entry with "content" (and no search) is a write.
    if not search and "content" in args:
        return execute_write(vfs, {"filepath": filepath, "content": args["content"]})

    try:
        content = vfs.read_file(filepath)
    except FileNotFoundError:
        return {"success": False, "error": f"File not found: {filepath}"}

    # Empty search with no content means ambiguous — require explicit write.
    if not search:
        return {
            "success": False,
            "error": "search text is required for replace edits; provide 'content' for a whole-file write",
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
        "modified_files": [filepath],
        "side_effects": [SideEffect.FILES_MODIFIED],
    }


def _execute_edits(vfs: "VFS", edits: Any) -> dict[str, Any]:
    """Apply a list of edit entries sequentially, stop-on-first-failure.

    Returns an aggregated result. On failure, the result carries the failing
    entry's error plus how many edits succeeded before it, and any files
    already modified are reported (the VFS changes are NOT rolled back — the
    pipeline simply stops, same semantics as the inline command pipeline).
    """
    if not isinstance(edits, list) or not edits:
        return {"success": False, "error": "edits must be a non-empty list"}

    modified: list[str] = []
    new_files: list[str] = []

    for i, entry in enumerate(edits):
        if not isinstance(entry, dict):
            return {
                "success": False,
                "error": f"edit #{i} must be an object, got {type(entry).__name__}",
                "edits_succeeded": i,
                "modified_files": modified,
            }

        result = _execute_single(vfs, entry)

        if not result.get("success"):
            error = result.get("error", "unknown error")
            out: dict[str, Any] = {
                "success": False,
                "error": f"edit #{i} failed: {error}",
                "edits_succeeded": i,
            }
            if "diff" in result:
                out["diff"] = result["diff"]
            if modified:
                out["modified_files"] = modified
                out["side_effects"] = [SideEffect.FILES_MODIFIED]
            return out

        for fp in result.get("modified_files", []):
            if fp not in modified:
                modified.append(fp)
        for fp in result.get("new_files", []):
            if fp not in new_files:
                new_files.append(fp)

    side_effects = [SideEffect.FILES_MODIFIED]
    out = {
        "success": True,
        "edits_applied": len(edits),
        "modified_files": modified,
    }
    if new_files:
        out["new_files"] = new_files
        side_effects.append(SideEffect.NEW_FILES_CREATED)
    out["side_effects"] = side_effects
    return out


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

    result: dict[str, Any] = {
        "success": True,
        "filepath": filepath,
        "created": not existed,
    }
    if existed:
        result["modified_files"] = [filepath]
        result["side_effects"] = [SideEffect.FILES_MODIFIED]
    else:
        # New file: declare both — it's "modified" for prompt-refresh purposes
        # (so the new content is sent to the LLM next turn) AND new for summary
        # generation purposes.
        result["modified_files"] = [filepath]
        result["new_files"] = [filepath]
        result["side_effects"] = [SideEffect.FILES_MODIFIED, SideEffect.NEW_FILES_CREATED]
    return result
