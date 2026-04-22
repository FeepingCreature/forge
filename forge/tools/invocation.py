"""
Tool invocation modes - inline XML vs API function calls.

Tools declare their preferred invocation mode. Inline tools use XML syntax
embedded in assistant messages. API tools use function calling.

Inline tools (no context change on success):
- replace (search/replace surgical edits) and write (whole-file create/overwrite)
- delete_file, rename_file
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

import re  # noqa: TC003 - used at runtime in type annotations
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
        return str(schema["inline_syntax"])

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


# Module-level cache for inline tool discovery (avoids repeated filesystem scans)
_inline_tools_cache: dict[str, Any] | None = None


def discover_inline_tools(user_tools_dir: str = "./tools") -> dict[str, Any]:
    """
    Discover all inline tools from builtin and user tools directories.

    Results are cached at the module level since inline tools don't change
    during a session. Call invalidate_inline_tools_cache() if tools change.

    Returns dict of tool_name -> module for tools that have:
    - get_schema() returning invocation="inline"
    - get_inline_pattern() returning compiled regex
    - parse_inline_match(match) returning args dict
    """
    global _inline_tools_cache
    if _inline_tools_cache is not None:
        return _inline_tools_cache

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

    _inline_tools_cache = inline_tools
    return inline_tools


def invalidate_inline_tools_cache() -> None:
    """Clear the inline tools cache, forcing re-discovery on next call."""
    global _inline_tools_cache
    _inline_tools_cache = None


def _is_inline_tool(module: Any) -> bool:
    """Check if a module is a valid inline tool."""
    if not hasattr(module, "get_schema"):
        return False
    schema = module.get_schema()
    if get_invocation_mode(schema) != InvocationMode.INLINE:
        return False
    if not hasattr(module, "get_inline_pattern"):
        return False
    return hasattr(module, "parse_inline_match")


# Pattern for an inline backtick code span. CommonMark rule: a run of N
# backticks opens a span; it closes at the next run of EXACTLY N backticks.
# Backreference \1 enforces matching run length, and lookarounds prevent
# matching a sub-run inside a longer one (e.g. ``foo`` should be one span,
# not two single-tick spans around `foo`).
_INLINE_CODE_PATTERN = re.compile(
    r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)",
    re.DOTALL,
)


def _build_code_regions(content: str) -> list[tuple[int, int]]:
    """
    Find all code regions (fenced blocks and inline backtick spans) in content.

    Returns sorted list of (start, end) tuples for regions where inline
    commands should NOT be matched.

    Fenced blocks follow CommonMark rules:
      - An open fence is a line of 0-3 leading spaces, then 3+ of `\\`` or `~`,
        optionally followed by an info string.
      - The matching close is a line of 0-3 leading spaces, then >= the same
        number of the SAME character, then only whitespace to end of line.
      - Tildes never close backtick fences and vice versa.
      - Inner fences with FEWER markers don't close the outer fence.
      - An unterminated open fence protects the rest of the document.

    Inline backtick spans follow CommonMark: a run of N backticks closes at
    the next run of exactly N backticks.

    Inline spans are only collected OUTSIDE fenced blocks (so a backtick
    inside a fenced code example doesn't get treated as inline code).
    """
    fenced_spans: list[tuple[int, int]] = []

    # Pass 1: line-oriented fence scanner.
    # Walk through lines tracking byte offsets so we can record (start, end).
    lines: list[tuple[int, int, str]] = []  # (line_start, line_end_excl_nl, line_text)
    pos = 0
    while pos <= len(content):
        nl = content.find("\n", pos)
        if nl == -1:
            lines.append((pos, len(content), content[pos:]))
            break
        lines.append((pos, nl, content[pos:nl]))
        pos = nl + 1

    def _fence_info(line: str) -> tuple[str, int] | None:
        """If line is a valid fence (open OR close), return (char, count).

        Recognizes 0-3 leading spaces, then >=3 of ` or ~. Anything after the
        run is allowed (info string for opens; only whitespace is valid for
        closes — caller distinguishes).
        """
        i = 0
        # 0-3 leading spaces
        while i < len(line) and i < 3 and line[i] == " ":
            i += 1
        if i < len(line) and line[i] == " ":
            return None  # 4+ leading spaces => indented code, not a fence
        if i >= len(line):
            return None
        ch = line[i]
        if ch not in ("`", "~"):
            return None
        run_start = i
        while i < len(line) and line[i] == ch:
            i += 1
        run_len = i - run_start
        if run_len < 3:
            return None
        return (ch, run_len)

    def _is_valid_close(line: str, open_char: str, open_len: int) -> bool:
        """A close fence has same char, >= markers, and only whitespace after."""
        info = _fence_info(line)
        if info is None:
            return False
        ch, n = info
        if ch != open_char or n < open_len:
            return False
        # After the fence run, only whitespace allowed (no info string on close).
        i = 0
        while i < len(line) and i < 3 and line[i] == " ":
            i += 1
        i += n
        rest = line[i:]
        return rest.strip() == ""

    # Walk lines, finding open fences and matching closes.
    line_idx = 0
    while line_idx < len(lines):
        line_start, line_end, line_text = lines[line_idx]
        info = _fence_info(line_text)
        if info is None:
            line_idx += 1
            continue
        open_char, open_len = info

        # Search subsequent lines for the matching close.
        close_idx = None
        for j in range(line_idx + 1, len(lines)):
            if _is_valid_close(lines[j][2], open_char, open_len):
                close_idx = j
                break

        if close_idx is None:
            # Unterminated: protect from the open fence to end of content.
            fenced_spans.append((line_start, len(content)))
            break

        # Region spans from start of open line to end of close line (incl. \n).
        close_line_start, close_line_end, _ = lines[close_idx]
        # Include the trailing newline of the close line if present
        region_end = close_line_end + 1 if close_line_end < len(content) else len(content)
        fenced_spans.append((line_start, region_end))
        line_idx = close_idx + 1

    # Pass 2: inline backtick spans, only outside fenced blocks.
    def _in_fenced(p: int) -> bool:
        for s, e in fenced_spans:
            if s <= p < e:
                return True
        return False

    inline_spans: list[tuple[int, int]] = []
    for m in _INLINE_CODE_PATTERN.finditer(content):
        if _in_fenced(m.start()):
            continue
        inline_spans.append((m.start(), m.end()))

    regions = fenced_spans + inline_spans
    regions.sort()
    return regions


def _inside_code_region(pos: int, regions: list[tuple[int, int]]) -> int | None:
    """
    Check if pos falls inside any code region.

    Returns the end position of the containing region (so we can skip past it),
    or None if pos is not inside any code region.
    """
    for start, end in regions:
        if start <= pos < end:
            return end
    return None


# Pattern variants a tool module can expose. Each entry maps a
# "get_*_pattern" method name to its corresponding "parse_*_match" method
# name. The edit tool's patterns now match both plain and nonced forms in
# a single regex, so only two entries are needed.
_PATTERN_METHODS: list[tuple[str, str]] = [
    ("get_inline_pattern", "parse_inline_match"),
    ("get_write_pattern", "parse_write_match"),
]


def parse_inline_commands(content: str) -> list[InlineCommand]:
    """
    Parse all inline commands from assistant message content.

    Parses front-to-back, finding the earliest matching command at each step.
    Skips commands that appear inside code blocks (fenced ``` or inline `).

    Returns list of InlineCommand objects in order of appearance.
    """
    inline_tools = discover_inline_tools()
    code_regions = _build_code_regions(content)
    commands: list[InlineCommand] = []
    pos = 0

    while pos < len(content):
        # Find the earliest matching command from current position across
        # every (tool, pattern-variant) the tool exposes.
        earliest_match: re.Match[str] | None = None
        earliest_tool: str | None = None
        earliest_parser: Any = None

        for tool_name, module in inline_tools.items():
            for pattern_attr, parser_attr in _PATTERN_METHODS:
                if not hasattr(module, pattern_attr):
                    continue
                pattern = getattr(module, pattern_attr)()
                match = pattern.search(content, pos)
                if match is None:
                    continue
                if earliest_match is None or match.start() < earliest_match.start():
                    earliest_match = match
                    earliest_tool = tool_name
                    earliest_parser = getattr(module, parser_attr)

        if earliest_match is None or earliest_tool is None or earliest_parser is None:
            # No more commands found
            break

        # Skip commands that fall inside code blocks
        skip_to = _inside_code_region(earliest_match.start(), code_regions)
        if skip_to is not None:
            pos = skip_to
            continue

        # Parse this command
        args = earliest_parser(earliest_match)
        cmd = InlineCommand(
            tool_name=earliest_tool,
            args=args,
            start_pos=earliest_match.start(),
            end_pos=earliest_match.end(),
        )
        commands.append(cmd)

        # Continue searching AFTER this command
        pos = earliest_match.end()

    return commands


def detect_unparsed_inline_blocks(
    content: str, commands: list[InlineCommand]
) -> list[dict[str, Any]]:
    """
    Detect inline blocks that *look* like commands but failed to parse.

    Currently checks the edit tool's loose opening-tag detector. Any
    <edit ...> (or <edit_NONCE ...>) that did not result in a parsed
    command — and isn't inside a code region — is reported so the AI can
    see that its edit was silently dropped instead of executing.

    Returns a list of dicts: {"tool": "edit", "position": int, "snippet": str}.
    """
    inline_tools = discover_inline_tools()
    code_regions = _build_code_regions(content)
    parsed_spans = [(c.start_pos, c.end_pos) for c in commands]
    unparsed: list[dict[str, Any]] = []

    for tool_name, module in inline_tools.items():
        if not hasattr(module, "detect_unparsed_edit_blocks"):
            continue
        for pos, snippet in module.detect_unparsed_edit_blocks(content, parsed_spans):
            # Ignore detections inside code regions — those are documentation.
            if _inside_code_region(pos, code_regions) is not None:
                continue
            unparsed.append({"tool": tool_name, "position": pos, "snippet": snippet})

    return unparsed


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


def execute_inline_commands_with_parse_check(
    vfs: "VFS", content: str, commands: list[InlineCommand]
) -> tuple[list[dict[str, Any]], int | None]:
    """
    Like execute_inline_commands but first checks for blocks that look like
    inline commands but failed to parse. If any are found, they are reported
    as a failed result *before* executing successfully-parsed commands, so
    the AI sees that some of its work was silently dropped.
    """
    unparsed = detect_unparsed_inline_blocks(content, commands)
    if unparsed:
        snippets = "\n".join(
            f"  - at position {u['position']}: {u['snippet']!r}" for u in unparsed
        )
        error = (
            f"{len(unparsed)} inline command block(s) failed to parse and were ignored.\n"
            "This usually means a body contained </replace>, </old>, </new>, or </write>, "
            "or the closing tag was missing/mismatched.\n"
            "If a body legitimately contains those tags, use the nonced syntax: "
            '<replace_NONCE file="..."><old_NONCE>...</old_NONCE>'
            "<new_NONCE>...</new_NONCE></replace_NONCE>"
            ' or <write_NONCE file="...">...</write_NONCE>.\n\n'
            f"Unparsed blocks:\n{snippets}"
        )
        return ([{"success": False, "error": error}], 0)

    return execute_inline_commands(vfs, commands)
