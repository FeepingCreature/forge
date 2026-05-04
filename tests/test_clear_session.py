"""
Tests for clear_session() in LiveSession.

Migrated from MagicMock-based unit tests to use the SessionTestHarness.
The claims are unchanged — clearing a session resets messages, prompt
manager, active files, internal state, and commits — but they're now
verified against real session state (not mock call counts).

A few tests still poke at private LiveSession attributes (`_queued_message`,
`_pending_wait_call`, etc.) because that's what's genuinely under test:
the state-reset half of `clear_session()`. The harness exposes `.session`
specifically so internal-state tests can do this when appropriate.
"""

from __future__ import annotations

import pytest

from forge.session.live_session import SessionState


def _seed_one_turn(session) -> None:
    """Run one minimal turn so the session has messages + a non-empty prompt
    manager to clear. Used as fixture-like setup for tests that want a
    populated session before clear_session()."""
    session.given_files({"a.py": "x = 1\n"})
    session.user_says("hello")
    session.ai_says("Hi there.")
    session.run_turn()


# ---------------------------------------------------------------------------
# Basic clear behaviour
# ---------------------------------------------------------------------------


class TestClearSessionBasic:
    """Test basic clear_session behavior."""

    def test_clears_all_messages(self, session):
        """Messages list should be empty after clear."""
        _seed_one_turn(session)
        assert len(session.messages) > 0  # sanity

        session.session.clear_session()

        assert session.messages == []

    def test_resets_prompt_manager(self, session):
        """Should create a fresh PromptManager (different identity), not just clear conversation."""
        _seed_one_turn(session)
        old_pm = session.session_manager.prompt_manager

        session.session.clear_session()

        # Should be a brand new PromptManager instance.
        assert session.session_manager.prompt_manager is not old_pm
        # And it should not carry the previous turn's content. (A fresh PM
        # renders a system / conversation-recap preamble even when empty,
        # so we check for the seeded text rather than expecting zero
        # non-system messages.)
        rendered = session.next_prompt_text()
        assert "hello" not in rendered, (
            "previous user message leaked into freshly-built PromptManager"
        )
        assert "Hi there." not in rendered, (
            "previous assistant message leaked into freshly-built PromptManager"
        )

    def test_passes_tool_schemas_to_new_prompt_manager(self, session):
        """New PromptManager should be built with current tool schemas.

        We don't have a clean way to introspect what schemas the PM was
        built with, but we can verify the new system prompt mentions the
        same tools the tool_manager exposes. That's the actual property
        the original test was protecting (PM gets fresh schemas).
        """
        _seed_one_turn(session)

        session.session.clear_session()

        # New PM exists and has a system prompt.
        new_pm = session.session_manager.prompt_manager
        assert new_pm.system_prompt is not None
        assert isinstance(new_pm.system_prompt, str)
        # System prompt should mention at least one real tool name. The
        # discovery is non-trivial here but `edit` is always present.
        assert "edit" in new_pm.system_prompt.lower()

    def test_clears_active_files(self, session):
        """Active files should be cleared."""
        session.given_files({"a.py": "x = 1\n", "b.py": "y = 2\n"})
        session.given_files_in_context("a.py", "b.py")
        # Trigger session build so active_files is populated.
        assert len(session.session_manager.active_files) == 2

        session.session.clear_session()

        assert session.session_manager.active_files == set()

    def test_commits_cleared_state(self, session):
        """Should commit the empty state — branch HEAD should advance."""
        _seed_one_turn(session)
        head_before = session.repo.get_branch_head("master").id

        session.session.clear_session()

        head_after = session.repo.get_branch_head("master").id
        assert head_after != head_before, "clear_session should produce a commit"

    def test_state_is_idle_after_clear(self, session):
        """State should be IDLE after clearing."""
        _seed_one_turn(session)
        # Force a non-IDLE state to verify the reset.
        session.session._state = SessionState.WAITING_INPUT

        session.session.clear_session()

        assert session.session.state == SessionState.IDLE

    def test_emits_messages_truncated_event(self, session):
        """Should emit messages_truncated(0) signal."""
        _seed_one_turn(session)
        session.session.attach()

        events: list[int] = []
        session.session.messages_truncated.connect(lambda n: events.append(n))

        session.session.clear_session()

        assert 0 in events


