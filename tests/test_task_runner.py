"""
Tests for forge.runtime.tasks (TaskRunner seam).

SyncTaskRunner is tested exhaustively — it's the test substitute, so it
must behave correctly under all the patterns production code uses
(result-only work, work that emits events, work that fails, work that
gets cancelled).

QtTaskRunner gets a smoke test using pytest-qt's `qtbot` fixture to
confirm the Qt plumbing works end-to-end without us hand-rolling event
loop spins.
"""

from __future__ import annotations

from typing import Any

import pytest

from forge.runtime.tasks import (
    CancelledError,
    CancelToken,
    QtTaskRunner,
    SyncTaskRunner,
    TaskRunner,
)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_sync_runner_satisfies_protocol() -> None:
    runner: TaskRunner = SyncTaskRunner()
    assert isinstance(runner, TaskRunner)


def test_qt_runner_satisfies_protocol(qtbot: Any) -> None:
    runner: TaskRunner = QtTaskRunner()
    assert isinstance(runner, TaskRunner)


# ---------------------------------------------------------------------------
# SyncTaskRunner: result-only work
# ---------------------------------------------------------------------------


class _Capture:
    """Test helper to record callbacks."""

    def __init__(self) -> None:
        self.events: list[Any] = []
        self.result: Any = _UNSET
        self.error: str | None = None

    def on_event(self, e: Any) -> None:
        self.events.append(e)

    def on_result(self, r: Any) -> None:
        self.result = r

    def on_error(self, msg: str) -> None:
        self.error = msg


_UNSET = object()


def test_sync_result_only_work_delivers_result() -> None:
    runner = SyncTaskRunner()
    cap = _Capture()

    runner.submit(
        work=lambda emit, token: 42,
        on_result=cap.on_result,
        on_error=cap.on_error,
    )

    assert cap.result == 42
    assert cap.error is None
    assert cap.events == []


def test_sync_work_emits_events_in_order() -> None:
    runner = SyncTaskRunner()
    cap = _Capture()

    def work(emit: Any, token: CancelToken) -> str:
        emit("a")
        emit("b")
        emit("c")
        return "done"

    runner.submit(work, on_result=cap.on_result, on_error=cap.on_error, on_event=cap.on_event)

    assert cap.events == ["a", "b", "c"]
    assert cap.result == "done"


def test_sync_events_dropped_if_no_handler_provided() -> None:
    """Work that emits events must not crash when on_event is None."""
    runner = SyncTaskRunner()
    cap = _Capture()

    def work(emit: Any, token: CancelToken) -> int:
        emit("ignored")
        emit("also ignored")
        return 1

    runner.submit(work, on_result=cap.on_result, on_error=cap.on_error)

    assert cap.result == 1


# ---------------------------------------------------------------------------
# SyncTaskRunner: errors
# ---------------------------------------------------------------------------


def test_sync_exception_routed_to_on_error() -> None:
    runner = SyncTaskRunner()
    cap = _Capture()

    def boom(emit: Any, token: CancelToken) -> None:
        raise RuntimeError("kaboom")

    runner.submit(boom, on_result=cap.on_result, on_error=cap.on_error)

    assert cap.result is _UNSET
    assert cap.error is not None
    assert "kaboom" in cap.error


def test_sync_exception_after_events_still_calls_on_error() -> None:
    """Work can emit events before failing; events arrive, then on_error."""
    runner = SyncTaskRunner()
    cap = _Capture()

    def work(emit: Any, token: CancelToken) -> None:
        emit("first")
        emit("second")
        raise ValueError("nope")

    runner.submit(work, on_result=cap.on_result, on_error=cap.on_error, on_event=cap.on_event)

    assert cap.events == ["first", "second"]
    assert cap.result is _UNSET
    assert cap.error is not None and "nope" in cap.error


# ---------------------------------------------------------------------------
# SyncTaskRunner: cancellation
# ---------------------------------------------------------------------------


# NOTE on cancellation in sync mode:
# SyncTaskRunner runs work *during* submit(), so the handle isn't
# available until work has already finished. Mid-flight cancellation
# is therefore only meaningfully testable for QtTaskRunner (see below).
# In sync mode we test only the after-completion and CancelledError
# code paths.


def test_sync_cancel_after_completion_is_safe() -> None:
    """Calling cancel_all after work has finished is a no-op."""
    runner = SyncTaskRunner()
    cap = _Capture()

    runner.submit(lambda emit, token: 7, on_result=cap.on_result, on_error=cap.on_error)
    assert cap.result == 7

    runner.cancel_all()  # no exception
    runner.shutdown()


