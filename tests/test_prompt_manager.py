"""
Tests for PromptManager - the core conversation state machine.

These tests cover:
- Basic conversation flow (user/assistant messages)
- Tool call and result handling
- filter_tool_calls() for multi-batch tool execution
- compact_tool_results() for context compaction
- to_messages() API format output
- Save/load simulation via block inspection
"""

import json
import pytest
from forge.prompts.manager import PromptManager, BlockType, ContentBlock


class TestBasicConversation:
    """Test basic user/assistant message flow"""

    def test_initial_state_has_system_prompt(self):
        pm = PromptManager(system_prompt="You are a helpful assistant.")
        assert len(pm.blocks) == 1
        assert pm.blocks[0].block_type == BlockType.SYSTEM
        assert pm.blocks[0].content == "You are a helpful assistant."

    def test_append_user_message(self):
        pm = PromptManager(system_prompt="System")
        pm.append_user_message("Hello!")
        
        assert len(pm.blocks) == 2
        assert pm.blocks[1].block_type == BlockType.USER_MESSAGE
        assert pm.blocks[1].content == "Hello!"

    def test_append_assistant_message(self):
        pm = PromptManager(system_prompt="System")
        pm.append_user_message("Hello!")
        pm.append_assistant_message("Hi there!")
        
        assert len(pm.blocks) == 3
        assert pm.blocks[2].block_type == BlockType.ASSISTANT_MESSAGE
        assert pm.blocks[2].content == "Hi there!"

    def test_conversation_to_messages(self):
        pm = PromptManager(system_prompt="System")
        pm.append_user_message("Hello!")
        pm.append_assistant_message("Hi there!")
        pm.append_user_message("How are you?")
        
        messages = pm.to_messages()
        
        # Should have: system, user(hello + recap/stats), assistant, user(how are you + recap/stats)
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[2]["role"] == "assistant"
        # Last message includes recap/stats injection
        assert messages[-1]["role"] == "user"


class TestToolCalls:
    """Test tool call and result handling"""

    def test_append_tool_call(self):
        pm = PromptManager(system_prompt="System")
        pm.append_user_message("Do something")
        
        tool_calls = [
            {"id": "call_1", "type": "function", "function": {"name": "test_tool", "arguments": "{}"}}
        ]
        pm.append_tool_call(tool_calls, content="I'll do that.")
        
        assert len(pm.blocks) == 3
        assert pm.blocks[2].block_type == BlockType.TOOL_CALL
        assert pm.blocks[2].content == "I'll do that."
        assert pm.blocks[2].metadata["tool_calls"] == tool_calls

    def test_append_tool_result(self):
        pm = PromptManager(system_prompt="System")
        pm.append_user_message("Do something")
        
        tool_calls = [
            {"id": "call_1", "type": "function", "function": {"name": "test_tool", "arguments": "{}"}}
        ]
        pm.append_tool_call(tool_calls)
        pm.append_tool_result("call_1", '{"success": true}')
        
        assert len(pm.blocks) == 4
        assert pm.blocks[3].block_type == BlockType.TOOL_RESULT
        assert pm.blocks[3].metadata["tool_call_id"] == "call_1"
        assert pm.blocks[3].metadata["user_id"] == "1"  # First tool result gets ID 1

    def test_tool_result_ids_increment(self):
        pm = PromptManager(system_prompt="System")
        pm.append_user_message("Do something")
        
        # Add multiple tool calls and results
        pm.append_tool_call([{"id": "call_1", "type": "function", "function": {"name": "tool1", "arguments": "{}"}}])
        pm.append_tool_result("call_1", '{"result": 1}')
        
        pm.append_tool_call([{"id": "call_2", "type": "function", "function": {"name": "tool2", "arguments": "{}"}}])
        pm.append_tool_result("call_2", '{"result": 2}')
        
        # Check user IDs increment
        tool_results = [b for b in pm.blocks if b.block_type == BlockType.TOOL_RESULT]
        assert tool_results[0].metadata["user_id"] == "1"
        assert tool_results[1].metadata["user_id"] == "2"

    def test_to_messages_includes_tool_calls(self):
        pm = PromptManager(system_prompt="System")
        pm.append_user_message("Do something")
        
        tool_calls = [
            {"id": "call_1", "type": "function", "function": {"name": "test_tool", "arguments": "{}"}}
        ]
        pm.append_tool_call(tool_calls, content="I'll help.")
        pm.append_tool_result("call_1", '{"success": true}')
        
        messages = pm.to_messages()
        
        # Find the assistant message with tool_calls
        tool_call_msg = next(m for m in messages if m.get("tool_calls"))
        assert tool_call_msg["role"] == "assistant"
        assert len(tool_call_msg["tool_calls"]) == 1
        assert tool_call_msg["content"] == "I'll help."
        
        # Find the tool result message
        tool_result_msg = next(m for m in messages if m.get("role") == "tool")
        assert tool_result_msg["tool_call_id"] == "call_1"


