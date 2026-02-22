"""
Tests for clear_session() in LiveSession.

Tests that clearing a session properly resets all state,
creates a fresh prompt manager, preserves summaries, and commits.
"""

import pytest
from unittest.mock import MagicMock, patch

from forge.session.live_session import LiveSession, SessionState


@pytest.fixture
def mock_session_manager():
    """Create a mock SessionManager with the minimum interface needed."""
    sm = MagicMock()
    sm.branch_name = "test-branch"
    sm.repo_summaries = {}
    sm.prompt_manager = MagicMock()
    sm.prompt_manager.expire_ephemeral_results.return_value = 0
    sm.tool_manager = MagicMock()
    sm.tool_manager.discover_tools.return_value = []
    sm._create_fresh_vfs.return_value = MagicMock()
    sm.commit_ai_turn.return_value = "abc12345"
    sm.active_files = {"file1.py", "file2.py"}
    return sm


@pytest.fixture
def session(mock_session_manager):
    """Create a LiveSession with mocked dependencies."""
    s = LiveSession(mock_session_manager, messages=[])
    s._state = SessionState.IDLE
    return s


class TestClearSessionBasic:
    """Test basic clear_session behavior."""

    def test_clears_all_messages(self, session):
        """Messages list should be empty after clear."""
        session.messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
            {"role": "user", "content": "Do something"},
            {"role": "assistant", "content": "Done"},
        ]

        with patch("forge.prompts.manager.PromptManager") as MockPM:
            MockPM.return_value = MagicMock()
            session.clear_session()

        assert session.messages == []

    def test_resets_prompt_manager(self, session):
        """Should create a fresh PromptManager, not just clear conversation."""
        session.messages = [{"role": "user", "content": "Hello"}]
        old_pm = session.session_manager.prompt_manager

        with patch("forge.prompts.manager.PromptManager") as MockPM:
            mock_new_pm = MagicMock()
            MockPM.return_value = mock_new_pm
            session.clear_session()

        # Should have created a new PromptManager
        MockPM.assert_called_once()
        assert session.session_manager.prompt_manager is mock_new_pm

    def test_passes_tool_schemas_to_new_prompt_manager(self, session):
        """New PromptManager should get current tool schemas."""
        session.session_manager.tool_manager.discover_tools.return_value = [
            {"name": "grep_open"}, {"name": "edit"}
        ]

        with patch("forge.prompts.manager.PromptManager") as MockPM:
            MockPM.return_value = MagicMock()
            session.clear_session()

        MockPM.assert_called_once_with(tool_schemas=[{"name": "grep_open"}, {"name": "edit"}])

    def test_clears_active_files(self, session):
        """Active files should be cleared."""
        assert len(session.session_manager.active_files) == 2

        with patch("forge.prompts.manager.PromptManager") as MockPM:
            MockPM.return_value = MagicMock()
            session.clear_session()

        assert session.session_manager.active_files == set()

    def test_commits_cleared_state(self, session):
        """Should commit the empty state via commit_ai_turn."""
        session.messages = [{"role": "user", "content": "Hello"}]

        with patch("forge.prompts.manager.PromptManager") as MockPM:
            MockPM.return_value = MagicMock()
            session.clear_session()

        session.session_manager.commit_ai_turn.assert_called_once_with(
            [],  # empty messages
            session_metadata=session.get_session_metadata(),
        )

    def test_state_is_idle_after_clear(self, session):
        """State should be IDLE after clearing."""
        session._state = SessionState.WAITING_INPUT

        with patch("forge.prompts.manager.PromptManager") as MockPM:
            MockPM.return_value = MagicMock()
            session.clear_session()

        assert session.state == SessionState.IDLE

    def test_emits_messages_truncated_event(self, session):
        """Should emit MessagesTruncatedEvent(0) to notify UI."""
        session.messages = [{"role": "user", "content": "Hello"}]
        events = []

        # Attach so events go through signals
        session.attach()

        # Capture the truncation signal
        session.messages_truncated.connect(lambda n: events.append(n))

        with patch("forge.prompts.manager.PromptManager") as MockPM:
            MockPM.return_value = MagicMock()
            session.clear_session()

        assert 0 in events


