"""
Tests for inline edit parsing and execution.

Covers the new <replace>/<old>/<new> surgical-edit syntax and the
<write> whole-file syntax, plus the nonced forms and code-block skipping.
"""

from unittest.mock import MagicMock

from forge.tools.builtin.edit import (
    EditBlock,
    detect_unparsed_edit_blocks,
    execute,
    execute_write,
    parse_edits,
)


# ---------------------------------------------------------------------------
# <replace> parsing
# ---------------------------------------------------------------------------


class TestParseReplace:
    """Test <replace>/<old>/<new> block parsing from assistant message text."""

    def test_parse_simple_replace(self):
        content = '''Here's the fix:

<replace file="test.py">
<old>
def foo():
    return 1
</old>
<new>
def foo():
    return 2
</new>
</replace>

That should work!'''

        edits = parse_edits(content)

        assert len(edits) == 1
        assert edits[0].file == "test.py"
        assert edits[0].search == "def foo():\n    return 1"
        assert edits[0].replace == "def foo():\n    return 2"

    def test_parse_multiple_replaces(self):
        content = '''<replace file="a.py">
<old>
old_a
</old>
<new>
new_a
</new>
</replace>

And also:

<replace file="b.py">
<old>
old_b
</old>
<new>
new_b
</new>
</replace>'''

        edits = parse_edits(content)

        assert len(edits) == 2
        assert edits[0].file == "a.py"
        assert edits[1].file == "b.py"

    def test_parse_empty_new_means_deletion(self):
        """An empty <new> block means delete the matched text."""
        content = '''<replace file="test.py">
<old>
# Remove this comment
</old>
<new>
</new>
</replace>'''

        edits = parse_edits(content)

        assert len(edits) == 1
        assert edits[0].search == "# Remove this comment"
        assert edits[0].replace == ""

    def test_replace_positions_tracked(self):
        """Edit blocks should track their position for truncation on failure."""
        content = (
            "Some text\n"
            '<replace file="x.py">\n'
            "<old>\na\n</old>\n"
            "<new>\nb\n</new>\n"
            "</replace>\n"
            "More text"
        )

        edits = parse_edits(content)

        assert len(edits) == 1
        assert edits[0].start_pos == 10  # After "Some text\n"
        chunk = content[edits[0].start_pos : edits[0].end_pos]
        assert chunk.startswith("<replace")
        assert chunk.endswith("</replace>")


# ---------------------------------------------------------------------------
# Malformed input
# ---------------------------------------------------------------------------


class TestMalformedReplace:
    """Test handling of malformed <replace> syntax."""

    def test_does_not_match_across_replace_boundaries(self):
        """The non-greedy regex must not span two <replace> blocks."""
        content = '''<replace file="first.py">
<old>
aaa
</old>
<new>
bbb
</new>
</replace>

Some text in between.

<replace file="second.py">
<old>
ccc
</old>
<new>
ddd
</new>
</replace>'''

        edits = parse_edits(content)

        assert len(edits) == 2
        assert edits[0].file == "first.py"
        assert edits[0].replace == "bbb"
        assert edits[1].file == "second.py"
        assert edits[1].replace == "ddd"

    def test_missing_closing_replace_tag(self):
        """A <replace> with no </replace> should not match."""
        content = '''<replace file="test.py">
<old>
foo
</old>
<new>
bar
</new>

I forgot to close the replace tag.'''

        edits = parse_edits(content)
        assert len(edits) == 0

    def test_missing_new_block(self):
        """A <replace> without <new>...</new> should not match."""
        content = '''<replace file="test.py">
<old>
foo
</old>
I forgot the new section
</replace>'''

        edits = parse_edits(content)
        assert len(edits) == 0

    def test_nested_angle_brackets_in_content(self):
        """Bodies containing HTML/XML tags should still parse correctly."""
        content = '''<replace file="template.html">
<old>
<div class="old">
  <span>text</span>
</div>
</old>
<new>
<div class="new">
  <span>updated</span>
</div>
</new>
</replace>'''

        edits = parse_edits(content)

        assert len(edits) == 1
        assert '<div class="old">' in edits[0].search
        assert '<div class="new">' in edits[0].replace