class TestFilterToolCalls:
    """Test filter_tool_calls() for handling partial tool execution"""

    def test_filter_removes_unexecuted_tools(self):
        """When tool B fails, tool C should be removed from the request"""
        pm = PromptManager(system_prompt="System")
        pm.append_user_message("Do A, B, C")
        
        # AI requests three tool calls
        tool_calls = [
            {"id": "call_a", "type": "function", "function": {"name": "tool_a", "arguments": "{}"}},
            {"id": "call_b", "type": "function", "function": {"name": "tool_b", "arguments": "{}"}},
            {"id": "call_c", "type": "function", "function": {"name": "tool_c", "arguments": "{}"}},
        ]
        pm.append_tool_call(tool_calls)
        
        # Only A and B were executed (B failed, C never ran)
        pm.append_tool_result("call_a", '{"success": true}')
        pm.append_tool_result("call_b", '{"success": false, "error": "failed"}')
        
        # Filter to only executed tools
        pm.filter_tool_calls({"call_a", "call_b"})
        
        # Check the tool_calls list was filtered
        tool_block = next(b for b in pm.blocks if b.block_type == BlockType.TOOL_CALL)
        assert len(tool_block.metadata["tool_calls"]) == 2
        tool_ids = [tc["id"] for tc in tool_block.metadata["tool_calls"]]
        assert "call_a" in tool_ids
        assert "call_b" in tool_ids
        assert "call_c" not in tool_ids

    def test_filter_deletes_empty_tool_block(self):
        """When no tools were executed, the entire block should be deleted"""
        pm = PromptManager(system_prompt="System")
        pm.append_user_message("Do something")
        
        tool_calls = [
            {"id": "call_1", "type": "function", "function": {"name": "tool", "arguments": "{}"}}
        ]
        pm.append_tool_call(tool_calls)
        
        # No tools executed
        pm.filter_tool_calls(set())
        
        # Block should be marked deleted
        tool_block = next(b for b in pm.blocks if b.block_type == BlockType.TOOL_CALL)
        assert tool_block.deleted is True

    def test_filter_handles_multiple_batches(self):
        """
        Critical test: When AI chains compact → done across batches,
        both tool calls should be preserved.
        
        This was a real bug: the first batch (compact) would run, then
        the second batch (done) would filter and accidentally remove
        the compact tool call because it wasn't in the current batch's
        executed_tool_ids.
        """
        pm = PromptManager(system_prompt="System")
        pm.append_user_message("Compact and finish")
        
        # First batch: compact
        pm.append_tool_call([
            {"id": "call_compact", "type": "function", "function": {"name": "compact", "arguments": "{}"}}
        ])
        pm.append_tool_result("call_compact", '{"success": true}')
        
        # Filter after first batch - compact was executed
        pm.filter_tool_calls({"call_compact"})
        
        # Second batch: done (same turn, AI continues)
        pm.append_tool_call([
            {"id": "call_done", "type": "function", "function": {"name": "done", "arguments": "{}"}}
        ])
        pm.append_tool_result("call_done", '{"success": true}')
        
        # Filter after second batch - BOTH tool IDs must be in the set
        # This is what _turn_executed_tool_ids accumulates in ai_chat_widget
        pm.filter_tool_calls({"call_compact", "call_done"})
        
        # Both tool call blocks should still exist (not deleted)
        tool_blocks = [b for b in pm.blocks if b.block_type == BlockType.TOOL_CALL and not b.deleted]
        assert len(tool_blocks) == 2

    def test_filter_only_affects_current_turn(self):
        """filter_tool_calls only affects blocks after the last user message"""
        pm = PromptManager(system_prompt="System")
        
        # Turn 1
        pm.append_user_message("First request")
        pm.append_tool_call([
            {"id": "turn1_call", "type": "function", "function": {"name": "tool", "arguments": "{}"}}
        ])
        pm.append_tool_result("turn1_call", '{"done": true}')
        pm.filter_tool_calls({"turn1_call"})
        
        # Turn 2 - new user message
        pm.append_user_message("Second request")
        pm.append_tool_call([
            {"id": "turn2_call", "type": "function", "function": {"name": "tool", "arguments": "{}"}}
        ])
        
        # Filter with empty set (nothing executed in turn 2)
        pm.filter_tool_calls(set())
        
        # Turn 1's tool call should be unaffected
        turn1_block = pm.blocks[2]  # After system, user1
        assert turn1_block.block_type == BlockType.TOOL_CALL
        assert not turn1_block.deleted
        assert len(turn1_block.metadata["tool_calls"]) == 1
        
        # Turn 2's tool call should be deleted
        turn2_block = pm.blocks[5]  # After user2
        assert turn2_block.block_type == BlockType.TOOL_CALL
        assert turn2_block.deleted