# ---------------------------------------------------------------------------
# Internal state reset
# ---------------------------------------------------------------------------


class TestClearSessionInternalState:
    """Test that internal state fields are properly reset.

    These tests deliberately poke at private LiveSession attributes — the
    behavior under test IS the internal reset. The harness gives us a real
    session so the assertions are meaningful (not just `mock.field = X`).
    """

    def test_clears_queued_message(self, session):
        _seed_one_turn(session)
        session.session._queued_message = "pending follow-up"

        session.session.clear_session()

        assert session.session._queued_message is None

    def test_clears_pending_wait_call(self, session):
        _seed_one_turn(session)
        session.session._pending_wait_call = {
            "tool_call_id": "call_1",
            "tool_name": "session",
            "tool_args": {"action": "wait"},
        }

        session.session.clear_session()

        assert session.session._pending_wait_call is None

    def test_clears_yield_message(self, session):
        _seed_one_turn(session)
        session.session._yield_message = "Waiting for input"

        session.session.clear_session()

        assert session.session._yield_message is None

    def test_clears_turn_tracking(self, session):
        _seed_one_turn(session)
        session.session._turn_executed_tool_ids = {"call_1", "call_2"}
        session.session._newly_created_files = {"new.py"}
        session.session._pending_file_updates = [("file.py", None)]

        session.session.clear_session()

        assert session.session._turn_executed_tool_ids == set()
        assert session.session._newly_created_files == set()
        assert session.session._pending_file_updates == []


# ---------------------------------------------------------------------------
# Summary preservation
# ---------------------------------------------------------------------------


class TestClearSessionSummaries:
    """Test that summaries are preserved and re-applied to new prompt manager."""

    def test_preserves_summaries_when_present(self, session):
        """When repo_summaries exist, they should appear in the new PM's rendered output."""
        _seed_one_turn(session)
        session.session_manager.repo_summaries = {
            "src/main.py": "Main entry point",
            "src/utils.py": "Utility functions",
        }

        session.session.clear_session()

        # Verify summaries are visible in the rendered prompt.
        rendered = session.next_prompt_text()
        assert "src/main.py" in rendered
        assert "Main entry point" in rendered
        assert "src/utils.py" in rendered
        assert "Utility functions" in rendered

    def test_skips_summaries_when_empty(self, session):
        """When no repo_summaries, clear_session should still succeed without error."""
        _seed_one_turn(session)
        session.session_manager.repo_summaries = {}

        # Should not raise.
        session.session.clear_session()

        # And should produce an empty conversation.
        assert session.messages == []


# ---------------------------------------------------------------------------
# State guards
# ---------------------------------------------------------------------------


class TestClearSessionGuards:
    """Test that clear_session is blocked in invalid states."""

    def test_blocked_when_running(self, session):
        """Should not clear when session is RUNNING."""
        _seed_one_turn(session)
        msg_count_before = len(session.messages)
        head_before = session.repo.get_branch_head("master").id

        session.session._state = SessionState.RUNNING
        session.session.clear_session()

        # Messages NOT cleared.
        assert len(session.messages) == msg_count_before
        # No commit happened.
        head_after = session.repo.get_branch_head("master").id
        assert head_after == head_before

    @pytest.mark.parametrize(
        "allowed_state",
        [
            SessionState.WAITING_INPUT,
            SessionState.WAITING_CHILDREN,
            SessionState.ERROR,
            SessionState.IDLE,
        ],
    )
    def test_allowed_in_non_running_states(self, session, allowed_state):
        """clear_session should work in any non-RUNNING state."""
        _seed_one_turn(session)
        session.session._state = allowed_state

        session.session.clear_session()

        assert session.messages == []
        assert session.session.state == SessionState.IDLE