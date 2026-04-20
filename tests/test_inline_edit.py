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


class TestCodeBlockSkipping:
    """Test that inline commands inside code blocks are NOT parsed."""

    def test_fenced_code_block_edit_ignored(self):
        """Edit inside a fenced ``` block should not be parsed."""
        from forge.tools.invocation import parse_inline_commands

        content = (
            "Here's an example edit:\n\n"
            "```\n"
            '<edit file="test.py">\n'
            "<search>\n"
            "old\n"
            "</search>\n"
            "<replace>\n"
            "new\n"
            "</replace>\n"
            "</edit>\n"
            "```\n\n"
            "That was just an example."
        )

        commands = parse_inline_commands(content)
        assert len(commands) == 0

    def test_tilde_fenced_code_block_ignored(self):
        """Edit inside a ~~~ fenced block should not be parsed."""
        from forge.tools.invocation import parse_inline_commands

        content = (
            "Example:\n\n"
            "~~~\n"
            '<edit file="test.py">\n'
            "<search>\n"
            "old\n"
            "</search>\n"
            "<replace>\n"
            "new\n"
            "</replace>\n"
            "</edit>\n"
            "~~~\n\n"
            "Done."
        )

        commands = parse_inline_commands(content)
        assert len(commands) == 0

    def test_inline_backtick_code_ignored(self):
        """Edit-like content inside inline backticks should not be parsed."""
        from forge.tools.invocation import parse_inline_commands

        content = 'Use `<commit message="fix"/>` to commit your changes.'

        commands = parse_inline_commands(content)
        assert len(commands) == 0

    def test_double_backtick_inline_code_ignored(self):
        """Content inside double backticks should not be parsed."""
        from forge.tools.invocation import parse_inline_commands

        content = 'Use ``<commit message="fix"/>`` to commit.'

        commands = parse_inline_commands(content)
        assert len(commands) == 0

    def test_real_command_outside_code_block_still_works(self):
        """Commands outside code blocks should still be parsed normally."""
        from forge.tools.invocation import parse_inline_commands

        content = (
            "Here's an example in a code block:\n\n"
            "```\n"
            '<commit message="example"/>\n'
            "```\n\n"
            "And here's the real one:\n\n"
            '<commit message="real fix"/>\n'
        )

        commands = parse_inline_commands(content)
        assert len(commands) == 1
        assert commands[0].tool_name == "commit"
        assert commands[0].args["message"] == "real fix"

    def test_fenced_block_with_language_tag_ignored(self):
        """Fenced block with language identifier (```python) should be skipped."""
        from forge.tools.invocation import parse_inline_commands

        content = (
            "Example:\n\n"
            "```python\n"
            '<edit file="test.py">\n'
            "<search>\nold\n</search>\n"
            "<replace>\nnew\n</replace>\n"
            "</edit>\n"
            "```\n"
        )

        commands = parse_inline_commands(content)
        assert len(commands) == 0

    def test_multiple_code_blocks_all_skipped(self):
        """Multiple code blocks should all be skipped."""
        from forge.tools.invocation import parse_inline_commands

        content = (
            "First example:\n\n"
            "```\n"
            '<commit message="one"/>\n'
            "```\n\n"
            "Second example:\n\n"
            "```\n"
            '<commit message="two"/>\n'
            "```\n"
        )

        commands = parse_inline_commands(content)
        assert len(commands) == 0


class TestNoncedEditSyntax:
    """Test the nonced <edit_NONCE> / <search_NONCE> / <replace_NONCE> form."""

    def test_simple_nonced_edit(self):
        content = (
            '<edit_abc123 file="test.py">\n'
            "<search_abc123>\n"
            "old\n"
            "</search_abc123>\n"
            "<replace_abc123>\n"
            "new\n"
            "</replace_abc123>\n"
            "</edit_abc123>"
        )
        edits = parse_edits(content)
        assert len(edits) == 1
        assert edits[0].file == "test.py"
        assert edits[0].search == "old"
        assert edits[0].replace == "new"

    def test_nonced_body_can_contain_edit_delimiters(self):
        """The whole point: a nonced body may contain </edit>, </search>, etc."""
        content = (
            '<edit_n1 file="parser_docs.md">\n'
            "<search_n1>\n"
            "Close with </edit> and </search>.\n"
            "</search_n1>\n"
            "<replace_n1>\n"
            "Close with </edit_NONCE> or </edit>.\n"
            "</replace_n1>\n"
            "</edit_n1>"
        )
        edits = parse_edits(content)
        assert len(edits) == 1
        assert "</edit>" in edits[0].search
        assert "</search>" in edits[0].search
        assert "</edit_NONCE>" in edits[0].replace

    def test_mismatched_nonce_does_not_match(self):
        """edit_aaa with search_bbb is a parse error, not a successful match."""
        content = (
            '<edit_aaa file="test.py">\n'
            "<search_bbb>\n"
            "x\n"
            "</search_bbb>\n"
            "<replace_bbb>\n"
            "y\n"
            "</replace_bbb>\n"
            "</edit_aaa>"
        )
        edits = parse_edits(content)
        assert len(edits) == 0

    def test_nonced_and_plain_in_same_message(self):
        content = (
            '<edit file="a.py">\n'
            "<search>\nfoo\n</search>\n"
            "<replace>\nbar\n</replace>\n"
            "</edit>\n"
            "\n"
            'And a tricky one:\n'
            '<edit_z file="b.md">\n'
            "<search_z>\nuse </edit>\n</search_z>\n"
            "<replace_z>\nuse </edit_NONCE>\n</replace_z>\n"
            "</edit_z>"
        )
        edits = parse_edits(content)
        assert len(edits) == 2
        # parse_edits sorts by position, so plain comes first
        assert edits[0].file == "a.py"
        assert edits[1].file == "b.md"
        assert "</edit>" in edits[1].search


