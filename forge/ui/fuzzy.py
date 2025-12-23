"""
Fuzzy matching utilities for UI components.
"""


def fuzzy_match(pattern: str, text: str) -> tuple[bool, int]:
    """
    Check if pattern fuzzy-matches text.

    Returns (matched, score) where score is lower for better matches.
    Score considers:
    - Position of first match (earlier is better)
    - Gaps between matched characters (fewer gaps is better)
    - Consecutive matches (bonus)
    - Word boundary matches (bonus)
    """
    pattern = pattern.lower()
    text_lower = text.lower()

    if not pattern:
        return True, 0

    # Exact substring match gets best score
    if pattern in text_lower:
        return True, text_lower.index(pattern)

    # Fuzzy match: all pattern chars must appear in order
    pattern_idx = 0
    text_idx = 0
    score = 0
    last_match_idx = -1
    first_match_idx = -1

    while pattern_idx < len(pattern) and text_idx < len(text_lower):
        if pattern[pattern_idx] == text_lower[text_idx]:
            if first_match_idx == -1:
                first_match_idx = text_idx

            # Bonus for consecutive matches
            if last_match_idx == text_idx - 1:
                score -= 5  # Consecutive bonus
            else:
                # Penalty for gaps
                if last_match_idx >= 0:
                    score += (text_idx - last_match_idx - 1) * 2

            last_match_idx = text_idx
            pattern_idx += 1
        text_idx += 1

    if pattern_idx < len(pattern):
        # Not all pattern characters matched
        return False, 999999

    # Add penalty for late first match
    score += first_match_idx * 2

    # Bonus for matching at word boundaries (after / or _)
    if first_match_idx == 0 or (first_match_idx > 0 and text_lower[first_match_idx - 1] in "/_"):
        score -= 10

    # Bonus for shorter text (prefer exact-ish matches)
    score += len(text) // 10

    return True, score
