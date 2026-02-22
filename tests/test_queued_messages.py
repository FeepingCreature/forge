"""
Tests for queued message handling in LiveSession.

Tests both paths where queued messages are checked:
1. Text-only responses: _finish_stream_processing() with no tool_calls
2. Tool-call responses: _on_tools_all_finished() after tool execution
"""

import json
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
    return sm


@pytest.fixture
def session(mock_session_manager):
    """Create a LiveSession with mocked dependencies."""
    s = LiveSession(mock_session_manager, messages=[])
    s._state = SessionState.RUNNING
    return s


class TestQueuedMessageTextOnly:
    """Test queued message injection in the text-only path (_finish_stream_processing)."""

    def test_queued_message_injected_on_text_only_response(self, session):
        """When AI responds with text only (no tool calls) and a message is queued,
        the queued message should be injected and continue processing."""
        session._queued_message = "follow-up question"

        # Patch _continue_after_tools to prevent actual LLM call
        with patch.object(session, "_continue_after_tools") as mock_continue:
            session._finish_stream_processing({"content": "Here is my answer."})

        # The assistant message should have been recorded
        session.session_manager.append_assistant_message.assert_called_once_with(
            "Here is my answer."
        )

        # Queued message should have been consumed
        assert session._queued_message is None

        # A mid-turn user message should have been added
        mid_turn_msgs = [
            m for m in session.messages
            if m.get("role") == "user" and m.get("_mid_turn")
        ]
        assert len(mid_turn_msgs) == 1
        assert mid_turn_msgs[0]["content"] == "follow-up question"

        # Should have appended the queued message to prompt manager
        session.session_manager.append_user_message.assert_called_with("follow-up question")

        # Should continue processing (not commit and finish)
        mock_continue.assert_called_once()

        # Should NOT have committed (that happens after the continued turn)
        session.session_manager.commit_ai_turn.assert_not_called()

    def test_no_queued_message_commits_and_finishes(self, session):
        """When AI responds with text only and no queued message, should commit and finish."""
        session._queued_message = None

        session._finish_stream_processing({"content": "Here is my answer."})

        # Should have recorded the assistant message
        session.session_manager.append_assistant_message.assert_called_once_with(
            "Here is my answer."
        )

        # Should have committed
        session.session_manager.commit_ai_turn.assert_called_once()

        # State should be IDLE
        assert session.state == SessionState.IDLE

    def test_queued_message_cleared_after_injection(self, session):
        """The queued message field should be None after injection."""
        session._queued_message = "my queued msg"

        with patch.object(session, "_continue_after_tools"):
            session._finish_stream_processing({"content": "response"})

        assert session._queued_message is None

    def test_text_only_with_tool_calls_does_not_check_queue(self, session):
        """When response has tool_calls, _finish_stream_processing should
        dispatch to _execute_tool_calls instead of checking the queue."""
        session._queued_message = "queued"

        tool_calls = [
            {
                "id": "call_1",
                "function": {"name": "grep_open", "arguments": '{"pattern": "foo"}'},
            }
        ]

        with patch.object(session, "_execute_tool_calls") as mock_exec:
            session._finish_stream_processing({"content": "", "tool_calls": tool_calls})

        # Should have dispatched to tool execution
        mock_exec.assert_called_once_with(tool_calls)

        # Queued message should still be there (handled later by _on_tools_all_finished)
        assert session._queued_message == "queued"