# ---------------------------------------------------------------------------
# execute() — single replace against a VFS
# ---------------------------------------------------------------------------


class TestExecuteReplace:
    """Test execute() against a mock VFS for a single <replace>."""

    def test_execute_simple_replace(self):
        vfs = MagicMock()
        vfs.read_file.return_value = "def foo():\n    return 1\n"

        result = execute(
            vfs,
            {"filepath": "test.py", "search": "return 1", "replace": "return 2"},
        )

        assert result["success"] is True
        vfs.write_file.assert_called_once()
        new_content = vfs.write_file.call_args[0][1]
        assert "return 2" in new_content
        assert "return 1" not in new_content

    def test_execute_file_not_found(self):
        vfs = MagicMock()
        vfs.read_file.side_effect = FileNotFoundError("No such file")

        result = execute(
            vfs,
            {"filepath": "nope.py", "search": "foo", "replace": "bar"},
        )

        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_execute_search_not_found_returns_diff(self):
        vfs = MagicMock()
        vfs.read_file.return_value = "def foo():\n    return 1\n"

        result = execute(
            vfs,
            {"filepath": "test.py", "search": "def bar():", "replace": "def baz():"},
        )

        assert result["success"] is False
        assert "not found" in result["error"].lower()
        # Diagnostic diff should be included
        assert "diff" in result

    def test_execute_missing_filepath(self):
        vfs = MagicMock()
        result = execute(vfs, {"filepath": "", "search": "x", "replace": "y"})
        assert result["success"] is False
        assert "filepath" in result["error"].lower()

    def test_execute_empty_search_rejected(self):
        """An empty search text on <replace> is invalid — must use <write>."""
        vfs = MagicMock()
        vfs.read_file.return_value = "anything"

        result = execute(
            vfs, {"filepath": "test.py", "search": "", "replace": "new"}
        )
        assert result["success"] is False
        assert "search" in result["error"].lower()


# ---------------------------------------------------------------------------
# execute_write() — whole-file writes
# ---------------------------------------------------------------------------


class TestExecuteWrite:
    """Test execute_write() against a mock VFS."""

    def test_write_creates_new_file(self):
        vfs = MagicMock()
        vfs.read_file.side_effect = FileNotFoundError("nope")

        result = execute_write(
            vfs, {"filepath": "new.py", "content": "print('hi')\n"}
        )

        assert result["success"] is True
        assert result["created"] is True
        vfs.write_file.assert_called_once_with("new.py", "print('hi')\n")

    def test_write_overwrites_existing_file(self):
        vfs = MagicMock()
        vfs.read_file.return_value = "old content"

        result = execute_write(
            vfs, {"filepath": "existing.py", "content": "new content"}
        )

        assert result["success"] is True
        assert result["created"] is False
        vfs.write_file.assert_called_once_with("existing.py", "new content")

    def test_write_missing_filepath(self):
        vfs = MagicMock()
        result = execute_write(vfs, {"filepath": "", "content": "x"})
        assert result["success"] is False


# ---------------------------------------------------------------------------
# Code-block skipping at the parser layer
# ---------------------------------------------------------------------------


class TestCodeBlockSkipping:
    """Test that inline commands inside code blocks are NOT parsed."""

    def test_fenced_code_block_replace_ignored(self):
        from forge.tools.invocation import parse_inline_commands

        content = (
            "Here's an example:\n\n"
            "```\n"
            '<replace file="test.py">\n'
            "<old>\nold\n</old>\n"
            "<new>\nnew\n</new>\n"
            "</replace>\n"
            "```\n\n"
            "That was just an example."
        )

        commands = parse_inline_commands(content)
        assert len(commands) == 0

    def test_tilde_fenced_code_block_ignored(self):
        from forge.tools.invocation import parse_inline_commands

        content = (
            "Example:\n\n"
            "~~~\n"
            '<replace file="test.py">\n'
            "<old>\nold\n</old>\n"
            "<new>\nnew\n</new>\n"
            "</replace>\n"
            "~~~\n\n"
            "Done."
        )

        commands = parse_inline_commands(content)
        assert len(commands) == 0

    def test_inline_backtick_code_ignored(self):
        from forge.tools.invocation import parse_inline_commands

        content = 'Use `<commit message="fix"/>` to commit your changes.'

        commands = parse_inline_commands(content)
        assert len(commands) == 0

    def test_real_command_outside_code_block_works(self):
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
        from forge.tools.invocation import parse_inline_commands

        content = (
            "Example:\n\n"
            "```python\n"
            '<replace file="test.py">\n'
            "<old>\nold\n</old>\n"
            "<new>\nnew\n</new>\n"
            "</replace>\n"
            "```\n"
        )

        commands = parse_inline_commands(content)
        assert len(commands) == 0

    def test_write_block_in_fence_ignored(self):
        from forge.tools.invocation import parse_inline_commands

        content = (
            "Example whole-file write:\n\n"
            "```\n"
            '<write file="new.py">\n'
            "print('hi')\n"
            "</write>\n"
            "```\n"
        )

        commands = parse_inline_commands(content)
        assert len(commands) == 0


