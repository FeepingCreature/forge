"""
Tests for inline edit parsing and execution.

These tests cover:
- Basic edit block parsing
- Edge cases with malformed syntax
- Empty replace blocks (deletion)
- Multiple edits in one message
- Execution behavior
"""

import pytest
from forge.tools.builtin.edit import parse_edits, execute_edit, execute_edits, EditBlock


class TestParseEdits:
    """Test edit block parsing from assistant message text."""

    def test_parse_simple_edit(self):
        content = '''Here's the fix:

<edit file="test.py">
<search>
def foo():
    return 1
</search>
<replace>
def foo():
    return 2
</replace>
</edit>

That should work!'''

        edits = parse_edits(content)
        
        assert len(edits) == 1
        assert edits[0].file == "test.py"
        assert edits[0].search == "def foo():\n    return 1"
        assert edits[0].replace == "def foo():\n    return 2"

    def test_parse_multiple_edits(self):
        content = '''<edit file="a.py">
<search>
old_a
</search>
<replace>
new_a
</replace>
</edit>

And also:

<edit file="b.py">
<search>
old_b
</search>
<replace>
new_b
</replace>
</edit>'''

        edits = parse_edits(content)
        
        assert len(edits) == 2
        assert edits[0].file == "a.py"
        assert edits[1].file == "b.py"

    def test_parse_empty_replace_deletion(self):
        """Empty replace block means delete the search text"""
        content = '''<edit file="test.py">
<search>
# Remove this comment
</search>
<replace>
</replace>
</edit>'''

        edits = parse_edits(content)
        
        assert len(edits) == 1
        assert edits[0].search == "# Remove this comment"
        assert edits[0].replace == ""

    def test_edit_positions_tracked(self):
        """Edit blocks should track their position for truncation on failure"""
        content = "Some text\n<edit file=\"x.py\">\n<search>\na\n</search>\n<replace>\nb\n</replace>\n</edit>\nMore text"
        
        edits = parse_edits(content)
        
        assert len(edits) == 1
        assert edits[0].start_pos == 10  # After "Some text\n"
        assert content[edits[0].start_pos:edits[0].end_pos].startswith("<edit")
        assert content[edits[0].start_pos:edits[0].end_pos].endswith("</edit>")


class TestMalformedEdits:
    """Test handling of malformed edit syntax - the AI sometimes does weird things."""

    def test_empty_replace_then_text_then_another_edit(self):
        """
        Real failure case: AI writes:
        </search>
        <replace>
        </edit>Actually, let me think...
        
        Then another edit. The regex should NOT match across both.
        """
        content = '''<edit file="a.py">
<search>
foo
</search>
<replace>
</edit>Actually, let me think about this more carefully.

<edit file="b.py">
<search>
bar
</search>
<replace>
baz
</replace>
</edit>'''

        edits = parse_edits(content)
        
        # The first "edit" is malformed (no </replace>), should not match
        # Only the second valid edit should be parsed
        assert len(edits) == 1
        assert edits[0].file == "b.py"
        assert edits[0].search == "bar"
        assert edits[0].replace == "baz"

    def test_greedy_regex_does_not_cross_edit_blocks(self):
        """
        The regex's .*? should be non-greedy, but we need to ensure
        it doesn't match across </edit>...<edit> boundaries.
        """
        content = '''<edit file="first.py">
<search>
aaa
</search>
<replace>
bbb
</replace>
</edit>

Some text in between.

<edit file="second.py">
<search>
ccc
</search>
<replace>
ddd
</replace>
</edit>'''

        edits = parse_edits(content)
        
        assert len(edits) == 2
        assert edits[0].file == "first.py"
        assert edits[0].replace == "bbb"
        assert edits[1].file == "second.py"
        assert edits[1].replace == "ddd"

    def test_missing_closing_edit_tag(self):
        """Edit without </edit> should not match"""
        content = '''<edit file="test.py">
<search>
foo
</search>
<replace>
bar
</replace>

I forgot to close the edit tag.'''

        edits = parse_edits(content)
        assert len(edits) == 0

    def test_missing_replace_tag(self):
        """Edit without <replace> should not match"""
        content = '''<edit file="test.py">
<search>
foo
</search>
I forgot the replace section
</edit>'''

        edits = parse_edits(content)
        assert len(edits) == 0

    def test_nested_angle_brackets_in_content(self):
        """Content with angle brackets (like HTML/XML) should work"""
        content = '''<edit file="template.html">
<search>
<div class="old">
  <span>text</span>
</div>
</search>
<replace>
<div class="new">
  <span>updated</span>
</div>
</replace>
</edit>'''

        edits = parse_edits(content)
        
        assert len(edits) == 1
        assert '<div class="old">' in edits[0].search
        assert '<div class="new">' in edits[0].replace


