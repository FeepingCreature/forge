"""
Tests for queued message handling in LiveSession.

A queued message is what gets stashed when the user calls
`send_message` while the session is RUNNING — it gets injected as the
next user turn whenever the AI's current step finishes.

There are three injection sites:
  1. send_message itself (when state is RUNNING)
  2. _finish_stream_processing (text-only assistant response)
  3. _continue_after_tools (after API tool calls finish)

Most of these are exercised through real flows via the harness. A few
white-box tests poke `session.session._queued_message` directly via
`harness.given_queued_message(...)`; that's a deliberate harness verb
because the queue slot is a real piece of session state, not just a
private detail.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge.session.live_session import SessionState

if TYPE_CHECKING:
    from tests.harness import SessionTestHarness


class TestQueuedMessageTextOnly:
    """Queued message injected after a text-only assistant response."""

    def test_queued_message_becomes_next_user_turn(
        self, session: "SessionTestHarness"
    ) -> None:
        # First turn: AI replies with text only. Before run_turn fires,
        # we plant a queued message simulating "user typed during stream".
        session.user_says("first question")
        session.given_queued_message("follow-up while you were thinking")
        session.ai_says_raw("Here is my answer.")
        session.ai_says_raw("And here is my follow-up answer.")

        result = session.run_turn()

        assert result.succeeded
        assert session.session._queued_message is None

        # The conversation now has the queued message as a real (mid-turn)
        # user message between the two assistant replies.
        roles = [(m.get("role"), m.get("content"), m.get("_mid_turn"))
                 for m in session.messages]
        assert roles == [
            ("user", "first question", None),
            ("assistant", "Here is my answer.", None),
            ("user", "follow-up while you were thinking", True),
            ("assistant", "And here is my follow-up answer.", None),
        ]

    def test_no_queued_message_finishes_turn(
        self, session: "SessionTestHarness"
    ) -> None:
        """Without a queued message, a text-only response just finishes
        the turn and lands at IDLE."""
        session.user_says("hi")
        session.ai_says_raw("hello.")

        result = session.run_turn()

        assert result.succeeded
        assert result.final_state == SessionState.IDLE
        # Only the original two messages, no mid-turn user injection.
        assert [m.get("role") for m in session.messages] == ["user", "assistant"]
        assert not any(m.get("_mid_turn") for m in session.messages)

    def test_queued_message_visible_to_ai_in_next_prompt(
        self, session: "SessionTestHarness"
    ) -> None:
        """The injected queued message must show up in the rendered
        prompt the AI sees on the follow-up step (not just in the local
        message list)."""
        session.user_says("first")
        session.given_queued_message("PLEASE_SEE_ME")
        session.ai_says_raw("ok.")
        session.ai_says_raw("got it.")

        session.run_turn()

        assert "PLEASE_SEE_ME" in session.next_prompt_text()


class TestQueuedMessageToolCallPath:
    """Queued message injected after API-tool-call execution finishes."""

    def test_queued_message_injected_after_tool_calls(
        self, session: "SessionTestHarness"
    ) -> None:
        """If a queued message is pending when tools finish, it lands as
        a mid-turn user message before the AI's next reply."""
        # AI calls grep_open, then on follow-up gives a text answer.
        session.user_says("look up foo")
        session.given_queued_message("also: did you check bar?")
        session.ai_returns_tool_calls(
            [
                {
                    "id": "call_1",
                    "function": {
                        "name": "grep_open",
                        "arguments": '{"pattern": "foo"}',
                    },
                }
            ],
            content="",
        )
        session.ai_says_raw("Looked at foo and bar both.")

        result = session.run_turn()

        assert result.succeeded
        assert session.session._queued_message is None

        # The mid-turn user message is now in the conversation.
        mid_turn = [m for m in session.messages if m.get("_mid_turn")]
        assert len(mid_turn) == 1
        assert mid_turn[0]["content"] == "also: did you check bar?"
        assert mid_turn[0]["role"] == "user"

    def test_no_queued_message_continues_normally_after_tools(
        self, session: "SessionTestHarness"
    ) -> None:
        """Without a queued message, the post-tool flow just sends tool
        results back and continues — no mid-turn user message."""
        session.user_says("look up foo")
        session.ai_returns_tool_calls(
            [
                {
                    "id": "call_1",
                    "function": {
                        "name": "grep_open",
                        "arguments": '{"pattern": "foo"}',
                    },
                }
            ],
            content="",
        )
        session.ai_says_raw("done.")

        result = session.run_turn()

        assert result.succeeded
        assert not any(m.get("_mid_turn") for m in session.messages)

    def test_yield_takes_priority_over_queued_message(
        self, session: "SessionTestHarness"
    ) -> None:
        """When a tool returns ``_yield``, the session must wait — the
        queued message stays pending and gets sent on resume.

        White-box: this calls ``_on_tools_all_finished`` directly with a
        synthetic ``_yield`` result. Driving a real yield would require
        wiring up the ``session`` tool with real child branches, which
        is far more setup than this single behavioural assertion needs.
        """
        from unittest.mock import patch

        live = session.session  # ensure session is built
        session.given_state(SessionState.RUNNING)
        session.given_queued_message("I have a follow-up")

        # Attach an assistant message with the matching tool_call so the
        # filter step in _on_tools_all_finished has something to look at.
        live._turn_executed_tool_ids = set()
        live._pending_file_updates = []
        live.messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call_1",
                     "function": {"name": "session", "arguments": "{}"}},
                ],
            }
        )

        results = [
            {
                "tool_call": {
                    "id": "call_1",
                    "function": {"name": "session", "arguments": "{}"},
                },
                "result": {
                    "_yield": True,
                    "_yield_message": "Waiting on children",
                },
                "args": {"action": "wait", "branches": ["child-1"]},
            }
        ]

        with patch.object(live, "yield_waiting") as mock_yield:
            live._on_tools_all_finished(results)

        mock_yield.assert_called_once_with("Waiting on children")
        # Queued message survives the yield — it'll be sent on resume.
        assert live._queued_message == "I have a follow-up"


