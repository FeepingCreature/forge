"""
Pytest configuration for forge tests.

Provides the `session` fixture used by harness-based flow tests.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.harness import SessionTestHarness


@pytest.fixture
def session(tmp_path) -> Iterator[SessionTestHarness]:
    """Provide a fresh SessionTestHarness rooted at tmp_path.

    Asserts the ScriptedBackend is drained at teardown so under-queueing
    bugs (e.g. test queues 3 responses but pipeline only used 2) surface
    immediately.
    """
    h = SessionTestHarness(tmp_path)
    yield h
    # Only check drained if a session was actually built — tests that
    # never used the harness shouldn't blow up during teardown.
    if h._live_session is not None:
        h.backend.assert_drained()