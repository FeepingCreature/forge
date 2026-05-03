"""Tests for forge.runtime.inline_executor.run_inline_commands.

The helper is a thin wrapper around
execute_inline_commands_with_parse_check that owns the
vfs.claim_thread() / release_thread() pair. We cover:
  - the claim/release brackets always run, even on exception
  - the wrapper's return value passes through unchanged
"""

from typing import Any

import pytest

from forge.runtime import run_inline_commands


class _FakeVFS:
    """Minimal VFS surface — just claim/release counters."""

    def __init__(self) -> None:
        self.claims = 0
        self.releases = 0

    def claim_thread(self) -> None:
        self.claims += 1

    def release_thread(self) -> None:
        self.releases += 1


@pytest.fixture
def fake_vfs() -> _FakeVFS:
    return _FakeVFS()


def test_brackets_claim_and_release(monkeypatch: pytest.MonkeyPatch, fake_vfs: _FakeVFS) -> None:
    """A normal call claims once and releases once."""

    def fake_executor(vfs: Any, content: str, commands: list) -> tuple[list, int | None]:
        # Inside the call, the claim should be active.
        assert vfs.claims == 1
        assert vfs.releases == 0
        return ([], None)

    monkeypatch.setattr(
        "forge.runtime.inline_executor.execute_inline_commands_with_parse_check",
        fake_executor,
    )

    result = run_inline_commands(fake_vfs, "content", [])

    assert result == ([], None)
    assert fake_vfs.claims == 1
    assert fake_vfs.releases == 1


def test_release_runs_even_on_exception(
    monkeypatch: pytest.MonkeyPatch, fake_vfs: _FakeVFS
) -> None:
    def boom(vfs: Any, content: str, commands: list) -> tuple[list, int | None]:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(
        "forge.runtime.inline_executor.execute_inline_commands_with_parse_check",
        boom,
    )

    with pytest.raises(RuntimeError, match="kaboom"):
        run_inline_commands(fake_vfs, "content", [])

    # The finally block must have run.
    assert fake_vfs.claims == 1
    assert fake_vfs.releases == 1


def test_passes_arguments_through(
    monkeypatch: pytest.MonkeyPatch, fake_vfs: _FakeVFS
) -> None:
    captured: dict[str, Any] = {}

    def capture(vfs: Any, content: str, commands: list) -> tuple[list, int | None]:
        captured["vfs"] = vfs
        captured["content"] = content
        captured["commands"] = commands
        return ([{"success": True}], None)

    monkeypatch.setattr(
        "forge.runtime.inline_executor.execute_inline_commands_with_parse_check",
        capture,
    )

    sentinel_commands: list = ["sentinel-1", "sentinel-2"]
    result = run_inline_commands(fake_vfs, "the content", sentinel_commands)

    assert captured["vfs"] is fake_vfs
    assert captured["content"] == "the content"
    assert captured["commands"] is sentinel_commands
    assert result == ([{"success": True}], None)