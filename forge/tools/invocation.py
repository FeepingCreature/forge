"""
Tool invocation modes - inline XML vs API function calls.

Tools declare their preferred invocation mode. Inline tools use XML syntax
embedded in assistant messages. API tools use function calling.

Inline tools (no context change on success):
- edit, write_file, delete_file, rename_file
- commit, check, run_tests
- think

API tools (change context or need complex interaction):
- update_context, grep_open, grep_context
- scout, compact, get_lines, get_skill
- undo_edit
"""

from enum import Enum
from typing import Any


class InvocationMode(Enum):
    """How a tool is invoked by the AI."""

    INLINE = "inline"  # <tool_name>...</tool_name> in message text
    API = "api"  # Function call via tool_calls


def get_invocation_mode(schema: dict[str, Any]) -> InvocationMode:
    """
    Get the invocation mode from a tool schema.

    Tools declare their mode via "invocation" key at the top level.
    Defaults to API for backwards compatibility.
    """
    mode_str = schema.get("invocation", "api")
    if mode_str == "inline":
        return InvocationMode.INLINE
    return InvocationMode.API


def get_inline_syntax(schema: dict[str, Any]) -> str | None:
    """
    Get the inline XML syntax documentation for a tool.

    Tools can provide custom syntax via "inline_syntax" key.
    Returns None for API-only tools.
    """
    if get_invocation_mode(schema) != InvocationMode.INLINE:
        return None

    # Check for custom syntax documentation
    if "inline_syntax" in schema:
        return schema["inline_syntax"]

    # Generate default syntax from schema
    func = schema.get("function", {})
    name = func.get("name", "unknown")
    params = func.get("parameters", {}).get("properties", {})
    required = func.get("parameters", {}).get("required", [])

    # Simple case: no params or just one simple param
    if not params:
        return f"<{name}/>"

    # Build attribute string for simple params
    attrs = []
    for param_name, param_info in params.items():
        param_type = param_info.get("type", "string")
        if param_type in ("string", "boolean", "integer", "number"):
            if param_name in required:
                attrs.append(f'{param_name}="..."')
            else:
                attrs.append(f'[{param_name}="..."]')

    if attrs:
        return f"<{name} {' '.join(attrs)}/>"

    return f"<{name}>...</{name}>"