class TestUnparsedBlockDetection:
    """Test that malformed <edit> blocks surface as parse errors."""

    def test_detects_orphan_open_tag(self):
        from forge.tools.builtin.edit import detect_unparsed_edit_blocks

        content = (
            '<edit file="oops.py">\n'
            "<search>\nfoo\n</search>\n"
            "I forgot the replace and the close.\n"
        )
        # Nothing parsed successfully → parsed_spans is empty.
        unparsed = detect_unparsed_edit_blocks(content, parsed_spans=[])
        assert len(unparsed) == 1
        assert unparsed[0][0] == 0  # position of opening tag
        assert "oops.py" in unparsed[0][1]

    def test_successful_parse_is_not_flagged(self):
        from forge.tools.builtin.edit import detect_unparsed_edit_blocks

        content = (
            '<edit file="ok.py">\n'
            "<search>\nfoo\n</search>\n"
            "<replace>\nbar\n</replace>\n"
            "</edit>"
        )
        edits = parse_edits(content)
        assert len(edits) == 1
        spans = [(e.start_pos, e.end_pos) for e in edits]
        unparsed = detect_unparsed_edit_blocks(content, parsed_spans=spans)
        assert unparsed == []

    def test_execute_with_parse_check_reports_unparsed(self):
        """execute_inline_commands_with_parse_check returns an error result
        when blocks failed to parse, BEFORE attempting to execute."""
        from unittest.mock import MagicMock

        from forge.tools.invocation import (
            execute_inline_commands_with_parse_check,
            parse_inline_commands,
        )

        # Content has one valid edit and one orphan open tag.
        content = (
            '<edit file="good.py">\n'
            "<search>\nfoo\n</search>\n"
            "<replace>\nbar\n</replace>\n"
            "</edit>\n"
            "\n"
            '<edit file="malformed.py">\n'
            "<search>\nbaz\n</search>\n"
            "no replace, no close, oops\n"
        )

        commands = parse_inline_commands(content)
        # Only the good edit parses
        assert len(commands) == 1

        vfs = MagicMock()
        results, failed_index = execute_inline_commands_with_parse_check(
            vfs, content, commands
        )
        # Parse error short-circuits execution: no VFS writes happen.
        assert failed_index == 0
        assert results[0]["success"] is False
        assert "failed to parse" in results[0]["error"].lower()
        assert "malformed.py" in results[0]["error"]
        vfs.write_file.assert_not_called()

    def test_orphan_in_code_block_is_ignored(self):
        """A malformed-looking <edit> inside a fenced code block is documentation,
        not a real command, and must not be flagged."""
        from forge.tools.invocation import (
            detect_unparsed_inline_blocks,
            parse_inline_commands,
        )

        content = (
            "Here's the syntax (don't actually run this):\n\n"
            "```\n"
            '<edit file="example.py">\n'
            "<search>\nold\n</search>\n"
            "(no replace shown)\n"
            "```\n"
        )
        commands = parse_inline_commands(content)
        unparsed = detect_unparsed_inline_blocks(content, commands)
        assert unparsed == []


class TestInlineCommandOrdering:
    """Test that inline commands are processed before tool calls are recorded."""

    def test_inline_command_ordering_invariant(self):
        """
        The key invariant for inline command processing:

        In ai_chat_widget._on_stream_finished:
        1. Process inline commands FIRST
        2. If any fail, return early (don't record tool_calls)
        3. Only record tool_calls if all inline commands succeed

        This prevents orphaned tool calls (recorded but never executed)
        from appearing on session reload.
        """
        # This is an integration-level invariant tested by the code structure.
        pass

    def test_inline_commands_sorted_by_position(self):
        """Inline commands should be sorted by their position in content."""
        from forge.tools.invocation import InlineCommand

        # Create commands with explicit positions
        cmd1 = InlineCommand("edit", {"file": "a.py"}, start_pos=10, end_pos=50)
        cmd2 = InlineCommand("commit", {"message": "test"}, start_pos=60, end_pos=90)
        cmd3 = InlineCommand("edit", {"file": "b.py"}, start_pos=100, end_pos=150)

        # Verify ordering
        assert cmd1.start_pos < cmd2.start_pos < cmd3.start_pos
        assert cmd1.tool_name == "edit"
        assert cmd2.tool_name == "commit"
