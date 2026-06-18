"""
Small, dependency-free text helpers.

This module is intentionally tiny and self-contained — it exists mostly as a
clean place to hang a few pure-function string utilities that several parts of
Forge reach for (rendering, summaries, prompt construction). Everything here is
pure: no I/O, no global state, no side effects. That keeps the functions trivial
to test and safe to call from any thread.

The helpers favour predictability over cleverness. They never raise on ordinary
input (empty strings, None-ish values coerced by the caller, already-short
text); instead they return a sensible, boring result. When in doubt, they leave
the text untouched rather than mangling it.
"""

from __future__ import annotations

__all__ = [
    "truncate_middle",
    "normalize_whitespace",
    "indent_block",
    "strip_trailing_blank_lines",
    "pluralize",
]


def truncate_middle(text: str, max_len: int, ellipsis: str = "\u2026") -> str:
    """Shorten *text* to at most *max_len* characters, cutting from the middle.

    Keeping both ends is usually more informative than a trailing cut: for a
    file path or an identifier you typically want to see the start *and* the
    end. The removed middle is replaced by *ellipsis*.

    If *text* already fits, it is returned unchanged. If *max_len* is too small
    to hold even the ellipsis, the text is hard-cut from the front.
    """
    if max_len <= 0:
        return ""
    if len(text) <= max_len:
        return text
    if max_len <= len(ellipsis):
        return text[:max_len]

    keep = max_len - len(ellipsis)
    head = (keep + 1) // 2
    tail = keep - head
    if tail == 0:
        return text[:head] + ellipsis
    return text[:head] + ellipsis + text[-tail:]


def normalize_whitespace(text: str) -> str:
    """Collapse runs of whitespace to single spaces and strip the ends.

    Newlines, tabs and repeated spaces all become a single space. This is handy
    for turning multi-line content into a one-line label or summary.
    """
    return " ".join(text.split())


def indent_block(text: str, prefix: str = "    ") -> str:
    """Prefix every non-empty line of *text* with *prefix*.

    Blank lines are left blank (no trailing prefix), which keeps diffs clean and
    avoids introducing trailing whitespace.
    """
    lines = text.split("\n")
    return "\n".join(prefix + line if line.strip() else line for line in lines)


def strip_trailing_blank_lines(text: str) -> str:
    """Remove blank lines at the end of *text*, preserving a single newline.

    The result has no trailing blank lines. If the input was entirely blank,
    the empty string is returned.
    """
    lines = text.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def pluralize(count: int, singular: str, plural: str | None = None) -> str:
    """Return ``"<count> <word>"`` with the word pluralized when needed.

    With no explicit *plural*, an ``"s"`` is appended to *singular*. Negative
    counts are treated like any other non-one count (e.g. ``-1`` -> plural).
    """
    word = singular if count == 1 else (plural if plural is not None else singular + "s")
    return f"{count} {word}"