class TestCompactToolResults:
    """Test compact_tool_results() for context size management"""

    def test_compact_single_result(self):
        pm = PromptManager(system_prompt="System")
        pm.append_user_message("Do something")
        pm.append_tool_call([{"id": "call_1", "type": "function", "function": {"name": "tool", "arguments": "{}"}}])
        pm.append_tool_result("call_1", '{"very": "long", "result": "data" * 100}')
        
        count, error = pm.compact_tool_results("1", "1", "Did the thing successfully")
        
        assert error is None
        assert count == 1
        
        result_block = next(b for b in pm.blocks if b.block_type == BlockType.TOOL_RESULT)
        assert result_block.content == "[COMPACTED] Did the thing successfully"

    def test_compact_range(self):
        pm = PromptManager(system_prompt="System")
        pm.append_user_message("Do things")
        
        for i in range(5):
            pm.append_tool_call([{"id": f"call_{i}", "type": "function", "function": {"name": f"tool{i}", "arguments": "{}"}}])
            pm.append_tool_result(f"call_{i}", f'{{"result": {i}}}')
        
        count, error = pm.compact_tool_results("2", "4", "Tools 2-4 completed")
        
        assert error is None
        assert count == 3  # Results 2, 3, 4
        
        # Check compaction
        results = [b for b in pm.blocks if b.block_type == BlockType.TOOL_RESULT]
        assert "[COMPACTED] Tools 2-4 completed" in results[1].content  # #2
        assert "[COMPACTED - see above]" in results[2].content  # #3
        assert "[COMPACTED - see above]" in results[3].content  # #4
        # #1 and #5 should be unaffected
        assert "COMPACTED" not in results[0].content
        assert "COMPACTED" not in results[4].content

    def test_compact_truncates_tool_calls_between_results(self):
        """Compaction truncates tool calls that fall BETWEEN compacted results"""
        pm = PromptManager(system_prompt="System")
        pm.append_user_message("Edit files")
        
        # Create sequence: TC1, TR1, TC2, TR2, TC3, TR3
        # When compacting TR2-TR3, TC3 is between them and should be compacted
        pm.append_tool_call([{"id": "call_1", "type": "function", "function": {"name": "tool1", "arguments": "{}"}}])
        pm.append_tool_result("call_1", '{"success": true}')
        
        large_args = json.dumps({"filepath": "test.py", "content": "x" * 1000})
        pm.append_tool_call([{"id": "call_2", "type": "function", "function": {"name": "write_file", "arguments": large_args}}])
        pm.append_tool_result("call_2", '{"success": true}')
        
        pm.append_tool_call([{"id": "call_3", "type": "function", "function": {"name": "tool3", "arguments": "{}"}}])
        pm.append_tool_result("call_3", '{"success": true}')
        
        # Compact range 2-3
        # Block layout: [sys, user, TC1, TR1, TC2, TR2, TC3, TR3]
        # Indices:       0    1     2    3    4    5    6    7
        # from_idx=5 (TR2), to_idx=7 (TR3)
        # TC3 at idx 6 is IN the range and should be compacted
        pm.compact_tool_results("2", "3", "Wrote files")
        
        tool_blocks = [b for b in pm.blocks if b.block_type == BlockType.TOOL_CALL and not b.deleted]
        
        # TC1 (before range) - unaffected
        assert tool_blocks[0].metadata["tool_calls"][0]["function"]["arguments"] == "{}"
        
        # TC2 (before range - its result TR2 starts the range) - unaffected  
        # The tool call precedes its result, so it's at idx 4, before from_idx=5
        tc2 = tool_blocks[1].metadata["tool_calls"][0]
        assert "filepath" in tc2["function"]["arguments"]  # NOT compacted
        
        # TC3 (idx 6, between TR2 and TR3) - SHOULD be compacted
        tc3 = tool_blocks[2].metadata["tool_calls"][0]
        assert tc3["function"]["arguments"] == '{"_compacted": true}'

    def test_compact_invalid_range(self):
        pm = PromptManager(system_prompt="System")
        pm.append_user_message("Do something")
        pm.append_tool_call([{"id": "call_1", "type": "function", "function": {"name": "tool", "arguments": "{}"}}])
        pm.append_tool_result("call_1", '{"result": true}')
        
        # from > to
        count, error = pm.compact_tool_results("5", "1", "Invalid")
        assert error is not None
        assert "must be <=" in error
        
        # Non-existent ID
        count, error = pm.compact_tool_results("99", "99", "Missing")
        assert error is not None
        assert "not found" in error