# ---------------------------------------------------------------------------
# Nonced syntax
# ---------------------------------------------------------------------------


class TestNoncedReplace:
    """Test the nonced <replace_NONCE>/<old_NONCE>/<new_NONCE> form."""

    def test_simple_nonced_replace(self):
        content = (
            '<replace_abc123 file="test.py">\n'
            "<old_abc123>\n"
            "old\n"
            "</old_abc123>\n"
            "<new_abc123>\n"
            "new\n"
            "</new_abc123>\n"
            "</replace_abc123>"
        )
        edits = parse_edits(content)
        assert len(edits) == 1
        assert edits[0].file == "test.py"
        assert edits[0].search == "old"
        assert edits[0].replace == "new"

    def test_nonced_body_can_contain_replace_delimiters(self):
        """The whole point: a nonced body may contain </replace>, </old>, etc."""
        content = (
            '<replace_n1 file="parser_docs.md">\n'
            "<old_n1>\n"
            "Close with </replace> and </old>.\n"
            "</old_n1>\n"
            "<new_n1>\n"
            "Close with </replace_NONCE> or </replace>.\n"
            "</new_n1>\n"
            "</replace_n1>"
        )
        edits = parse_edits(content)
        assert len(edits) == 1
        assert "</replace>" in edits[0].search
        assert "</old>" in edits[0].search
        assert "</replace_NONCE>" in edits[0].replace

    def test_mismatched_nonce_does_not_match(self):
        """replace_aaa with old_bbb should not match."""
        content = (
            '<replace_aaa file="test.py">\n'
            "<old_bbb>\n"
            "x\n"
            "</old_bbb>\n"
            "<new_bbb>\n"
            "y\n"
            "</new_bbb>\n"
            "</replace_aaa>"
        )
        edits = parse_edits(content)
        assert len(edits) == 0

    def test_nonced_and_plain_in_same_message(self):
        content = (
            '<replace file="a.py">\n'
            "<old>\nfoo\n</old>\n"
            "<new>\nbar\n</new>\n"
            "</replace>\n"
            "\n"
            "And a tricky one:\n"
            '<replace_z file="b.md">\n'
            "<old_z>\nuse </replace>\n</old_z>\n"
            "<new_z>\nuse </replace_NONCE>\n</new_z>\n"
            "</replace_z>"
        )
        edits = parse_edits(content)
        assert len(edits) == 2
        # parse_edits returns in source order
        assert edits[0].file == "a.py"
        assert edits[1].file == "b.md"
        assert "</replace>" in edits[1].search


# ---------------------------------------------------------------------------
# Streaming preview parser (tool_rendering)
# ---------------------------------------------------------------------------


