"""
Commit type system for smart amending
"""

from enum import Enum


class CommitType(Enum):
    """Types of commits for smart amending logic"""

    PREPARE = "prepare"  # Pre-work commits that merge with next major commit
    FOLLOW_UP = "follow_up"  # Post-work commits that amend previous major commit
    MAJOR = "major"  # Standalone commits


def parse_commit_type(message: str) -> tuple[CommitType, str]:
    """
    Parse commit type from message prefix.

    Returns:
        (commit_type, clean_message) tuple
    """
    message = message.strip()

    if message.startswith("[prepare]"):
        return CommitType.PREPARE, message[9:].strip()
    elif message.startswith("[follow-up]"):
        return CommitType.FOLLOW_UP, message[11:].strip()
    else:
        return CommitType.MAJOR, message


def format_commit_message(commit_type: CommitType, message: str) -> str:
    """Format commit message with type prefix"""
    if commit_type == CommitType.PREPARE:
        return f"[prepare] {message}"
    elif commit_type == CommitType.FOLLOW_UP:
        return f"[follow-up] {message}"
    else:
        return message
