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
    sm.append_tool_result = lambda tc_id, content: pm.append_tool_result(tc_id, content)
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

    def test_ui_only_messages_skipped(self, mock_session_manager):
        """UI-only messages should not affect message ID numbering."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi", "_ui_only": True},  # Skipped
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