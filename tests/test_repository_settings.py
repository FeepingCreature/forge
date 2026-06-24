"""Tests for RepositorySettingsDialog config round-tripping.

These exercise the load/save logic against an in-memory VFS without needing a
live Qt event loop beyond constructing the widget. A QApplication is required
for any QWidget, so we create one for the module.
"""

import json

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from forge.ui.repository_settings_dialog import RepositorySettingsDialog
from forge.ui.summary_exclusions_dialog import CONFIG_FILE


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _FakeVFS:
    """Minimal VFS standing in for WorkInProgressVFS."""

    def __init__(self, files: dict[str, str] | None = None) -> None:
        self._files: dict[str, str] = dict(files or {})
        self.commits: list[tuple[str, object]] = []

    def file_exists(self, path: str) -> bool:
        return path in self._files

    def read_file(self, path: str) -> str:
        return self._files[path]

    def write_file(self, path: str, content: str) -> None:
        self._files[path] = content

    def list_files(self) -> list[str]:
        return list(self._files)

    def commit(self, message: str, commit_type: object = None) -> None:
        self.commits.append((message, commit_type))


class _FakeWorkspace:
    def __init__(self, vfs: _FakeVFS) -> None:
        self.vfs = vfs


def _config(vfs: _FakeVFS) -> dict:
    return json.loads(vfs.read_file(CONFIG_FILE))


class TestTestCommandRoundTrip:
    def test_loads_existing_test_command(self, qapp):
        vfs = _FakeVFS(
            {CONFIG_FILE: json.dumps({"test_command": "pytest -q", "summary_exclusions": []})}
        )
        dialog = RepositorySettingsDialog(_FakeWorkspace(vfs))
        assert dialog.test_command_input.text() == "pytest -q"

    def test_empty_when_unset(self, qapp):
        vfs = _FakeVFS({CONFIG_FILE: json.dumps({"summary_exclusions": []})})
        dialog = RepositorySettingsDialog(_FakeWorkspace(vfs))
        assert dialog.test_command_input.text() == ""

    def test_saves_test_command(self, qapp):
        vfs = _FakeVFS({CONFIG_FILE: json.dumps({"summary_exclusions": []})})
        dialog = RepositorySettingsDialog(_FakeWorkspace(vfs))
        dialog.test_command_input.setText("  npm test  ")
        dialog._save_and_close()
        assert _config(vfs)["test_command"] == "npm test"
        assert vfs.commits  # a PREPARE commit was made

    def test_save_preserves_unmanaged_keys(self, qapp):
        """enabled_tools (managed by SettingsDialog) must survive a save here."""
        vfs = _FakeVFS(
            {
                CONFIG_FILE: json.dumps(
                    {
                        "enabled_tools": ["web_search"],
                        "summary_exclusions": ["*.min.js"],
                        "test_command": "",
                    }
                )
            }
        )
        dialog = RepositorySettingsDialog(_FakeWorkspace(vfs))
        dialog.test_command_input.setText("pytest")
        dialog._save_and_close()
        config = _config(vfs)
        assert config["enabled_tools"] == ["web_search"]
        assert config["test_command"] == "pytest"

    def test_saves_summary_exclusions(self, qapp):
        vfs = _FakeVFS({CONFIG_FILE: json.dumps({"summary_exclusions": ["*.lock"]})})
        dialog = RepositorySettingsDialog(_FakeWorkspace(vfs))
        dialog._save_and_close()
        assert _config(vfs)["summary_exclusions"] == ["*.lock"]