def test_sync_raise_if_stopped_treated_as_silent_cancel() -> None:
    """Work that uses CancelToken.raise_if_stopped() and is cancelled
    *before* submit() returns... isn't possible in sync mode, but we
    can simulate the same code path by raising CancelledError directly.
    """
    runner = SyncTaskRunner()
    cap = _Capture()

    def work(emit: Any, token: CancelToken) -> None:
        raise CancelledError()

    runner.submit(work, on_result=cap.on_result, on_error=cap.on_error)

    # CancelledError should be swallowed silently, not routed to on_error.
    assert cap.result is _UNSET
    assert cap.error is None


# ---------------------------------------------------------------------------
# CancelToken
# ---------------------------------------------------------------------------


def test_cancel_token_initial_state() -> None:
    token = CancelToken()
    assert token.stop_requested is False


def test_cancel_token_request_stop_flips_flag() -> None:
    token = CancelToken()
    token.request_stop()
    assert token.stop_requested is True


def test_cancel_token_raise_if_stopped() -> None:
    token = CancelToken()
    token.raise_if_stopped()  # no-op when not stopped
    token.request_stop()
    with pytest.raises(CancelledError):
        token.raise_if_stopped()


# ---------------------------------------------------------------------------
# QtTaskRunner — smoke tests via pytest-qt
# ---------------------------------------------------------------------------
#
# We use pytest-qt's `qtbot` fixture instead of hand-rolling event loops.
# qtbot.waitUntil(predicate, timeout=ms) spins the event loop correctly
# without the Qt-internal abort we hit when constructing QCoreApplication
# manually inside a test that's running under pytest-qt's QApplication.


def test_qt_runner_delivers_result(qtbot: Any) -> None:
    runner = QtTaskRunner()
    cap = _Capture()

    runner.submit(
        work=lambda emit, token: "hello",
        on_result=cap.on_result,
        on_error=cap.on_error,
    )

    qtbot.waitUntil(lambda: cap.result is not _UNSET, timeout=2000)
    assert cap.result == "hello"
    assert cap.error is None
    runner.shutdown()


def test_qt_runner_delivers_events_then_result(qtbot: Any) -> None:
    runner = QtTaskRunner()
    cap = _Capture()

    def work(emit: Any, token: CancelToken) -> str:
        emit("chunk-1")
        emit("chunk-2")
        emit("chunk-3")
        return "final"

    runner.submit(work, on_result=cap.on_result, on_error=cap.on_error, on_event=cap.on_event)

    qtbot.waitUntil(lambda: cap.result is not _UNSET, timeout=2000)
    assert cap.events == ["chunk-1", "chunk-2", "chunk-3"]
    assert cap.result == "final"
    runner.shutdown()


def test_qt_runner_routes_exceptions_to_on_error(qtbot: Any) -> None:
    runner = QtTaskRunner()
    cap = _Capture()

    def boom(emit: Any, token: CancelToken) -> None:
        raise RuntimeError("explode")

    runner.submit(boom, on_result=cap.on_result, on_error=cap.on_error)

    qtbot.waitUntil(lambda: cap.error is not None, timeout=2000)
    assert cap.result is _UNSET
    assert "explode" in (cap.error or "")
    runner.shutdown()


def test_qt_runner_cooperative_cancel_during_long_work(qtbot: Any) -> None:
    """Real test of cooperative cancellation — only meaningful in Qt mode
    where work runs on a background thread and the handle is available
    while work is still running.
    """
    import time

    runner = QtTaskRunner()
    cap = _Capture()

    def work(emit: Any, token: CancelToken) -> str:
        for _ in range(50):
            if token.stop_requested:
                return "cancelled"
            time.sleep(0.01)
        return "completed"

    handle = runner.submit(work, on_result=cap.on_result, on_error=cap.on_error)

    # Let work start, then cancel.
    qtbot.wait(50)
    handle.request_stop()

    # Wait long enough for work to notice and finish; result should be
    # suppressed because the handle was cancelled.
    qtbot.wait(500)
    assert cap.result is _UNSET, f"expected no result delivered, got {cap.result!r}"
    runner.shutdown()


def test_qt_runner_shutdown_waits_for_completion(qtbot: Any) -> None:
    """shutdown(wait=True) should not return until threads have stopped."""
    import time

    runner = QtTaskRunner()
    cap = _Capture()

    def work(emit: Any, token: CancelToken) -> str:
        for _ in range(20):
            if token.stop_requested:
                return "early"
            time.sleep(0.01)
        return "full"

    runner.submit(work, on_result=cap.on_result, on_error=cap.on_error)
    qtbot.wait(50)
    runner.shutdown(wait=True)
    # If we get here, shutdown returned; threads should be torn down.
    # No assertion needed — the test passing means shutdown didn't hang.