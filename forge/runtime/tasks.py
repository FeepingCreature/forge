"""
TaskRunner — seam for "run work off the main thread."

Production code uses `QtTaskRunner`, which spawns a `QThread` per task
and marshals callbacks back via Qt's queued signals so they fire on the
caller's thread.

Tests use `SyncTaskRunner`, which runs the work function on the calling
thread and invokes callbacks immediately. With this swap, the entire
LiveSession pipeline (LLM call → inline commands → tools → next LLM
call) runs straight-line, no event loop required.

Design notes:
- `submit()` takes a work function that may need to emit progress events
  (chunks during streaming, per-tool started/finished). The work
  function receives an `Emitter` it can call to publish events. Events
  are routed to `on_event`. The function's return value is routed to
  `on_result`. Exceptions are caught and routed to `on_error`.
- Cancellation is cooperative. A `CancelToken` is also passed to the
  work function. When `handle.request_stop()` is called, the token's
  `.stop_requested` flag flips. Work that doesn't poll runs to
  completion, but its `on_result`/`on_event` callbacks become no-ops
  once the handle is cancelled — the result is silently dropped.
- We deliberately do *not* support hard termination. The previous
  `QThread.terminate()` path was unreliable and the source of more bugs
  than it ever fixed.
"""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from typing import Any, Protocol, TypeVar, runtime_checkable

from PySide6.QtCore import QObject, QThread, Signal, Slot

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class CancelToken:
    """Cooperative cancellation flag passed to work functions.

    Work that runs long enough to be cancellable should poll
    `token.stop_requested` at safe boundaries and return early when set.
    Work that doesn't poll just runs to completion — its result is then
    discarded by the runner.
    """

    def __init__(self) -> None:
        self._flag = threading.Event()

    @property
    def stop_requested(self) -> bool:
        return self._flag.is_set()

    def request_stop(self) -> None:
        self._flag.set()

    def raise_if_stopped(self) -> None:
        """Convenience: raise CancelledError if a stop was requested.

        Useful for work that wants exception-based early exit instead of
        polling-and-returning.
        """
        if self._flag.is_set():
            raise CancelledError()


class CancelledError(Exception):
    """Raised by `CancelToken.raise_if_stopped()` when cancel was requested."""


# Type alias for the emit callback the work function uses to publish
# mid-execution events (e.g., stream chunks, per-tool progress).
Emitter = Callable[[Any], None]


# Type alias for the work function the caller submits. It receives an
# emitter for events and a cancel token, and returns a final result.
Work = Callable[[Emitter, CancelToken], T]


class TaskHandle:
    """Handle to a submitted task. Used to cancel it."""

    def __init__(self, token: CancelToken) -> None:
        self._token = token
        self._cancelled = False
        self._lock = threading.Lock()

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def request_stop(self) -> None:
        """Request cooperative cancellation.

        Sets the token's flag (so polling work sees it) and marks the
        handle as cancelled (so any pending callbacks become no-ops).
        """
        with self._lock:
            self._cancelled = True
        self._token.request_stop()


@runtime_checkable
class TaskRunner(Protocol):
    """Seam for executing work off the calling thread.

    Production: `QtTaskRunner`. Tests: `SyncTaskRunner`.
    """

    def submit(
        self,
        work: Work[T],
        on_result: Callable[[T], None],
        on_error: Callable[[str], None],
        on_event: Callable[[Any], None] | None = None,
    ) -> TaskHandle:
        """Submit work to run.

        Args:
            work: Callable taking (emitter, cancel_token) and returning a result.
                  May call emitter(event) any number of times during execution.
            on_result: Called with the work function's return value.
            on_error: Called with a string description if work raises.
            on_event: Called for each event the work function emits.

        Returns:
            TaskHandle that can be used to cancel.
        """
        ...

    def cancel_all(self) -> None:
        """Request cancellation of all in-flight tasks.

        After this returns, no new callbacks will fire from previously
        submitted tasks. Tasks that don't poll their cancel token will
        still finish their work; their results are simply discarded.
        """
        ...

    def shutdown(self, wait: bool = True) -> None:
        """Cancel everything and tear down resources.

        Args:
            wait: If True, block until all in-flight tasks finish.
                  If False, return immediately (tasks finish in background).
        """
        ...


# ---------------------------------------------------------------------------
# SyncTaskRunner — for tests
# ---------------------------------------------------------------------------


class SyncTaskRunner:
    """TaskRunner that runs work synchronously on the calling thread.

    `submit()` runs `work` immediately, calls `on_event` for each emitted
    event, then calls `on_result` (or `on_error` on exception) — all
    before returning the handle.

    This means the entire pipeline (LLM call → inline commands → tools →
    next LLM call) collapses into a single synchronous call from the
    test's perspective. No event loop, no threading, no flakiness.

    Cancellation: if a test calls `handle.request_stop()` *during*
    work execution (e.g., from an `on_event` callback), the work
    function can poll `token.stop_requested` and exit early. After
    cancel, callbacks become no-ops.
    """

    def __init__(self) -> None:
        self._handles: list[TaskHandle] = []

    def submit(
        self,
        work: Work[T],
        on_result: Callable[[T], None],
        on_error: Callable[[str], None],
        on_event: Callable[[Any], None] | None = None,
    ) -> TaskHandle:
        token = CancelToken()
        handle = TaskHandle(token)
        self._handles.append(handle)

        def safe_emit(event: Any) -> None:
            if handle.cancelled:
                return
            if on_event is not None:
                on_event(event)

        try:
            result = work(safe_emit, token)
        except CancelledError:
            # Cooperative early exit; treat as silent cancellation.
            return handle
        except Exception as exc:
            if not handle.cancelled:
                on_error(_format_error(exc))
            return handle

        if not handle.cancelled:
            on_result(result)
        return handle

    def cancel_all(self) -> None:
        for handle in self._handles:
            handle.request_stop()

    def shutdown(self, wait: bool = True) -> None:
        self.cancel_all()
        # Sync runner has nothing to wait on — work has already finished
        # by the time submit() returned.
        self._handles.clear()