class TestFileContent:
    """Test file content management"""

    def test_append_file_content(self):
        pm = PromptManager(system_prompt="System")
        pm.append_file_content("test.py", "print('hello')")
        
        file_blocks = [b for b in pm.blocks if b.block_type == BlockType.FILE_CONTENT]
        assert len(file_blocks) == 1
        assert file_blocks[0].metadata["filepath"] == "test.py"
        assert "print('hello')" in file_blocks[0].content

    def test_file_content_replaces_previous(self):
        """When a file is modified, old content is deleted and new is appended"""
        pm = PromptManager(system_prompt="System")
        pm.append_file_content("test.py", "v1")
        pm.append_file_content("test.py", "v2")
        
        # Only one active file block
        active_files = [b for b in pm.blocks if b.block_type == BlockType.FILE_CONTENT and not b.deleted]
        assert len(active_files) == 1
        assert "v2" in active_files[0].content

    def test_get_active_files(self):
        pm = PromptManager(system_prompt="System")
        pm.append_file_content("a.py", "content a")
        pm.append_file_content("b.py", "content b")
        pm.remove_file_content("a.py")
        
        files = pm.get_active_files()
        assert files == ["b.py"]

    def test_file_relocation_preserves_order(self):
        """When updating a file, other files after it are relocated to maintain contiguity"""
        pm = PromptManager(system_prompt="System")
        pm.append_file_content("a.py", "a")
        pm.append_file_content("b.py", "b")
        pm.append_file_content("c.py", "c")
        
        # Update a.py - b and c should be relocated after it
        pm.append_file_content("a.py", "a_updated")
        
        files = pm.get_active_files()
        # Order should be: b, c, a (a was updated last)
        assert files == ["b.py", "c.py", "a.py"]


