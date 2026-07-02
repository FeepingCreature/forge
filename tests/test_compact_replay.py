"""
Tests for compact call replay during session reload.

Verifies that replay_messages_to_prompt_manager correctly defers compaction
until after all messages (including tool results) have been appended.
"""

import json
from unittest.mock import MagicMock

import pytest

from forge.prompts.manager import BlockType, PromptManager
from forge.session.startup import replay_messages_to_prompt_manager


@pytest.fixture
def mock_session_manager():
    """Create a mock SessionManager that delegates to a real PromptManager."""
    pm = PromptManager(system_prompt="System prompt")

    sm = MagicMock()
    sm.prompt_manager = pm

    # Wire through to the real PromptManager methods
    sm.append_user_message = lambda content: pm.append_user_message(content)
    sm.append_assistant_message = lambda content: pm.append_assistant_message(content)
    sm.append_tool_call = lambda tc, content="": pm.append_tool_call(tc, content)
    sm.append_tool_result = lambda tc_id, content, is_ephemeral=False: pm.append_tool_result(
        tc_id, content, is_ephemeral
    )
    sm.compact_messages = lambda from_id, to_id, summary: pm.compact_messages(
        from_id, to_id, summary
    )

    return sm


class TestCompactReplayDeferred:
    """Test that compaction is deferred until after all messages are replayed."""

    def test_tool_results_compacted_after_replay(self, mock_session_manager):
        """Tool results in the compacted range should be compacted after replay.

        This is the core bug: during replay, compact was applied immediately
        when the assistant tool_call message was seen, but the corresponding
        tool results hadn't been appended yet. The fix defers compaction.
        """
        messages = [
            {"role": "user", "content": "Hello"},
            # Assistant message #2 with a tool call
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "grep_open",
                            "arguments": '{"pattern": "foo"}',
                        },
                    }
                ],
                "content": "Let me search for that.",
            },
            # Tool result for call_1
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": '{"matches": ["file1.py", "file2.py"], "success": true}',
            },
            # User message #3
            {"role": "user", "content": "Now compact that."},
            # Assistant message #4 with compact tool call
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_compact",
                        "type": "function",
                        "function": {
                            "name": "compact",
                            "arguments": json.dumps(
                                {
                                    "from_id": "1",
                                    "to_id": "3",
                                    "summary": "Searched for foo, found file1.py and file2.py",
                                }
                            ),
                        },
                    }
                ],
                "content": "",
            },
            # Tool result for compact
            {
                "role": "tool",
                "tool_call_id": "call_compact",
                "content": '{"success": true, "compacted": 3}',
            },
        ]

        replay_messages_to_prompt_manager(messages, mock_session_manager)

        pm = mock_session_manager.prompt_manager

        # Find the tool result for call_1 - it should be compacted
        tool_results = [
            b
            for b in pm.blocks
            if b.block_type == BlockType.TOOL_RESULT
            and not b.deleted
            and b.metadata.get("tool_call_id") == "call_1"
        ]
        assert len(tool_results) == 1
        assert "COMPACTED" in tool_results[0].content

    def test_multiple_tool_results_compacted(self, mock_session_manager):
        """Multiple tool results in range should all be compacted."""
        messages = [
            {"role": "user", "content": "Do three things"},
            # Tool call #2 with 3 tools
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_a",
                        "type": "function",
                        "function": {"name": "tool_a", "arguments": "{}"},
                    },
                    {
                        "id": "call_b",
                        "type": "function",
                        "function": {"name": "tool_b", "arguments": "{}"},
                    },
                    {
                        "id": "call_c",
                        "type": "function",
                        "function": {"name": "tool_c", "arguments": "{}"},
                    },
                ],
                "content": "Running three tools.",
            },
            {"role": "tool", "tool_call_id": "call_a", "content": '{"data": "' + "x" * 500 + '"}'},
            {"role": "tool", "tool_call_id": "call_b", "content": '{"data": "' + "y" * 500 + '"}'},
            {"role": "tool", "tool_call_id": "call_c", "content": '{"data": "' + "z" * 500 + '"}'},
            # User #3
            {"role": "user", "content": "Compact please"},
            # Compact call #4
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_compact",
                        "type": "function",
                        "function": {
                            "name": "compact",
                            "arguments": json.dumps(
                                {
                                    "from_id": "1",
                                    "to_id": "3",
                                    "summary": "Did three things",
                                }
                            ),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_compact",
                "content": '{"success": true}',
            },
        ]

        replay_messages_to_prompt_manager(messages, mock_session_manager)

        pm = mock_session_manager.prompt_manager

        # All three tool results should be compacted
        for call_id in ["call_a", "call_b", "call_c"]:
            results = [
                b
                for b in pm.blocks
                if b.block_type == BlockType.TOOL_RESULT
                and not b.deleted
                and b.metadata.get("tool_call_id") == call_id
            ]
            assert len(results) == 1, f"Expected 1 result for {call_id}"
            assert "COMPACTED" in results[0].content, (
                f"Tool result for {call_id} should be compacted, got: {results[0].content[:100]}"
            )

    def test_ui_only_system_messages_skipped(self, mock_session_manager):
        """Display-only system messages should not affect message ID numbering.

        Only `_ui_only` messages with role=="system" are display-only and never
        touch the PromptManager at runtime, so replay must skip them too.
        """
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "system", "content": "notice", "_ui_only": True},  # Skipped
            {"role": "assistant", "content": "Hi there"},
            {"role": "user", "content": "Bye"},
        ]

        replay_messages_to_prompt_manager(messages, mock_session_manager)

        pm = mock_session_manager.prompt_manager

        # Should have 3 conversation blocks (user, assistant, user)
        conv_blocks = [
            b
            for b in pm.blocks
            if b.block_type
            in (BlockType.USER_MESSAGE, BlockType.ASSISTANT_MESSAGE)
            and not b.deleted
        ]
        assert len(conv_blocks) == 3
        # IDs should be sequential: 1, 2, 3
        assert conv_blocks[0].metadata["message_id"] == "1"
        assert conv_blocks[1].metadata["message_id"] == "2"
        assert conv_blocks[2].metadata["message_id"] == "3"

    def test_synthetic_user_messages_consume_ids(self, mock_session_manager):
        """`_synthetic` USER messages must consume a message_id during replay.

        At runtime, inline-command-error feedback is added as a `_synthetic`
        user message AND appended to the PromptManager (consuming an ID) — the
        LLM DOES see it, so it is NOT `_ui_only`. If replay skipped it, every
        later message_id would drift down by one and stored compact ranges
        would select the wrong blocks. This is the regression guard for that
        ID drift.
        """
        messages = [
            {"role": "user", "content": "Hello"},  # id 1
            {"role": "assistant", "content": "Reply"},  # id 2
            # Inline-error feedback: _synthetic user role -> consumes id 3
            {"role": "user", "content": "error feedback", "_synthetic": True},
            {"role": "user", "content": "Next"},  # id 4
        ]

        replay_messages_to_prompt_manager(messages, mock_session_manager)

        pm = mock_session_manager.prompt_manager

        conv_blocks = [
            b
            for b in pm.blocks
            if b.block_type
            in (BlockType.USER_MESSAGE, BlockType.ASSISTANT_MESSAGE)
            and not b.deleted
        ]
        # All four messages are replayed (the _synthetic user message included)
        assert len(conv_blocks) == 4
        assert [b.metadata["message_id"] for b in conv_blocks] == ["1", "2", "3", "4"]

    def test_synthetic_user_message_keeps_compact_range_aligned(self, mock_session_manager):
        """A stored compact range must still hit the right tool result after reload.

        Reproduces the original bug: an inline-error `_synthetic` user message
        occurs before a tool call whose result is later compacted. If replay
        dropped the `_synthetic` message, the message IDs would shift and the
        compact range (captured against runtime IDs) would miss the tool call,
        leaving its result uncompacted.
        """
        messages = [
            {"role": "user", "content": "Hello"},  # id 1
            # Inline-error feedback consumes id 2 at runtime
            {"role": "user", "content": "error feedback", "_synthetic": True},
            # Tool call at id 3
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "grep_open", "arguments": "{}"},
                    }
                ],
                "content": "searching",
            },
            {"role": "tool", "tool_call_id": "call_1", "content": '{"ok": true}'},
            {"role": "user", "content": "compact"},  # id 4
            # Compact ids 1..3 (captured against runtime IDs that INCLUDE the
            # _synthetic user message at id 2)
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_compact",
                        "type": "function",
                        "function": {
                            "name": "compact",
                            "arguments": json.dumps(
                                {"from_id": "1", "to_id": "3", "summary": "did stuff"}
                            ),
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_compact", "content": '{"success": true}'},
        ]

        replay_messages_to_prompt_manager(messages, mock_session_manager)

        pm = mock_session_manager.prompt_manager

        # The grep_open tool result should be compacted because its tool call
        # (id 3) falls inside the compact range only when the _synthetic user
        # message consumed id 2.
        results = [
            b
            for b in pm.blocks
            if b.block_type == BlockType.TOOL_RESULT
            and not b.deleted
            and b.metadata.get("tool_call_id") == "call_1"
        ]
        assert len(results) == 1
        assert "COMPACTED" in results[0].content

    def test_malformed_compact_args_skipped(self, mock_session_manager):
        """Malformed compact arguments should be silently skipped."""
        messages = [
            {"role": "user", "content": "Hello"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_bad",
                        "type": "function",
                        "function": {
                            "name": "compact",
                            "arguments": "not valid json{{{",
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_bad",
                "content": '{"error": "bad args"}',
            },
        ]

        # Should not raise
        replay_messages_to_prompt_manager(messages, mock_session_manager)

    def test_compact_without_from_to_skipped(self, mock_session_manager):
        """Compact with missing from_id/to_id should be skipped."""
        messages = [
            {"role": "user", "content": "Hello"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_empty",
                        "type": "function",
                        "function": {
                            "name": "compact",
                            "arguments": json.dumps({"summary": "something"}),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_empty",
                "content": '{"success": false}',
            },
        ]

        # Should not raise
        replay_messages_to_prompt_manager(messages, mock_session_manager)

    def test_messages_after_compact_not_affected(self, mock_session_manager):
        """Messages after the compacted range should retain full content."""
        messages = [
            {"role": "user", "content": "First message"},
            {"role": "assistant", "content": "First reply"},
            {"role": "user", "content": "Second message"},
            {"role": "assistant", "content": "Second reply"},
            # Compact messages 1-2 only
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_compact",
                        "type": "function",
                        "function": {
                            "name": "compact",
                            "arguments": json.dumps(
                                {
                                    "from_id": "1",
                                    "to_id": "2",
                                    "summary": "Greeted each other",
                                }
                            ),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_compact",
                "content": '{"success": true}',
            },
        ]

        replay_messages_to_prompt_manager(messages, mock_session_manager)

        pm = mock_session_manager.prompt_manager

        # Messages 1-2 should be compacted
        msg1 = [
            b for b in pm.blocks
            if b.block_type == BlockType.USER_MESSAGE
            and not b.deleted
            and b.metadata.get("message_id") == "1"
        ]
        assert len(msg1) == 1
        assert "COMPACTED" in msg1[0].content

        # Messages 3-4 should retain full content
        msg3 = [
            b for b in pm.blocks
            if b.block_type == BlockType.USER_MESSAGE
            and not b.deleted
            and b.metadata.get("message_id") == "3"
        ]
        assert len(msg3) == 1
        assert msg3[0].content == "Second message"

        msg4 = [
            b for b in pm.blocks
            if b.block_type == BlockType.ASSISTANT_MESSAGE
            and not b.deleted
            and b.metadata.get("message_id") == "4"
        ]
        assert len(msg4) == 1
        assert msg4[0].content == "Second reply"