class TestStreamingPartialReplace:
    """Test the streaming partial-replace renderer."""

    def test_partial_plain_replace_search_phase(self):
        from forge.ui.tool_rendering import _render_partial_replace

        # Mid-stream: opened <replace>, partial <old> body
        content = '<replace file="foo.py">\n<old>\nold cont'
        rendered = _render_partial_replace(content)
        assert rendered is not None
        assert "foo.py" in rendered
        assert "old cont" in rendered

    def test_partial_nonced_replace_old_phase(self):
        from forge.ui.tool_rendering import _render_partial_replace

        # Mid-stream nonced replace: still receiving the old body
        content = '<replace_x9 file="parser.py">\n<old_x9>\nclose with </replace'
        rendered = _render_partial_replace(content)
        assert rendered is not None
        assert "parser.py" in rendered
        # The body contains a literal </replace which must NOT terminate the block
        assert "close with" in rendered

    def test_partial_nonced_replace_new_phase(self):
        from forge.ui.tool_rendering import _render_partial_replace

        # Mid-stream nonced replace: old complete, mid-new
        content = (
            '<replace_abc file="docs.md">\n'
            "<old_abc>\nold </replace> text\n</old_abc>\n"
            "<new_abc>\nnew </replace> tex"
        )
        rendered = _render_partial_replace(content)
        assert rendered is not None
        assert "docs.md" in rendered
        assert "old" in rendered
        assert "new" in rendered

    def test_partial_full_block_pre_close(self):
        """new body complete, but </replace_NONCE> not yet streamed."""
        from forge.ui.tool_rendering import _render_partial_replace

        content = (
            '<replace_z file="x.py">\n'
            "<old_z>\nold\n</old_z>\n"
            "<new_z>\nnew\n</new_z>\n"
        )
        rendered = _render_partial_replace(content)
        assert rendered is not None
        assert "x.py" in rendered

    def test_no_replace_returns_none(self):
        from forge.ui.tool_rendering import _render_partial_replace

        assert _render_partial_replace("just some prose") is None
        assert _render_partial_replace("") is None


class TestStreamingPartialWrite:
    """Test the streaming partial-write renderer."""

    def test_partial_write_with_body(self):
        from forge.ui.tool_rendering import _render_partial_write

        content = '<write file="new.py">\nprint("hello'
        rendered = _render_partial_write(content)
        assert rendered is not None
        assert "new.py" in rendered
        assert "hello" in rendered

    def test_partial_nonced_write(self):
        from forge.ui.tool_rendering import _render_partial_write

        content = (
            '<write_q42 file="example.md">\n'
            "This file documents </replace> and </write> with no escaping needed.\n"
        )
        rendered = _render_partial_write(content)
        assert rendered is not None
        assert "example.md" in rendered

    def test_no_write_returns_none(self):
        from forge.ui.tool_rendering import _render_partial_write

        assert _render_partial_write("just some prose") is None
        assert _render_partial_write("") is None


# ---------------------------------------------------------------------------
# Unparsed-block detection
# ---------------------------------------------------------------------------


class TestUnparsedBlockDetection:
    """Test that malformed <replace>/<write> blocks surface as parse errors."""

    def test_detects_orphan_replace_open_tag(self):
        content = (
            '<replace file="oops.py">\n'
            "<old>\nfoo\n</old>\n"
            "I forgot the new and the close.\n"
        )
        unparsed = detect_unparsed_edit_blocks(content, parsed_spans=[])
        assert len(unparsed) == 1
        assert unparsed[0][0] == 0
        assert "oops.py" in unparsed[0][1]

    def test_detects_orphan_write_open_tag(self):
        content = (
            '<write file="oops.py">\n'
            "print('hi')\n"
            "I forgot the close.\n"
        )
        unparsed = detect_unparsed_edit_blocks(content, parsed_spans=[])
        assert len(unparsed) == 1
        assert "oops.py" in unparsed[0][1]

    def test_successful_parse_is_not_flagged(self):
        content = (
            '<replace file="ok.py">\n'
            "<old>\nfoo\n</old>\n"
            "<new>\nbar\n</new>\n"
            "</replace>"
        )
        edits = parse_edits(content)
        assert len(edits) == 1
        spans = [(e.start_pos, e.end_pos) for e in edits]
        unparsed = detect_unparsed_edit_blocks(content, parsed_spans=spans)
        assert unparsed == []


# ---------------------------------------------------------------------------
# EditBlock dataclass sanity
# ---------------------------------------------------------------------------


class TestEditBlockDataclass:
    def test_construct_and_compare(self):
        b = EditBlock(file="a.py", search="x", replace="y", start_pos=0, end_pos=10)
        assert b.file == "a.py"
        assert b.search == "x"
        assert b.replace == "y"
        assert b.start_pos == 0
        assert b.end_pos == 10