class TestExecuteEdit:
    """Test edit execution against VFS."""

    def test_execute_simple_edit(self, tmp_path):
        """Basic edit execution"""
        from forge.vfs.work_in_progress import WorkInProgressVFS
        from unittest.mock import MagicMock
        
        # Create a mock repo
        repo = MagicMock()
        repo.path = tmp_path
        
        # Create a real file
        test_file = tmp_path / "test.py"
        test_file.write_text("def foo():\n    return 1\n")
        
        # Mock the VFS to read from our file
        vfs = MagicMock()
        vfs.read_file.return_value = "def foo():\n    return 1\n"
        
        edit = EditBlock(
            file="test.py",
            search="return 1",
            replace="return 2",
            start_pos=0,
            end_pos=100
        )
        
        result = execute_edit(vfs, edit)
        
        assert result["success"] is True
        vfs.write_file.assert_called_once()
        # Check the new content was written
        new_content = vfs.write_file.call_args[0][1]
        assert "return 2" in new_content
        assert "return 1" not in new_content

    def test_execute_file_not_found(self):
        """Edit on non-existent file fails gracefully"""
        from unittest.mock import MagicMock
        
        vfs = MagicMock()
        vfs.read_file.side_effect = FileNotFoundError("No such file")
        
        edit = EditBlock(
            file="nonexistent.py",
            search="foo",
            replace="bar",
            start_pos=0,
            end_pos=50
        )
        
        result = execute_edit(vfs, edit)
        
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_execute_search_not_found(self):
        """Edit with non-matching search text fails with helpful error"""
        from unittest.mock import MagicMock
        
        vfs = MagicMock()
        vfs.read_file.return_value = "def foo():\n    return 1\n"
        
        edit = EditBlock(
            file="test.py",
            search="def bar():",  # Wrong function name
            replace="def baz():",
            start_pos=0,
            end_pos=50
        )
        
        result = execute_edit(vfs, edit)
        
        assert result["success"] is False
        assert "not found" in result["error"].lower()
        # Should include fuzzy match info
        assert "similar" in result["error"].lower() or "diff" in result["error"].lower()


class TestExecuteEdits:
    """Test sequential edit execution."""

    def test_execute_multiple_edits(self):
        """Multiple edits execute in order"""
        from unittest.mock import MagicMock
        
        vfs = MagicMock()
        content = "aaa\nbbb\nccc"
        
        def fake_read(path):
            return content
        
        def fake_write(path, new_content):
            nonlocal content
            content = new_content
        
        vfs.read_file.side_effect = fake_read
        vfs.write_file.side_effect = fake_write
        
        edits = [
            EditBlock("test.py", "aaa", "AAA", 0, 50),
            EditBlock("test.py", "bbb", "BBB", 60, 110),
        ]
        
        results, failed_index = execute_edits(vfs, edits)
        
        assert failed_index is None
        assert len(results) == 2
        assert all(r["success"] for r in results)
        assert content == "AAA\nBBB\nccc"

    def test_execute_stops_on_first_failure(self):
        """Execution stops at first failed edit"""
        from unittest.mock import MagicMock
        
        vfs = MagicMock()
        vfs.read_file.return_value = "aaa\nbbb\nccc"
        
        edits = [
            EditBlock("test.py", "aaa", "AAA", 0, 50),
            EditBlock("test.py", "MISSING", "XXX", 60, 110),  # Will fail
            EditBlock("test.py", "ccc", "CCC", 120, 170),  # Should not run
        ]
        
        results, failed_index = execute_edits(vfs, edits)
        
        assert failed_index == 1
        assert len(results) == 2  # Only first two attempted
        assert results[0]["success"] is True
        assert results[1]["success"] is False


new text
</search>
<replace>
new text
</replace>
</edit>

I'll also update the context.'''
        
        # Parse inline commands - note: content uses literal angle brackets
        # We need to use actual angle brackets for the parser
        actual_content = content.replace('<', '<').replace('>', '>')
        commands = parse_inline_commands(actual_content)
        
        # The key invariant: if this edit fails, any tool_calls in the
        # LLM response should NOT be recorded. This is tested at the
        # integration level in ai_chat_widget._on_stream_finished:
        # 
        # 1. Process inline commands FIRST
        # 2. If any fail, return early (don't record tool_calls)
        # 3. Only record tool_calls if all inline commands succeed

    def test_inline_commands_parsed_in_order(self):
        """Inline commands should be parsed in document order."""
        from forge.tools.invocation import parse_inline_commands
        
        content = '<edit file="first.py">\n<search>a</search>\n<replace>b</replace>\n</edit>\n\n<commit message="First commit"/>\n\n<edit file="second.py">\n<search>c</search>\n<replace>d</replace>\n</edit>'
        
        # Unescape for actual parsing
        import html
        actual_content = html.unescape(content)
        commands = parse_inline_commands(actual_content)
        
        # Should find 3 commands in order
        assert len(commands) == 3
        assert commands[0].tool_name == "edit"
        assert commands[0].args.get("file") == "first.py"
        assert commands[1].tool_name == "commit"
        assert commands[2].tool_name == "edit"
        assert commands[2].args.get("file") == "second.py"
        
new text
</search>
<replace>
new text
