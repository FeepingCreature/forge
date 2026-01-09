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

Each inline tool exports:
- get_schema() with invocation="inline" and inline_syntax
- get_inline_pattern() returning a compiled regex
- parse_inline_match(match) returning parsed arguments dict
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.vfs.base import VFS


class InvocationMode(Enum):
    """How a tool is invoked by the AI."""

    INLINE = "inline"  # <tool_name>...</tool_name> in message text
    API = "api"  # Function call via tool_calls


@dataclass
class InlineCommand:
    """A parsed inline command from assistant message text."""

    tool_name: str
    args: dict[str, Any]
    start_pos: int
    end_pos: int


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


def discover_inline_tools(user_tools_dir: str = "./tools") -> dict[str, Any]:
    """
    Discover all inline tools from builtin and user tools directories.

    Returns dict of tool_name -> module for tools that have:
    - get_schema() returning invocation="inline"
    - get_inline_pattern() returning compiled regex
    - parse_inline_match(match) returning args dict
    """
    import importlib
    import importlib.util
    import sys
    from pathlib import Path

    inline_tools: dict[str, Any] = {}

    # Check builtin tools
    builtin_dir = Path(__file__).parent / "builtin"
    for tool_file in builtin_dir.iterdir():
        if tool_file.suffix == ".py" and tool_file.name != "__init__.py":
            tool_name = tool_file.stem
            try:
                module = importlib.import_module(f"forge.tools.builtin.{tool_name}")
                if _is_inline_tool(module):
                    inline_tools[tool_name] = module
            except Exception:
                continue

    # Check user tools
    user_dir = Path(user_tools_dir)
    if user_dir.exists():
        for tool_file in user_dir.iterdir():
            if tool_file.suffix == ".py" and tool_file.name != "__init__.py":
                tool_name = tool_file.stem
                if tool_name in inline_tools:
                    continue  # Builtin takes precedence
                try:
                    # Load user tool module
                    module_name = f"tools.{tool_name}"
                    spec = importlib.util.spec_from_file_location(module_name, tool_file)
                    if spec and spec.loader:
                        module = importlib.util.module_from_spec(spec)
                        sys.modules[module_name] = module
                        spec.loader.exec_module(module)
                        if _is_inline_tool(module):
                            inline_tools[tool_name] = module
                except Exception:
                    continue

    return inline_tools


def _is_inline_tool(module: Any) -> bool:
    """Check if a module is a valid inline tool."""
    if not hasattr(module, "get_schema"):
        return False
    schema = module.get_schema()
    if get_invocation_mode(schema) != InvocationMode.INLINE:
        return False
    if not hasattr(module, "get_inline_pattern"):
        return False
    if not hasattr(module, "parse_inline_match"):
        return False
    return True


def parse_inline_commands(content: str) -> list[InlineCommand]:
    """
    Parse all inline commands from assistant message content.

    Discovers inline tools dynamically and applies their patterns.
    Returns list of InlineCommand objects in order of appearance.
    """
    inline_tools = discover_inline_tools()
    commands: list[tuple[int, InlineCommand]] = []  # (start_pos, command) for sorting

    for tool_name, module in inline_tools.items():
        pattern = module.get_inline_pattern()
        for match in pattern.finditer(content):
            args = module.parse_inline_match(match)
            cmd = InlineCommand(
                tool_name=tool_name,
                args=args,
                start_pos=match.start(),
                end_pos=match.end(),
            )
            commands.append((match.start(), cmd))

    # Sort by position in text
    commands.sort(key=lambda x: x[0])
    return [cmd for _, cmd in commands]


def execute_inline_commands(
    vfs: "VFS", commands: list[InlineCommand]
) -> tuple[list[dict[str, Any]], int | None]:
    """
    Execute a list of inline commands sequentially.

    Stops on first failure (like tool chain behavior).

    Returns:
        (results, failed_index) where:
        - results: list of result dicts for executed commands
        - failed_index: index of first failed command, or None if all succeeded
    """
    inline_tools = discover_inline_tools()
    results: list[dict[str, Any]] = []
    failed_index: int | None = None

    for i, cmd in enumerate(commands):
        module = inline_tools.get(cmd.tool_name)
        if not module or not hasattr(module, "execute"):
            results.append({"success": False, "error": f"Tool {cmd.tool_name} not found"})
            failed_index = i
            break

        result = module.execute(vfs, cmd.args)
        results.append(result)

        if not result.get("success", True):
            failed_index = i
            break

    return results, failed_index