class TestQueuedMessageToolCallPath:
    """Test queued message injection in the tool-call path (_on_tools_all_finished)."""

    def test_queued_message_injected_after_tools(self, session):
        """When tools finish and a message is queued, the queued message
        should be injected and continue processing."""
        session._queued_message = "follow-up after tools"
        session._turn_executed_tool_ids = set()
        session._pending_file_updates = []

        # Simulate tool results
        results = [
            {
                "tool_call": {"id": "call_1", "function": {"name": "grep_open"}},
                "result": {"success": True},
                "args": {"pattern": "foo"},
            }
        ]

        # Add a matching assistant message with tool_calls
        session.messages.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_1", "function": {"name": "grep_open"}}],
        })

        with patch.object(session, "_continue_after_tools") as mock_continue:
            session._on_tools_all_finished(results)

        # Queued message should have been consumed
        assert session._queued_message is None

        # A mid-turn user message should have been added
        mid_turn_msgs = [
            m for m in session.messages
            if m.get("role") == "user" and m.get("_mid_turn")
        ]
        assert len(mid_turn_msgs) == 1
        assert mid_turn_msgs[0]["content"] == "follow-up after tools"

        # Should continue processing
        mock_continue.assert_called_once()

    def test_no_queued_message_continues_normally(self, session):
        """When tools finish with no queued message, should continue normally."""
        session._queued_message = None
        session._turn_executed_tool_ids = set()
        session._pending_file_updates = []

        results = [
            {
                "tool_call": {"id": "call_1", "function": {"name": "grep_open"}},
                "result": {"success": True},
                "args": {"pattern": "foo"},
            }
        ]

        session.messages.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_1", "function": {"name": "grep_open"}}],
        })

        with patch.object(session, "_continue_after_tools") as mock_continue:
            session._on_tools_all_finished(results)

        # Should still continue (to send tool results back to LLM)
        mock_continue.assert_called_once()

        # No mid-turn messages should exist
        mid_turn_msgs = [
            m for m in session.messages
            if m.get("role") == "user" and m.get("_mid_turn")
        ]
        assert len(mid_turn_msgs) == 0

    def test_yield_flag_takes_priority_over_queued_message(self, session):
        """When a tool returns _yield, the session should wait even if
        there's a queued message."""
        session._queued_message = "I have a follow-up"
        session._turn_executed_tool_ids = set()
        session._pending_file_updates = []

        results = [
            {
                "tool_call": {
                    "id": "call_1",
                    "function": {"name": "session", "arguments": "{}"},
                },
                "result": {"_yield": True, "_yield_message": "Waiting on children"},
                "args": {"action": "wait", "branches": ["child-1"]},
            }
        ]

        session.messages.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_1", "function": {"name": "session", "arguments": "{}"}},
            ],
        })

        with patch.object(session, "yield_waiting") as mock_yield:
            session._on_tools_all_finished(results)

        # yield_waiting should have been called
        mock_yield.assert_called_once_with("Waiting on children")

        # Queued message should still be pending (will be sent on resume)
        assert session._queued_message == "I have a follow-up"


class TestQueuedMessageSendMessage:
    """Test the send_message path for queuing."""

    def test_send_message_queues_when_running(self, session):
        """When session is RUNNING, send_message should queue the message."""
        session._state = SessionState.RUNNING

        result = session.send_message("queued text")

        assert result is True
        assert session._queued_message == "queued text"

    def test_send_message_replaces_existing_queue(self, session):
        """A new queued message should replace any existing one."""
        session._state = SessionState.RUNNING
        session._queued_message = "old message"

        session.send_message("new message")

        assert session._queued_message == "new message"

    def test_send_message_not_accepted_in_error_state(self, session):
        """send_message should return False when in ERROR state."""
        session._state = SessionState.ERROR

        result = session.send_message("test")

        assert result is False


class TestFinishStreamNewFiles:
    """Test that _finish_stream_processing handles newly created files."""

    def test_new_files_get_summaries_generated(self, session):
        """When there are newly created files, summaries should be generated."""
        session._newly_created_files = {"new_file.py", "another.py"}
        session._queued_message = None

        session._finish_stream_processing({"content": "Created the files."})

        # Should have called generate_summary_for_file for each
        calls = session.session_manager.generate_summary_for_file.call_args_list
        generated_files = {call.args[0] for call in calls}
        assert generated_files == {"new_file.py", "another.py"}

        # Set should be cleared
        assert len(session._newly_created_files) == 0