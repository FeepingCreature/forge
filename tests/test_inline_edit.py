"""
Tests for inline edit parsing and execution.

Covers the surgical-edit syntax (with self-closing <with/> separator) and
the <write> whole-file syntax, plus the nonced forms and code-block skipping.
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
    """Test surgical-edit block parsing from assistant text."""

    def test_parse_simple_replace(self):
        content = '''Here's the fix:

<replace file="test.py">
def foo():
    return 1
<with/>
def foo():
    return 2
</replace>

That should work!'''

        edits = parse_edits(content)

        assert len(edits) == 1
        assert edits[0].file == "test.py"
        assert edits[0].search == "def foo():\n    return 1"
        assert edits[0].replace == "def foo():\n    return 2"

    def test_parse_multiple_replaces(self):
        content = '''<replace file="a.py">
old_a
<with/>
new_a
</replace>

And also:

<replace file="b.py">
old_b
<with/>
new_b
</replace>'''

        edits = parse_edits(content)

        assert len(edits) == 2
        assert edits[0].file == "a.py"
        assert edits[1].file == "b.py"

    def test_parse_empty_replacement_means_deletion(self):
        """An empty replacement (nothing between <with/> and </replace>) deletes."""
        content = '''<replace file="test.py">
# Remove this comment
<with/>
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
            "a\n"
            "<with/>\n"
            "b\n"
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
    """Test handling of malformed surgical-edit syntax."""

    def test_does_not_match_across_replace_boundaries(self):
        """The non-greedy regex must not span two <replace> blocks."""
        content = '''<replace file="first.py">
aaa
<with/>
bbb
</replace>

Some text in between.

<replace file="second.py">
ccc
<with/>
ddd
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
foo
<with/>
bar

I forgot to close the replace tag.'''

        edits = parse_edits(content)
        assert len(edits) == 0

    def test_missing_with_separator(self):
        """A <replace>...</replace> with no separator should not match."""
        content = '''<replace file="test.py">
foo
no separator here at all
</replace>'''

        edits = parse_edits(content)
        assert len(edits) == 0

    def test_nested_angle_brackets_in_content(self):
        """Bodies containing HTML/XML tags should still parse correctly."""
        content = '''<replace file="template.html">
<div class="old">
  <span>text</span>
</div>
<with/>
<div class="new">
  <span>updated</span>
</div>
</replace>'''

        edits = parse_edits(content)

        assert len(edits) == 1
        assert '<div class="old">' in edits[0].search
        assert '<div class="new">' in edits[0].replace


# ---------------------------------------------------------------------------
# execute() — single replace against a VFS
# ---------------------------------------------------------------------------


class TestExecuteReplace:
    """Test execute() against a mock VFS for a single replace."""

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
        """An empty search is invalid for replace — must use <write>."""
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
            "old\n"
            "<with/>\n"
            "new\n"
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
            "old\n"
            "<with/>\n"
            "new\n"
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
            "old\n"
            "<with/>\n"
            "new\n"
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
    """Test the nonced surgical-edit form."""

    def test_simple_nonced_replace(self):
        content = (
            '<replace_abc123 file="test.py">\n'
            "old\n"
            "<with_abc123/>\n"
            "new\n"
            "</replace_abc123>"
        )
        edits = parse_edits(content)
        assert len(edits) == 1
        assert edits[0].file == "test.py"
        assert edits[0].search == "old"
        assert edits[0].replace == "new"

    def test_nonced_body_can_contain_replace_delimiters(self):
        """The whole point: a nonced body may contain </replace> and <with/>."""
        content = (
            '<replace_n1 file="parser_docs.md">\n'
            "Close with </replace>, split with <with/>.\n"
            "<with_n1/>\n"
            "Close with </replace_NONCE>, split with <with_NONCE/>.\n"
            "</replace_n1>"
        )
        edits = parse_edits(content)
        assert len(edits) == 1
        assert "</replace>" in edits[0].search
        assert "<with/>" in edits[0].search
        assert "</replace_NONCE>" in edits[0].replace
        assert "<with_NONCE/>" in edits[0].replace

    def test_mismatched_nonce_does_not_match(self):
        """replace_aaa with with_bbb/ should not match."""
        content = (
            '<replace_aaa file="test.py">\n'
            "x\n"
            "<with_bbb/>\n"
            "y\n"
            "</replace_aaa>"
        )
        edits = parse_edits(content)
        assert len(edits) == 0

    def test_plain_replace_does_not_pair_with_nonced_with(self):
        """A plain <replace> requires a plain <with/>, not <with_NONCE/>."""
        content = (
            '<replace file="test.py">\n'
            "x\n"
            "<with_zz/>\n"
            "y\n"
            "</replace>"
        )
        edits = parse_edits(content)
        assert len(edits) == 0

    def test_nonced_and_plain_in_same_message(self):
        content = (
            '<replace file="a.py">\n'
            "foo\n"
            "<with/>\n"
            "bar\n"
            "</replace>\n"
            "\n"
            "And a tricky one:\n"
            '<replace_z file="b.md">\n'
            "use </replace> and <with/>\n"
            "<with_z/>\n"
            "use </replace_NONCE>\n"
            "</replace_z>"
        )
        edits = parse_edits(content)
        assert len(edits) == 2
        assert edits[0].file == "a.py"
        assert edits[1].file == "b.md"
        assert "</replace>" in edits[1].search
        assert "<with/>" in edits[1].search


# ---------------------------------------------------------------------------
# Streaming preview parser (tool_rendering)
# ---------------------------------------------------------------------------


class TestStreamingPartialReplace:
    """Test the streaming partial-replace renderer."""

    def test_partial_plain_replace_search_phase(self):
        from forge.ui.tool_rendering import _render_partial_replace

        # Mid-stream: opened <replace>, streaming the "to find" half
        content = '<replace file="foo.py">\nold cont'
        rendered = _render_partial_replace(content)
        assert rendered is not None
        assert "foo.py" in rendered
        assert "old cont" in rendered

    def test_partial_nonced_replace_search_phase(self):
        from forge.ui.tool_rendering import _render_partial_replace

        # Mid-stream nonced replace: still receiving the "to find" half,
        # body contains a literal </replace which must NOT terminate the block
        content = '<replace_x9 file="parser.py">\nclose with </replace'
        rendered = _render_partial_replace(content)
        assert rendered is not None
        assert "parser.py" in rendered
        assert "close with" in rendered

    def test_partial_nonced_replace_after_separator(self):
        from forge.ui.tool_rendering import _render_partial_replace

        # Mid-stream nonced replace: separator seen, mid-replacement
        content = (
            '<replace_abc file="docs.md">\n'
            "old </replace> text\n"
            "<with_abc/>\n"
            "new </replace> tex"
        )
        rendered = _render_partial_replace(content)
        assert rendered is not None
        assert "docs.md" in rendered

    def test_partial_full_block_pre_close(self):
        """Replacement complete, but </replace_NONCE> not yet streamed."""
        from forge.ui.tool_rendering import _render_partial_replace

        content = (
            '<replace_z file="x.py">\n'
            "old\n"
            "<with_z/>\n"
            "new\n"
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


class TestParseCheckFailureContract:
    """Regression tests for execute_inline_commands_with_parse_check.

    Original bug: when parse-check found unparsed blocks, the function returned
    (results=[synthetic_error], failed_index=0). The consumer
    (LiveSession._on_inline_commands_finished) used commands[0] to position
    the error, which mis-attributed the error to the first SUCCESSFULLY parsed
    command, and never executed any of the parsed commands.

    Fix: parse-check failures use PARSE_CHECK_FAILED (-1) as the sentinel so
    consumers can distinguish them from execution failures.
    """

    def test_parse_check_failure_uses_sentinel(self):
        from forge.tools.invocation import (
            PARSE_CHECK_FAILED,
            execute_inline_commands_with_parse_check,
            parse_inline_commands,
        )

        # First edit block is well-formed and parses. Second is malformed
        # (missing close tag) so parse-check should reject the whole batch.
        good = (
            '<replace_t1 file="good.py">\n'
            "foo\n"
            "<with_t1/>\n"
            "bar\n"
            "</replace_t1>"
        )
        broken = (
            '<replace_t2 file="broken.py">\n'
            "baz\n"
            "<with_t2/>\n"
            "qux\n"
            "(missing close tag here)\n"
        )
        content = good + "\n\nThen a broken one:\n" + broken

        commands = parse_inline_commands(content)
        # The good block parses; the broken one does not.
        assert len(commands) == 1
        assert commands[0].args["filepath"] == "good.py"

        vfs = MagicMock()
        vfs.read_file.return_value = "foo\n"

        results, failed_index = execute_inline_commands_with_parse_check(
            vfs, content, commands
        )

        # Critical: failed_index must be the sentinel, NOT 0.
        # If this is 0, the consumer will mis-attribute the error to commands[0]
        # (the *successful* parse) instead of to the unparsed block.
        assert failed_index == PARSE_CHECK_FAILED
        assert failed_index != 0

        # No command was executed: VFS.write_file was never called even though
        # commands[0] would have succeeded if executed.
        vfs.write_file.assert_not_called()

        # The synthetic error is in results[0] and mentions the broken block.
        assert len(results) == 1
        assert results[0]["success"] is False
        assert "broken.py" in results[0]["error"]
        assert "failed to parse" in results[0]["error"]

    def test_all_succeed_returns_none(self):
        from forge.tools.invocation import (
            execute_inline_commands_with_parse_check,
            parse_inline_commands,
        )

        content = (
            '<replace_t3 file="x.py">\n'
            "foo\n"
            "<with_t3/>\n"
            "bar\n"
            "</replace_t3>"
        )
        commands = parse_inline_commands(content)
        assert len(commands) == 1

        vfs = MagicMock()
        vfs.read_file.return_value = "foo\n"

        results, failed_index = execute_inline_commands_with_parse_check(
            vfs, content, commands
        )

        assert failed_index is None
        assert len(results) == 1
        assert results[0]["success"] is True

    def test_sentinel_value_is_distinct_from_valid_indices(self):
        from forge.tools.invocation import PARSE_CHECK_FAILED

        # Must be negative so it can never equal a valid list index.
        assert PARSE_CHECK_FAILED < 0


class TestUnparsedBlockDetection:
    """Test that malformed edit blocks surface as parse errors."""

    def test_detects_orphan_replace_open_tag(self):
        content = (
            '<replace_t4 file="oops.py">\n'
            "foo\n"
            "<with_t4/>\n"
            "bar\n"
            "I forgot to close.\n"
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
            "foo\n"
            "<with/>\n"
            "bar\n"
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