"""
DSL compiler: terse test-friendly syntax to inline XML the parser understands.

Why a DSL? Because writing inline-command XML directly inside a Python
test means literal replace/old/new tags inside triple-quoted strings,
which is awkward to read. The DSL keeps tests skimmable.

Supported directives (everything else passes through as prose):

    @edit <path>
        old: <text...>
        new: <text...>

    @write <path>
        <full file content>

    @delete <path>

    @rename <old_path> -> <new_path>

    @run_tests
    @check
    @commit message=<text>
    @think
        <conclusion text>

Indentation rules (whitespace-significant, like YAML or Python):

  - A directive line starts with @ at any column.
  - Subsequent lines that are MORE INDENTED than the directive are part
    of the directive's body. Once we see a line that is at-or-less-
    indented than the directive (and isn't blank), the body ends.
  - Inside an @edit body, the labels old: and new: start their
    respective sections; their content runs until the next label, the
    next @ directive, or end-of-body.
  - Blank lines are kept as blank lines; they do NOT end a body.

Anything outside a directive is emitted verbatim as prose, so you can
mix narration with commands the way an LLM actually would.

Escape hatch: write inline XML directly. The compiler only consumes
lines beginning with @ (after optional leading whitespace) — raw XML
is just prose to it.
"""

from __future__ import annotations

import re
import textwrap
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _Directive:
    name: str  # e.g. "edit", "run_tests"
    head: str  # text on the directive line after the name (e.g. "a.py")
    body_lines: list[str]  # de-indented body lines (no trailing newline each)
    indent: int  # column where the @ started


def compile_dsl(text: str) -> str:
    """Compile DSL `text` into the assistant content format (prose + inline XML).

    Pure function. The output is what an LLM "would have said" — you can
    feed it straight into ScriptedBackend.queue_response(content=...) or
    pass it through parse_inline_commands directly.
    """
    text = textwrap.dedent(text).strip("\n")
    lines = text.split("\n")

    out_parts: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()

        if stripped.startswith("@"):
            directive, consumed = _parse_directive(lines, i)
            out_parts.append(_render_directive(directive))
            i += consumed
        else:
            # Plain prose line — emit verbatim.
            out_parts.append(line)
            i += 1

    # Strip leading/trailing blank lines from the joined output but keep
    # internal structure.
    return "\n".join(out_parts).strip("\n") + "\n"


# --- Parsing ---------------------------------------------------------


_DIRECTIVE_RE = re.compile(r"^(\s*)@(\w+)(.*)$")


def _parse_directive(lines: list[str], start: int) -> tuple[_Directive, int]:
    """Parse a directive at lines[start]. Returns (directive, lines_consumed)."""
    m = _DIRECTIVE_RE.match(lines[start])
    if m is None:  # pragma: no cover — caller already checked
        raise ValueError(f"not a directive: {lines[start]!r}")
    indent_str, name, head = m.groups()
    indent = len(indent_str)
    head = head.strip()

    # Collect body lines: those more indented than the directive (or blank).
    body_lines: list[str] = []
    j = start + 1
    while j < len(lines):
        candidate = lines[j]
        if candidate.strip() == "":
            body_lines.append("")
            j += 1
            continue
        # Stop if we're back at-or-less indented than the directive.
        leading = len(candidate) - len(candidate.lstrip())
        if leading <= indent:
            break
        body_lines.append(candidate)
        j += 1

    # Trim trailing blank lines from the body (they're between directives,
    # not part of this body).
    while body_lines and body_lines[-1] == "":
        body_lines.pop()

    # De-indent the body to the minimum indent of non-blank lines.
    if body_lines:
        body_lines = _dedent_block(body_lines)

    return _Directive(name=name, head=head, body_lines=body_lines, indent=indent), j - start


def _dedent_block(lines: list[str]) -> list[str]:
    """De-indent a block to its smallest non-blank indent."""
    min_indent = min(
        (len(line) - len(line.lstrip()) for line in lines if line.strip()),
        default=0,
    )
    return [line[min_indent:] if line.strip() else "" for line in lines]