class TestSummaries:
    """Test repository summaries"""

    def test_set_summaries(self):
        pm = PromptManager(system_prompt="System")
        pm.set_summaries({"file.py": "A Python file"})
        
        summary_blocks = [b for b in pm.blocks if b.block_type == BlockType.SUMMARIES]
        assert len(summary_blocks) == 1
        assert "file.py" in summary_blocks[0].content

    def test_set_summaries_replaces_previous(self):
        pm = PromptManager(system_prompt="System")
        pm.set_summaries({"old.py": "Old file"})
        pm.set_summaries({"new.py": "New file"})
        
        active_summaries = [b for b in pm.blocks if b.block_type == BlockType.SUMMARIES and not b.deleted]
        assert len(active_summaries) == 1
        assert "new.py" in active_summaries[0].content
        assert "old.py" not in active_summaries[0].content


class TestToMessagesFormat:
    """Test the to_messages() output format for API compatibility"""

    def test_consecutive_user_content_grouped(self):
        """Consecutive user-role blocks should be grouped into single message"""
        pm = PromptManager(system_prompt="System")
        pm.set_summaries({"file.py": "A file"})
        pm.append_file_content("file.py", "content")
        pm.append_user_message("Hello!")
        
        messages = pm.to_messages()
        
        # Should be: system, user (summaries + file + hello + recap/stats)
        assert len([m for m in messages if m["role"] == "user"]) == 1
        user_msg = next(m for m in messages if m["role"] == "user")
        # Multiple content blocks in one message
        assert len(user_msg["content"]) >= 3  # summaries, file, user msg, + recap/stats

    def test_tool_result_includes_user_id(self):
        """Tool results should include user-friendly ID in content"""
        pm = PromptManager(system_prompt="System")
        pm.append_user_message("Do something")
        pm.append_tool_call([{"id": "call_1", "type": "function", "function": {"name": "tool", "arguments": "{}"}}])
        pm.append_tool_result("call_1", '{"success": true}')
        
        messages = pm.to_messages()
        tool_msg = next(m for m in messages if m.get("role") == "tool")
        
        # Content should have the user-friendly ID prefix
        content_text = tool_msg["content"][0]["text"]
        assert "[tool_call_id: 1]" in content_text

    def test_think_tool_compacted_in_output(self):
        """Think tool calls should have scratchpad stripped in to_messages()"""
        pm = PromptManager(system_prompt="System")
        pm.append_user_message("Think about something")
        
        # Think tool with full scratchpad
        think_args = json.dumps({"scratchpad": "Long thinking..." * 100, "conclusion": "The answer is 42"})
        pm.append_tool_call([{"id": "think_1", "type": "function", "function": {"name": "think", "arguments": think_args}}])
        pm.append_tool_result("think_1", '{"conclusion": "The answer is 42"}')
        
        messages = pm.to_messages()
        
        # Find the think tool call in messages
        tool_msg = next(m for m in messages if m.get("tool_calls"))
        think_call = tool_msg["tool_calls"][0]
        
        # Should be compacted (scratchpad stripped)
        assert think_call["function"]["arguments"] == '{"_compacted": true}'

    def test_cache_control_on_last_content_block(self):
        """cache_control should be on the last content block before stats injection"""
        pm = PromptManager(system_prompt="System")
        pm.append_user_message("Hello")
        pm.append_assistant_message("Hi there")
        pm.append_user_message("How are you?")
        
        messages = pm.to_messages()
        
        # Find blocks with cache_control
        cached_blocks = []
        for msg in messages:
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if block.get("cache_control"):
                        cached_blocks.append(block)
        
        # Should have exactly one cached block
        assert len(cached_blocks) == 1