# ---------------------------------------------------------------------------
# QtTaskRunner — for production
# ---------------------------------------------------------------------------


class _QtWorker(QObject):
    """Internal QObject that runs the work function on a QThread.

    Signals are how we marshal callbacks from the worker thread back to
    the thread that submitted the task (typically the main/UI thread).
    Qt's queued connection mode handles the cross-thread delivery
    automatically when sender and receiver live on different threads.
    """

    # Emitted from worker thread; received on the submitter's thread.
    event = Signal(object)
    finished = Signal(object)
    failed = Signal(str)
    done = Signal()  # Always emitted last, used for thread cleanup.

    def __init__(self, work: Work[Any], token: CancelToken) -> None:
        super().__init__()
        self._work = work
        self._token = token

    @Slot()
    def run(self) -> None:
        try:
            result = self._work(self.event.emit, self._token)
        except CancelledError:
            # Cooperative cancellation — silent, no callback.
            self.done.emit()
            return
        except Exception as exc:
            self.failed.emit(_format_error(exc))
            self.done.emit()
            return

        self.finished.emit(result)
        self.done.emit()


class QtTaskRunner(QObject):
    """TaskRunner that runs work on background QThreads.

    Each `submit()` call creates a new `QThread` and a `_QtWorker` that
    runs `work` on it. The worker's signals deliver events, results, and
    errors back to the calling thread via Qt's queued connections.

    Threads are tracked and torn down when work finishes (or when
    `shutdown()` is called).
    """

    def __init__(self) -> None:
        super().__init__()
        self._threads: list[tuple[QThread, _QtWorker, TaskHandle]] = []
        self._lock = threading.Lock()

    def submit(
        self,
        work: Work[T],
        on_result: Callable[[T], None],
        on_error: Callable[[str], None],
        on_event: Callable[[Any], None] | None = None,
    ) -> TaskHandle:
        token = CancelToken()
        handle = TaskHandle(token)

        thread = QThread()
        worker = _QtWorker(work, token)
        worker.moveToThread(thread)

        # Wrap callbacks so they no-op once the handle is cancelled.
        def safe_event(ev: object) -> None:
            if handle.cancelled or on_event is None:
                return
            on_event(ev)

        def safe_result(res: object) -> None:
            if handle.cancelled:
                return
            on_result(res)  # type: ignore[arg-type]

        def safe_error(msg: str) -> None:
            if handle.cancelled:
                return
            on_error(msg)

        worker.event.connect(safe_event)
        worker.finished.connect(safe_result)
        worker.failed.connect(safe_error)

        # Thread teardown follows the documented Qt pattern:
        #
        #   worker.done   ─► thread.quit         (ask exec() to return)
        #   thread.finished ─► worker.deleteLater (worker lives on thread,
        #                                          deleted via thread's loop
        #                                          before it actually exits)
        #   thread.finished ─► thread.deleteLater (only after the OS thread
        #                                          has fully finished)
        #
        # Previously we ran a `cleanup` closure directly off `worker.done`,
        # which is emitted from inside `_QtWorker.run` — i.e. while the
        # thread is *still* executing. That closure posted
        # `thread.deleteLater()` to the main event loop immediately, and if
        # main got back to its event loop before the native OS thread had
        # finished winding down, Qt would destroy the QThread C++ object
        # while the thread was still running:
        #
        #     QThread: Destroyed while thread '' is still running
        #     Aborted
        #
        # The race is widest on the first tool call of a turn, where stream
        # completion immediately submits a tool task synchronously, then
        # returns to the event loop with Thread S's DeferredDelete already
        # queued and only microseconds of OS-level teardown done.
        #
        # `thread.finished` fires *after* `QThread::run()` has returned, so
        # connecting deleteLater to it is safe.
        worker.done.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        # Bookkeeping: drop our strong reference once the thread is really
        # done. Runs on the main thread (the runner's affinity), so the
        # list mutation doesn't need the lock for thread-safety — but we
        # keep it for symmetry with the append side.
        def _forget_thread() -> None:
            with self._lock:
                self._threads = [t for t in self._threads if t[0] is not thread]

        thread.finished.connect(_forget_thread)
        thread.started.connect(worker.run)

        with self._lock:
            self._threads.append((thread, worker, handle))

        thread.start()
        return handle

    def cancel_all(self) -> None:
        with self._lock:
            handles = [h for _, _, h in self._threads]
        for handle in handles:
            handle.request_stop()

    def shutdown(self, wait: bool = True) -> None:
        self.cancel_all()
        if wait:
            with self._lock:
                threads = [t for t, _, _ in self._threads]
            for thread in threads:
                thread.quit()
                thread.wait(3000)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_error(exc: Exception) -> str:
    """Format an exception for on_error callback.

    Prints the traceback to stderr (matching the existing worker
    behavior) and returns a string representation.
    """
    print(f"❌ Task error: {exc}")
    traceback.print_exc()
    return str(exc)
