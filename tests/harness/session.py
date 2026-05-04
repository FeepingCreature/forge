"""
SessionTestHarness — the test-facing API for flow tests.

Sits on top of three Phase-1/2/3 seams:
  - SyncTaskRunner so the pipeline executes straight-line.
  - ScriptedBackend so no network is touched.
  - The runtime helpers (stream_to_events / run_inline_commands /
    execute_tool_calls) which let the pipeline run against the VFS
    without Qt or threading.

Lifecycle:
  1. Construct (cheap; no SessionManager yet).
  2. Setup methods (.given_files etc.) seed in-memory state.
  3. First call to `.session` lazily builds repo + SessionManager +
     LiveSession. After that, mutations like .given_files are not
     allowed (they'd silently disagree with the repo state).
  4. .user_says queues a user message; .ai_says queues a scripted
     response; .run_turn drains them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from forge.config.settings import Settings
from forge.runtime import ScriptedBackend, SyncTaskRunner
from forge.session.live_session import LiveSession, SessionState
from forge.session.manager import SessionManager
from tests.harness.dsl import compile_dsl
from tests.harness.repo import FAILING_MAKEFILE, PASSING_MAKEFILE, bootstrap_repo

if TYPE_CHECKING:
    from forge.git_backend.repository import ForgeRepository
    from forge.vfs.work_in_progress import WorkInProgressVFS


@dataclass
class TurnResult:
    """Outcome of a single .run_turn() call.

    Attributes:
        succeeded: True if the turn ended in a normal idle/finished state
            with no inline-command failures.
        failed_at: Name of the first inline command that failed
            ("run_tests", "check", "edit", ...), or None.
        inline_results: Raw list of result dicts from the inline pipeline
            for the most recent assistant message.
        final_state: The SessionState the LiveSession ended in.
        error_message: The first error string surfaced (from inline
            failure or stream error), or None.
    """

    succeeded: bool
    failed_at: str | None
    inline_results: list[dict[str, Any]] = field(default_factory=list)
    final_state: str | None = None
    error_message: str | None = None


class _VFSView:
    """Dict-like view: harness.vfs[path] reads from the live VFS."""

    def __init__(self, harness: SessionTestHarness) -> None:
        self._h = harness

    def __getitem__(self, path: str) -> str:
        vfs = self._h._vfs()
        return vfs.read_file(path)

    def __contains__(self, path: str) -> bool:
        vfs = self._h._vfs()
        return vfs.file_exists(path)

    def list(self) -> list[str]:
        return list(self._h._vfs().list_files())


class SessionTestHarness:
    """Test harness for the AI session pipeline. See module docstring."""

    def __init__(self, tmp_path: Path) -> None:
        self._tmp_path = tmp_path

        # Pre-session state — flushed into the repo on first use.
        self._initial_files: dict[str, str | bytes] = {}
        self._files_in_context: list[str] = []
        self._test_makefile: str | None = None

        # Built lazily.
        self._repo: ForgeRepository | None = None
        self._session_manager: SessionManager | None = None
        self._live_session: LiveSession | None = None
        self._backend = ScriptedBackend()
        self._task_runner = SyncTaskRunner()

        # Pending script.
        self._pending_user_message: str | None = None

        # Captured outcomes.
        self.last_turn: TurnResult | None = None

    # --- Setup (pre-session) ---

    def given_files(self, files: dict[str, str | bytes]) -> SessionTestHarness:
        """Seed the repo's initial commit with these files.

        Must be called before the session is built (i.e. before any
        .vfs / .user_says / .run_turn / .session access).
        """
        self._ensure_pre_session("given_files")
        self._initial_files.update(files)
        return self

    def given_passing_tests(self) -> SessionTestHarness:
        """Install a hermetic Makefile that always passes."""
        self._ensure_pre_session("given_passing_tests")
        self._test_makefile = PASSING_MAKEFILE
        return self

    def given_failing_tests(self) -> SessionTestHarness:
        """Install a hermetic Makefile that always fails."""
        self._ensure_pre_session("given_failing_tests")
        self._test_makefile = FAILING_MAKEFILE
        return self

    def given_files_in_context(self, *paths: str) -> SessionTestHarness:
        """Mark these files as in-context for the AI.

        Adds them to active_files and the prompt manager once the session
        is built. Must be set before the session is built.
        """
        self._ensure_pre_session("given_files_in_context")
        for p in paths:
            if p not in self._files_in_context:
                self._files_in_context.append(p)
        return self

    # --- Script (queues, doesn't trigger) ---

    def user_says(self, text: str) -> SessionTestHarness:
        """Queue a user message. Triggered by .run_turn()."""
        if self._pending_user_message is not None:
            raise AssertionError(
                "user_says called twice without a .run_turn() between them. "
                "Each turn consumes exactly one user message."
            )
        self._pending_user_message = text
        return self

    def ai_says(self, dsl_text: str) -> SessionTestHarness:
        """Queue a scripted assistant response written in the DSL."""
        rendered = compile_dsl(dsl_text)
        self._backend.queue_response(content=rendered)
        return self

    def ai_says_raw(self, content: str) -> SessionTestHarness:
        """Queue a scripted assistant response as raw text (no DSL)."""
        self._backend.queue_response(content=content)
        return self

    def ai_returns_tool_calls(
        self, tool_calls: list[dict[str, Any]], content: str | None = None
    ) -> SessionTestHarness:
        """Queue a scripted response that returns API tool calls."""
        self._backend.queue_response(content=content, tool_calls=tool_calls)
        return self

    # --- Run ---

    def run_turn(self) -> TurnResult:
        """Send the pending user message and drain the pipeline.

        Returns a TurnResult summarising what happened.
        """
        if self._pending_user_message is None:
            raise AssertionError(
                "run_turn() called with no pending user message. "
                "Call .user_says(text) first."
            )

        sess = self.session  # build if needed
        text = self._pending_user_message
        self._pending_user_message = None

        # Snapshot turn-failure tracking. The session emits errors via
        # error_occurred and stores per-message inline results in
        # _inline_results_by_index when applicable.
        captured_errors: list[str] = []

        def _on_error(msg: str) -> None:
            captured_errors.append(msg)

        sess.error_occurred.connect(_on_error)
        try:
            # Skip the workdir-cleanliness gate: it refuses headless sessions
            # on a dirty workdir, and tests don't care about that. The repo
            # bootstrap leaves the workdir untouched anyway.
            sess.send_message(text, _skip_workdir_check=True)
        finally:
            sess.error_occurred.disconnect(_on_error)

        inline_results = self._extract_last_inline_results(sess)
        failed_at = self._first_failed_tool(inline_results)

        result = TurnResult(
            succeeded=(failed_at is None and not captured_errors),
            failed_at=failed_at,
            inline_results=inline_results,
            final_state=sess.state,
            error_message=captured_errors[0] if captured_errors else None,
        )
        self.last_turn = result
        return result

    # --- White-box state pokes ---
    #
    # These bypass the normal send_message / run_turn flow to set up
    # specific internal session states that are otherwise hard to reach
    # synchronously. They build the session lazily if needed.

    def given_state(self, state: str) -> SessionTestHarness:
        """Force the LiveSession into a given state (e.g. RUNNING).

        Useful for tests that exercise behaviour conditional on state
        without driving a real partial turn (which SyncTaskRunner can't
        easily pause mid-flight).
        """
        self.session._state = state
        return self

    def given_queued_message(self, text: str) -> SessionTestHarness:
        """Simulate 'user typed a message while the AI was running'.

        This sets _queued_message directly. In production this slot is
        only filled by send_message() called while state is RUNNING.
        Tests that just want to verify the consume-the-queue branches
        should use this instead of orchestrating a real mid-turn send.
        """
        self.session._queued_message = text
        return self

    def track_file_summaries(self) -> SessionTestHarness:
        """Opt-in: record every generate_summary_for_file call.

        The harness stubs this method by default to keep the network
        out of tests. Tests that specifically care about the
        new-files-trigger-summaries behaviour can opt in here and read
        `harness.summarized_files` afterward.
        """
        self.summarized_files: list[str] = []

        def _record(filepath: str) -> str | None:
            self.summarized_files.append(filepath)
            return None

        self.session_manager.generate_summary_for_file = _record  # type: ignore[method-assign]
        return self

    # --- Inspection ---

    @property
    def vfs(self) -> _VFSView:
        """Dict-like view on the VFS. harness.vfs[path] -> contents."""
        return _VFSView(self)

    @property
    def messages(self) -> list[dict[str, Any]]:
        """Live session message list."""
        return list(self.session.messages)

    @property
    def prompt_blocks(self) -> list[Any]:
        """Raw PromptManager blocks for advanced assertions."""
        return list(self.session_manager.prompt_manager.blocks)

    def next_prompt_text(self) -> str:
        """Concatenated user-side text from the rendered prompt messages.

        Useful for assertions like 'old marker is not visible to the AI'.
        """
        msgs = self.session_manager.prompt_manager.to_messages()
        parts: list[str] = []
        for m in msgs:
            content = m.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for chunk in content:
                    if isinstance(chunk, dict) and "text" in chunk:
                        parts.append(str(chunk["text"]))
        return "\n".join(parts)

    @property
    def backend(self) -> ScriptedBackend:
        """The ScriptedBackend, for direct inspection / advanced setup."""
        return self._backend

    # --- Lazy session construction ---

    @property
    def session(self) -> LiveSession:
        if self._live_session is None:
            self._build_session()
        assert self._live_session is not None
        return self._live_session

    @property
    def session_manager(self) -> SessionManager:
        if self._session_manager is None:
            self._build_session()
        assert self._session_manager is not None
        return self._session_manager

    @property
    def repo(self) -> ForgeRepository:
        if self._repo is None:
            self._build_session()
        assert self._repo is not None
        return self._repo

    # --- Internals ---

    def _vfs(self) -> WorkInProgressVFS:
        return self.session_manager.tool_manager.vfs

    def _ensure_pre_session(self, op: str) -> None:
        if self._live_session is not None:
            raise AssertionError(
                f"{op}() must be called before the session is built "
                f"(i.e. before any .vfs/.session/.run_turn access)."
            )

    def _build_session(self) -> None:
        # Bake in the test Makefile if requested.
        files = dict(self._initial_files)
        if self._test_makefile is not None:
            files.setdefault("Makefile", self._test_makefile)
        if not files:
            files = {".gitkeep": ""}

        self._repo = bootstrap_repo(self._tmp_path, files=files)

        # Settings stores its config tree on `self.settings` (not `self.config`).
        # The bare-init pattern below skips file IO so the test never touches
        # the user's real config.
        settings = Settings.__new__(Settings)
        settings.settings = {}

        # Disable every SessionManager method that constructs an LLMClient
        # directly. There are FOUR such methods (none of them go through
        # the LLMBackend seam — that's only for the streaming chat path):
        #
        #   - start_summary_generation: spawns repo-summary work on the
        #     task runner; would queue a real LLM call.
        #   - generate_repo_summaries: the work above; also called sync.
        #   - generate_summary_for_file: invoked when a turn creates a
        #     new file (LiveSession._finish_stream_processing calls this).
        #   - generate_commit_message: invoked at end-of-turn when a
        #     commit lands (LiveSession.commit_ai_turn calls this).
        #
        # All four are stubbed at the class level for the duration of the
        # SessionManager construction; they're then re-stubbed on the
        # instance so subsequent invocations stay neutralised. Tests that
        # care about commit messages or summaries should set them on
        # prompt_manager / read commits directly through the repo.
        original_start = SessionManager.start_summary_generation
        SessionManager.start_summary_generation = lambda self, force_refresh=False: None  # type: ignore[assignment, method-assign]
        try:
            self._session_manager = SessionManager(
                repo=self._repo,
                branch_name="master",
                settings=settings,
                task_runner=self._task_runner,
            )
        finally:
            SessionManager.start_summary_generation = original_start  # type: ignore[method-assign]

        # Per-instance stubs so the rest of the test never reaches the
        # network. Bound as plain attributes — Python will resolve them
        # before the unbound class methods.
        def _no_summary_generation(force_refresh: bool = False) -> None:
            return None

        def _no_repo_summaries(
            force_refresh: bool = False,
            progress_callback: Any = None,
        ) -> dict[str, str]:
            return {}

        def _no_file_summary(filepath: str) -> str | None:
            return None

        def _stub_commit_message(changes: dict[str, str]) -> str:
            return "test: simulated commit"

        self._session_manager.start_summary_generation = _no_summary_generation  # type: ignore[method-assign]
        self._session_manager.generate_repo_summaries = _no_repo_summaries  # type: ignore[method-assign]
        self._session_manager.generate_summary_for_file = _no_file_summary  # type: ignore[method-assign]
        self._session_manager.generate_commit_message = _stub_commit_message  # type: ignore[method-assign]

        # Wire up requested in-context files.
        for path in self._files_in_context:
            self._session_manager.add_active_file(path)

        # Build the live session with the same task runner + scripted backend.
        self._live_session = LiveSession(
            session_manager=self._session_manager,
            task_runner=self._task_runner,
            llm_backend=self._backend,
        )

    @staticmethod
    def _extract_last_inline_results(sess: LiveSession) -> list[dict[str, Any]]:
        """Pull the most recent inline-pipeline results from the session.

        On the success path, LiveSession stashes them on the last assistant
        message under `_inline_results` (see `update_last_assistant_message`
        in `_on_inline_commands_finished`).

        On the failure path, no `_inline_results` key is ever written —
        instead the failure is reflected as a `❌ <tool> failed:` user
        message right after the assistant message. We synthesize a single
        result dict from that pattern so the harness has *something*
        meaningful to surface as `failed_at`.
        """
        # Walk back from the end; find the most recent substantive assistant
        # message — i.e. one with non-empty content OR with `_inline_results`
        # already attached. Empty assistant messages exist as placeholders for
        # retried turns when the ScriptedBackend has been drained, and they
        # would otherwise mask the prior turn's failure.
        last_assistant_idx: int | None = None
        for i in range(len(sess.messages) - 1, -1, -1):
            m = sess.messages[i]
            if m.get("role") != "assistant":
                continue
            if "_inline_results" in m:
                last_assistant_idx = i
                break
            content = m.get("content", "")
            if isinstance(content, str) and content.strip():
                last_assistant_idx = i
                break
        if last_assistant_idx is None:
            return []

        msg = sess.messages[last_assistant_idx]
        if "_inline_results" in msg:
            return list(msg["_inline_results"])

        # Failure path: look for the synthetic "❌ <tool> failed:" user
        # message following the assistant message. Don't require _ui_only —
        # the prefix is unique enough on its own.
        for j in range(last_assistant_idx + 1, len(sess.messages)):
            m = sess.messages[j]
            if m.get("role") != "user":
                continue
            content = m.get("content", "")
            if not isinstance(content, str) or not content.startswith("❌ "):
                continue
            # Format: "❌ `<tool_name>` failed:\n\n<error>"
            try:
                # Strip "❌ `" and split at "` failed:"
                rest = content[len("❌ `") :]
                tool_name, _, tail = rest.partition("` failed:")
                error_msg = tail.strip()
            except Exception:
                tool_name, error_msg = "unknown", content
            return [{"success": False, "tool": tool_name, "error": error_msg}]

        return []

    @staticmethod
    def _first_failed_tool(results: list[dict[str, Any]]) -> str | None:
        """Return the tool name of the first failed result, or None."""
        for r in results:
            if not r.get("success", True):
                return str(r.get("tool") or r.get("tool_name") or "unknown")
        return None


# Public alias for the SessionState constants — tests may want to compare.
__all__ = ["SessionTestHarness", "TurnResult", "SessionState"]