def _split_labeled_sections(
    body_lines: list[str], labels: tuple[str, ...]
) -> dict[str, list[str]]:
    """Split a body into named sections introduced by `<label>:` lines.

    A label line looks like `old:` or `new:` (matching one of `labels`,
    case-sensitive, optional trailing inline content on the same line is
    appended as the first body line of that section).

    Lines before the first label are dropped. Sections continue until
    the next label or end-of-body.

    Returns a dict mapping label-without-colon to its dedented body lines.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    current_lines: list[str] = []

    label_set = set(labels)

    for line in body_lines:
        stripped = line.strip()
        matched_label: str | None = None
        for lbl in label_set:
            # A label line is one whose stripped form starts with `lbl`
            # and where the next char is space or end-of-string.
            if stripped == lbl or stripped.startswith(lbl + " ") or stripped.startswith(lbl):
                # Be strict: must be "<label>:" with optional content after.
                if stripped[: len(lbl)] == lbl and (
                    len(stripped) == len(lbl) or stripped[len(lbl)] == " "
                ):
                    matched_label = lbl
                    break

        if matched_label is not None:
            # Close out previous section.
            if current is not None:
                sections[current] = _dedent_block(current_lines)
            current = matched_label.rstrip(":")
            current_lines = []
            inline_after = stripped[len(matched_label) :].lstrip()
            if inline_after:
                current_lines.append(inline_after)
        elif current is not None:
            current_lines.append(line)
        # else: line before first label — ignored.

    if current is not None:
        sections[current] = _dedent_block(current_lines)

    return sections


# --- Rendering -------------------------------------------------------


def _render_directive(d: _Directive) -> str:
    handler = _RENDERERS.get(d.name)
    if handler is None:
        raise ValueError(
            f"unknown DSL directive: @{d.name}. "
            f"Known: {sorted(_RENDERERS)}. "
            f"For unsupported tools, write inline XML directly."
        )
    return handler(d)


_LT = "<"
_GT = ">"
_SLASH = "/"


def _open_tag(name: str, attrs: str = "") -> str:
    return f"{_LT}{name}{(' ' + attrs) if attrs else ''}{_GT}"


def _close_tag(name: str) -> str:
    return f"{_LT}{_SLASH}{name}{_GT}"


def _self_closing(name: str, attrs: str = "") -> str:
    return f"{_LT}{name}{(' ' + attrs) if attrs else ''}{_SLASH}{_GT}"


def _render_edit(d: _Directive) -> str:
    """@edit <path> with old:/new: sub-blocks → <replace>...<with/>...</replace>."""
    path = d.head.strip()
    if not path:
        raise ValueError("@edit requires a file path")

    sections = _split_labeled_sections(d.body_lines, ("old:", "new:"))
    if "old" not in sections or "new" not in sections:
        raise ValueError(
            f"@edit {path}: requires both old: and new: sub-blocks; "
            f"got {sorted(sections)}"
        )

    old_text = "\n".join(sections["old"]).rstrip("\n")
    new_text = "\n".join(sections["new"]).rstrip("\n")

    open_replace = _open_tag("replace", f'file="{path}"')
    close_replace = _close_tag("replace")
    with_sep = _self_closing("with")

    return (
        f"{open_replace}\n"
        f"{old_text}\n"
        f"{with_sep}\n"
        f"{new_text}\n"
        f"{close_replace}"
    )


def _render_write(d: _Directive) -> str:
    """@write <path> with raw body."""
    path = d.head.strip()
    if not path:
        raise ValueError("@write requires a file path")
    body = "\n".join(d.body_lines).rstrip("\n")
    open_w = _open_tag("write", f'file="{path}"')
    close_w = _close_tag("write")
    return f"{open_w}\n{body}\n{close_w}"


def _render_delete(d: _Directive) -> str:
    """@delete <path>."""
    path = d.head.strip()
    if not path:
        raise ValueError("@delete requires a file path")
    return _self_closing("delete", f'file="{path}"')


def _render_rename(d: _Directive) -> str:
    """@rename <old> -> <new>."""
    head = d.head.strip()
    if "->" not in head:
        raise ValueError(f"@rename requires `<old> -> <new>` syntax; got {head!r}")
    old, new = (p.strip() for p in head.split("->", 1))
    if not old or not new:
        raise ValueError(f"@rename: both old and new paths required; got {head!r}")
    return _self_closing("rename", f'from="{old}" to="{new}"')


def _render_run_tests(d: _Directive) -> str:
    """@run_tests with optional head attrs (passed through verbatim)."""
    attrs = d.head.strip()
    return _self_closing("run_tests", attrs)


def _render_check(d: _Directive) -> str:
    """@check."""
    attrs = d.head.strip()
    return _self_closing("check", attrs)


def _render_commit(d: _Directive) -> str:
    """@commit [message=<text>]."""
    head = d.head.strip()
    # Parse `message=...` if present. Quote it for safety.
    msg = ""
    m = re.match(r'message\s*=\s*(?:"([^"]*)"|(\S.*))$', head)
    if m:
        msg = m.group(1) if m.group(1) is not None else m.group(2)
    elif head:
        msg = head  # bare text after @commit becomes the message
    attrs = f'message="{msg}"' if msg else ""
    return _self_closing("commit", attrs)


def _render_think(d: _Directive) -> str:
    """@think with body becomes a <conclusion>...</conclusion> block.

    The think tool reads <conclusion> from the assistant text. The body
    of @think is passed verbatim as the conclusion text.
    """
    body = "\n".join(d.body_lines).rstrip("\n")
    open_c = _open_tag("conclusion")
    close_c = _close_tag("conclusion")
    return f"{open_c}\n{body}\n{close_c}"


_RENDERERS: dict[str, Callable[[_Directive], str]] = {
    "edit": _render_edit,
    "write": _render_write,
    "delete": _render_delete,
    "rename": _render_rename,
    "run_tests": _render_run_tests,
    "check": _render_check,
    "commit": _render_commit,
    "think": _render_think,
}


__all__ = ["compile_dsl"]