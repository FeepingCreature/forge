"""
SEARCH/REPLACE tool for making code edits using VFS
"""

import difflib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.vfs.base import VFS


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Calculate Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            # j+1 instead of j since previous_row and current_row are one character longer
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def _find_best_match(search: str, content: str, max_candidates: int = 20) -> tuple[str, int, int]:
    """
    Find the substring in content most similar to search.

    Returns (best_match, distance, start_position)
    """
    search_len = len(search)
    if search_len == 0:
        return ("", 0, 0)

    print(f"🔍 _find_best_match: search_len={search_len}, content_len={len(content)}")

    # Cap search length for Levenshtein - too expensive for large strings
    MAX_SEARCH_LEN = 500
    if search_len > MAX_SEARCH_LEN:
        print(
            f"   ⚠️ Search text too long ({search_len} > {MAX_SEARCH_LEN}), truncating for similarity search"
        )
        search = search[:MAX_SEARCH_LEN]
        search_len = MAX_SEARCH_LEN

    best_match = ""
    best_distance = float("inf")
    best_pos = 0
    calc_count = 0
    MAX_CALCULATIONS = 100  # Hard cap on Levenshtein calculations

    # Use difflib's SequenceMatcher for quick candidate finding (much faster than Levenshtein)
    # Find lines that share common substrings with the search
    search_lines = search.split("\n")
    content_lines = content.split("\n")

    print(f"   Search has {len(search_lines)} lines, content has {len(content_lines)} lines")

    # Strategy 1: Find lines with matching first non-whitespace content
    first_search_line = search_lines[0].strip() if search_lines else ""
    candidate_line_nums = []

    for i, line in enumerate(content_lines):
        if first_search_line and first_search_line[:20] in line:
            candidate_line_nums.append(i)

    print(f"   Found {len(candidate_line_nums)} candidate lines matching start")

    # Strategy 2: If no matches, sample evenly through file
    if not candidate_line_nums:
        step = max(1, len(content_lines) // max_candidates)
        candidate_line_nums = list(range(0, len(content_lines), step))
        print(f"   No prefix matches, sampling {len(candidate_line_nums)} positions")

    # Limit candidates
    candidate_line_nums = candidate_line_nums[:max_candidates]

    # Convert line numbers to character positions
    line_starts = [0]
    pos = 0
    for line in content_lines[:-1]:
        pos += len(line) + 1
        line_starts.append(pos)

    # Check each candidate position
    for line_num in candidate_line_nums:
        if calc_count >= MAX_CALCULATIONS:
            print(f"   ⚠️ Hit max calculations ({MAX_CALCULATIONS}), stopping search")
            break

        if line_num >= len(line_starts):
            continue

        start_pos = line_starts[line_num]

        # Try a few window sizes around search length
        for size_delta in [-10, 0, 10, 30]:
            if calc_count >= MAX_CALCULATIONS:
                break

            end_pos = start_pos + search_len + size_delta
            if end_pos <= start_pos or end_pos > len(content):
                continue

            candidate = content[start_pos:end_pos]

            # Skip if candidate is way too short
            if len(candidate) < search_len // 2:
                continue

            calc_count += 1
            distance = _levenshtein_distance(search, candidate)

            if distance < best_distance:
                best_distance = distance
                best_match = candidate
                best_pos = start_pos
                print(f"   New best: distance={distance}, pos={start_pos}, line={line_num + 1}")

                # Early exit if we found a very close match
                if distance < search_len * 0.1:  # Less than 10% edits needed
                    print("   Found close match (<10% edits), stopping early")
                    return (best_match, int(best_distance), best_pos)

    print(f"   Done: {calc_count} Levenshtein calculations, best_distance={best_distance}")

    return (best_match, int(best_distance), best_pos)


def _generate_diff(expected: str, actual: str) -> str:
    """Generate a unified diff between expected and actual text."""
    expected_lines = expected.splitlines(keepends=True)
    actual_lines = actual.splitlines(keepends=True)

    diff = difflib.unified_diff(
        expected_lines,
        actual_lines,
        fromfile="SEARCH (expected)",
        tofile="FOUND (actual)",
        lineterm="",
    )

    return "".join(diff)


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "function": {
            "name": "search_replace",
            "description": "Make a SEARCH/REPLACE edit to a file. Works on VFS (git + pending changes).",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Path to the file to edit"},
                    "search": {"type": "string", "description": "Exact text to search for"},
                    "replace": {"type": "string", "description": "Text to replace with"},
                },
                "required": ["filepath", "search", "replace"],
            },
        },
    }


def execute(vfs: "VFS", args: dict[str, Any]) -> dict[str, Any]:
    """Execute the search/replace operation using VFS"""
    filepath = args.get("filepath")
    search = args.get("search")
    replace = args.get("replace")

    # Type check arguments
    if not isinstance(filepath, str) or not isinstance(search, str) or not isinstance(replace, str):
        return {"success": False, "error": "Missing required arguments"}

    # Read current state from VFS (includes pending changes from previous tools)
    try:
        content = vfs.read_file(filepath)
    except FileNotFoundError:
        return {"success": False, "error": f"File not found: {filepath}"}

    if search not in content:
        print(f"❌ Search text not found in {filepath}")
        print(f"   Search length: {len(search)} chars, {search.count(chr(10))} lines")
        print(f"   Content length: {len(content)} chars")
        print(f"   First 100 chars of search: {repr(search[:100])}")

        # Find the closest matching text to help diagnose the issue
        best_match, distance, pos = _find_best_match(search, content)

        # Calculate similarity percentage
        max_len = max(len(search), len(best_match))
        similarity = ((max_len - distance) / max_len * 100) if max_len > 0 else 0

        # Generate a diff to show what's different
        diff = _generate_diff(search, best_match)

        # Find the line number of the best match
        line_num = content[:pos].count("\n") + 1

        error_msg = (
            f"Search text not found in file.\n\n"
            f"Most similar text found (line {line_num}, {similarity:.1f}% similar, "
            f"edit distance {distance}):\n\n"
            f"```\n{best_match}\n```\n\n"
            f"Diff (your search vs actual file content):\n"
            f"```diff\n{diff}\n```"
        )

        return {"success": False, "error": error_msg}

    # Replace first occurrence
    new_content = content.replace(search, replace, 1)

    # Write back to VFS
    vfs.write_file(filepath, new_content)

    return {"success": True, "message": f"Replaced in {filepath}"}
