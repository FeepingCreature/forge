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


def _find_best_match(search: str, content: str, max_candidates: int = 50) -> tuple[str, int, int]:
    """
    Find the substring in content most similar to search.
    
    Returns (best_match, distance, start_position)
    """
    search_len = len(search)
    if search_len == 0:
        return ("", 0, 0)
    
    # Try different window sizes around the search length
    best_match = ""
    best_distance = float('inf')
    best_pos = 0
    
    # Look at windows of similar size to search text
    for window_delta in range(-min(20, search_len // 2), min(50, search_len)):
        window_size = search_len + window_delta
        if window_size <= 0 or window_size > len(content):
            continue
        
        # Sample positions throughout the file to find candidates
        step = max(1, (len(content) - window_size) // max_candidates)
        
        for pos in range(0, len(content) - window_size + 1, step):
            candidate = content[pos:pos + window_size]
            
            # Quick filter: if first/last chars don't match and candidate is way different, skip
            # This is a heuristic to speed things up
            if abs(len(candidate) - search_len) > search_len // 2:
                continue
            
            distance = _levenshtein_distance(search, candidate)
            
            if distance < best_distance:
                best_distance = distance
                best_match = candidate
                best_pos = pos
    
    # Also try to find matches starting at each line
    lines_start = 0
    for line in content.split('\n'):
        if len(line) > 0:
            # Try matching from line starts with similar length
            for end_offset in range(-10, 50):
                end_pos = lines_start + search_len + end_offset
                if end_pos <= lines_start or end_pos > len(content):
                    continue
                candidate = content[lines_start:end_pos]
                distance = _levenshtein_distance(search, candidate)
                
                if distance < best_distance:
                    best_distance = distance
                    best_match = candidate
                    best_pos = lines_start
        
        lines_start += len(line) + 1  # +1 for newline
    
    return (best_match, int(best_distance), best_pos)


def _generate_diff(expected: str, actual: str) -> str:
    """Generate a unified diff between expected and actual text."""
    expected_lines = expected.splitlines(keepends=True)
    actual_lines = actual.splitlines(keepends=True)
    
    diff = difflib.unified_diff(
        expected_lines,
        actual_lines,
        fromfile='SEARCH (expected)',
        tofile='FOUND (actual)',
        lineterm=''
    )
    
    return ''.join(diff)


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
        # Find the closest matching text to help diagnose the issue
        best_match, distance, pos = _find_best_match(search, content)
        
        # Calculate similarity percentage
        max_len = max(len(search), len(best_match))
        similarity = ((max_len - distance) / max_len * 100) if max_len > 0 else 0
        
        # Generate a diff to show what's different
        diff = _generate_diff(search, best_match)
        
        # Find the line number of the best match
        line_num = content[:pos].count('\n') + 1
        
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
