"""
Centralized constants for Forge.

This module contains hardcoded strings and magic numbers that are used
across the codebase. Centralizing them here makes them easier to find
and modify.
"""

# Branch naming
SESSION_BRANCH_PREFIX = "forge/session/"

# File paths within .forge/
SESSION_FILE = ".forge/session.json"
APPROVED_TOOLS_FILE = ".forge/approved_tools.json"

# Default models
DEFAULT_MODEL = "anthropic/claude-sonnet-4-20250514"
DEFAULT_SUMMARIZATION_MODEL = "anthropic/claude-3-haiku"
