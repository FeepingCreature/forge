"""
A tiny demo module written via the `edit` tool's whole-file write shape.

This exists purely to exercise streaming of a `content` entry: as the
argument streams in, the file body should type out character-by-character
in the diff view rather than popping in complete at the end.

Everything here is pure and side-effect free.
"""

from __future__ import annotations

__all__ = ["greet", "shout", "countdown"]


def greet(name: str) -> str:
    """Return a friendly greeting for *name*."""
    return f"Hello, {name}!"


def shout(text: str) -> str:
    """Return *text* upper-cased with an exclamation mark."""
    return text.upper() + "!"


def countdown(n: int) -> list[int]:
    """Return a list counting down from *n* to 1 (empty if n < 1)."""
    return list(range(n, 0, -1))
