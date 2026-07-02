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

# Raster image extensions supported by the vision context mechanism (§1) and
# the tab image viewer (§3). Kept in one place since both forge/session/manager.py
# and forge/ui/branch_tab_widget.py need to agree on what counts as "an image".
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# Forge AI identity (used for Co-authored-by trailer and committer field)
FORGE_AUTHOR_NAME = "Forge AI (github.com/FeepingCreature/forge)"
FORGE_AUTHOR_EMAIL = "noreply@forge-ai.invalid"
CO_AUTHORED_BY_TRAILER = f"Co-authored-by: {FORGE_AUTHOR_NAME} <{FORGE_AUTHOR_EMAIL}>"