class TestClearSessionInternalState:
    """Test that internal state fields are properly reset."""

    def test_clears_queued_message(self, session):
        """Queued message should be cleared."""
        session._queued_message = "pending follow-up"

        with patch("forge.prompts.manager.PromptManager") as MockPM:
            MockPM.return_value = MagicMock()
            session.clear_session()

        assert session._queued_message is None

    def test_clears_pending_wait_call(self, session):
        """Pending wait call should be cleared."""
        session._pending_wait_call = {
            "tool_call_id": "call_1",
            "tool_name": "session",
            "tool_args": {"action": "wait"},
        }

        with patch("forge.prompts.manager.PromptManager") as MockPM:
            MockPM.return_value = MagicMock()
            session.clear_session()

        assert session._pending_wait_call is None

    def test_clears_yield_message(self, session):
        """Yield message should be cleared."""
        session._yield_message = "Waiting for input"

        with patch("forge.prompts.manager.PromptManager") as MockPM:
            MockPM.return_value = MagicMock()
            session.clear_session()

        assert session._yield_message is None

    def test_clears_turn_tracking(self, session):
        """Turn tracking sets should be cleared."""
        session._turn_executed_tool_ids = {"call_1", "call_2"}
        session._newly_created_files = {"new.py"}
        session._pending_file_updates = [("file.py", None)]

        with patch("forge.prompts.manager.PromptManager") as MockPM:
            MockPM.return_value = MagicMock()
            session.clear_session()

        assert session._turn_executed_tool_ids == set()
        assert session._newly_created_files == set()
        assert session._pending_file_updates == []


class TestClearSessionSummaries:
    """Test that summaries are preserved and re-applied."""

    def test_preserves_summaries_when_present(self, session):
        """When repo_summaries exist, they should be re-applied to new prompt manager."""
        session.session_manager.repo_summaries = {
            "src/main.py": "Main entry point",
            "src/utils.py": "Utility functions",
        }

        with patch("forge.prompts.manager.PromptManager") as MockPM:
            mock_new_pm = MagicMock()
            MockPM.return_value = mock_new_pm
            session.clear_session()

        mock_new_pm.set_summaries.assert_called_once_with(
            {"src/main.py": "Main entry point", "src/utils.py": "Utility functions"}
        )

    def test_skips_summaries_when_empty(self, session):
        """When no repo_summaries, should not call set_summaries."""
        session.session_manager.repo_summaries = {}

        with patch("forge.prompts.manager.PromptManager") as MockPM:
            mock_new_pm = MagicMock()
            MockPM.return_value = mock_new_pm
            session.clear_session()

        mock_new_pm.set_summaries.assert_not_called()


class TestClearSessionGuards:
    """Test that clear_session is blocked in invalid states."""

    def test_blocked_when_running(self, session):
        """Should not clear when session is RUNNING."""
        session._state = SessionState.RUNNING
        session.messages = [{"role": "user", "content": "Hello"}]

        session.clear_session()

        # Messages should NOT be cleared
        assert len(session.messages) == 1
        # Should NOT have committed
        session.session_manager.commit_ai_turn.assert_not_called()

    def test_allowed_when_waiting_input(self, session):
        """Should work when session is WAITING_INPUT."""
        session._state = SessionState.WAITING_INPUT
        session.messages = [{"role": "user", "content": "Hello"}]

        with patch("forge.prompts.manager.PromptManager") as MockPM:
            MockPM.return_value = MagicMock()
            session.clear_session()

        assert session.messages == []

    def test_allowed_when_waiting_children(self, session):
        """Should work when session is WAITING_CHILDREN."""
        session._state = SessionState.WAITING_CHILDREN
        session.messages = [{"role": "user", "content": "Hello"}]

        with patch("forge.prompts.manager.PromptManager") as MockPM:
            MockPM.return_value = MagicMock()
            session.clear_session()

        assert session.messages == []

    def test_allowed_when_error(self, session):
        """Should work when session is in ERROR state."""
        session._state = SessionState.ERROR
        session.messages = [{"role": "user", "content": "Hello"}]

        with patch("forge.prompts.manager.PromptManager") as MockPM:
            MockPM.return_value = MagicMock()
            session.clear_session()

        assert session.messages == []