class TestQueuedMessageSendMessage:
    """send_message's own queueing behaviour when called while RUNNING."""

    def test_send_message_queues_when_running(
        self, session: "SessionTestHarness"
    ) -> None:
        session.given_state(SessionState.RUNNING)

        accepted = session.session.send_message("queued text")

        assert accepted is True
        assert session.session._queued_message == "queued text"

    def test_send_message_replaces_existing_queue(
        self, session: "SessionTestHarness"
    ) -> None:
        session.given_state(SessionState.RUNNING)
        session.given_queued_message("old message")

        session.session.send_message("new message")

        assert session.session._queued_message == "new message"

    def test_send_message_rejected_in_error_state(
        self, session: "SessionTestHarness"
    ) -> None:
        session.given_state(SessionState.ERROR)

        accepted = session.session.send_message("test")

        assert accepted is False
        assert session.session._queued_message is None


class TestFinishStreamNewFiles:
    """A turn that creates new files should request summaries for them."""

    def test_new_files_get_summaries_generated(
        self, session: "SessionTestHarness"
    ) -> None:
        # Opt into recording — the harness stubs this method by default.
        session.given_files({"existing.py": "x = 1\n"})
        session.track_file_summaries()

        # AI writes two new files via the inline @write directive. The
        # inline executor reports these via the NEW_FILES_CREATED side
        # effect, which routes into _newly_created_files, which
        # _finish_stream_processing flushes through generate_summary_for_file.
        session.user_says("create two files")
        session.ai_says(
            """
            Creating both files now.

            @write new_file.py
                def foo():
                    return 1

            @write another.py
                def bar():
                    return 2

            Done.
            """
        )

        result = session.run_turn()

        assert result.succeeded
        assert set(session.summarized_files) == {"new_file.py", "another.py"}
        # And the newly-created tracking set was drained on the way out.
        assert session.session._newly_created_files == set()