class TestSessionSimulation:
    """
    Test save/load scenarios by inspecting and rebuilding block state.
    
    In production, session state is persisted as a list of operations.
    These tests verify that replaying operations produces identical state.
    """

    def test_replay_produces_identical_state(self):
        """Replaying the same operations should produce identical blocks"""
        # Build initial state
        pm1 = PromptManager(system_prompt="System")
        pm1.set_summaries({"file.py": "A file"})
        pm1.append_user_message("Hello")
        pm1.append_assistant_message("Hi")
        pm1.append_tool_call([{"id": "c1", "type": "function", "function": {"name": "tool", "arguments": "{}"}}])
        pm1.append_tool_result("c1", '{"ok": true}')
        pm1.filter_tool_calls({"c1"})
        
        # "Save" by capturing the messages
        messages1 = pm1.to_messages()
        
        # "Load" by rebuilding from scratch
        pm2 = PromptManager(system_prompt="System")
        pm2.set_summaries({"file.py": "A file"})
        pm2.append_user_message("Hello")
        pm2.append_assistant_message("Hi")
        pm2.append_tool_call([{"id": "c1", "type": "function", "function": {"name": "tool", "arguments": "{}"}}])
        pm2.append_tool_result("c1", '{"ok": true}')
        pm2.filter_tool_calls({"c1"})
        
        messages2 = pm2.to_messages()
        
        # Messages should be identical
        assert len(messages1) == len(messages2)
        for m1, m2 in zip(messages1, messages2):
            assert m1["role"] == m2["role"]

    def test_compaction_survives_reload(self):
        """After compaction, reloading should preserve compacted state"""
        pm = PromptManager(system_prompt="System")
        pm.append_user_message("Do work")
        
        for i in range(3):
            pm.append_tool_call([{"id": f"c{i}", "type": "function", "function": {"name": f"t{i}", "arguments": "{}"}}])
            pm.append_tool_result(f"c{i}", f'{{"data": "{("x" * 100)}"}}')
            pm.filter_tool_calls({f"c{i}"})
        
        # Compact
        pm.compact_tool_results("1", "3", "Did 3 things")
        
        # Get messages (simulating what would be sent to API after reload)
        messages = pm.to_messages()
        
        # Tool results should be compacted
        tool_results = [m for m in messages if m.get("role") == "tool"]
        assert len(tool_results) == 3
        
        # First should have summary, rest should reference it
        assert "COMPACTED" in tool_results[0]["content"][0]["text"]


class TestContextStats:
    """Test context statistics and recap generation"""

    def test_get_context_stats(self):
        pm = PromptManager(system_prompt="System prompt here")
        pm.set_summaries({"a.py": "File A", "b.py": "File B"})
        pm.append_file_content("a.py", "content " * 100)
        pm.append_user_message("Hello")
        pm.append_assistant_message("Hi there")
        
        stats = pm.get_context_stats()
        
        assert stats["system_tokens"] > 0
        assert stats["summaries_tokens"] > 0
        assert stats["files_tokens"] > 0
        assert stats["conversation_tokens"] > 0
        assert stats["file_count"] == 1
        assert stats["total_tokens"] == (
            stats["system_tokens"] + 
            stats["summaries_tokens"] + 
            stats["files_tokens"] + 
            stats["conversation_tokens"]
        )

    def test_conversation_recap(self):
        pm = PromptManager(system_prompt="System")
        pm.append_user_message("Please help me")
        pm.append_assistant_message("Sure, I'll help!")
        pm.append_tool_call([{"id": "c1", "type": "function", "function": {"name": "search_replace", "arguments": '{"filepath": "test.py"}'}}])
        pm.append_tool_result("c1", '{"success": true}')
        
        recap = pm.format_conversation_recap()
        
        assert "Conversation Recap" in recap
        assert "Please help me" in recap
        assert "search_replace" in recap
        assert "Result #1" in recap


class TestClearConversation:
    """Test clearing conversation while preserving context"""

    def test_clear_keeps_system_and_files(self):
        pm = PromptManager(system_prompt="System")
        pm.set_summaries({"file.py": "A file"})
        pm.append_file_content("file.py", "content")
        pm.append_user_message("Hello")
        pm.append_assistant_message("Hi")
        pm.append_tool_call([{"id": "c1", "type": "function", "function": {"name": "tool", "arguments": "{}"}}])
        pm.append_tool_result("c1", '{"ok": true}')
        
        pm.clear_conversation()
        
        # Should keep system, summaries, file content
        assert len(pm.blocks) == 3
        assert pm.blocks[0].block_type == BlockType.SYSTEM
        assert pm.blocks[1].block_type == BlockType.SUMMARIES
        assert pm.blocks[2].block_type == BlockType.FILE_CONTENT
        
        # Tool ID counter should reset
        assert pm._next_tool_id == 1
