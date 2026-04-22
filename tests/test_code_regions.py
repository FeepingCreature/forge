"""
Tests for code-region detection in inline command parsing.

The inline command parser (`parse_inline_commands`) skips commands that
appear inside fenced code blocks (``` or ~~~) and inline backtick spans.
This file tests the underlying _build_code_regions function and the
end-to-end behavior, with particular focus on nested-fence edge cases
that previously caused commands to leak out of "protected" prose.
"""

import pytest


class TestCodeRegions:
    """Test _build_code_regions directly."""

    def test_simple_fenced_block(self):
        from forge.tools.invocation import _build_code_regions

        content = "before\n```\ninside\n```\nafter\n"
        regions = _build_code_regions(content)
        assert len(regions) == 1
        start, end = regions[0]
        # Region should cover the open fence through the close fence
        assert content[start:end].startswith("```")
        assert content[start:end].rstrip().endswith("```")
        assert "inside" in content[start:end]

    def test_tilde_fence(self):
        from forge.tools.invocation import _build_code_regions

        content = "before\n~~~\ninside\n~~~\nafter\n"
        regions = _build_code_regions(content)
        assert len(regions) == 1

    def test_inline_backticks(self):
        from forge.tools.invocation import _build_code_regions

        content = "use `the foo()` function"
        regions = _build_code_regions(content)
        assert len(regions) == 1
        start, end = regions[0]
        assert content[start:end] == "`the foo()`"

    def test_double_inline_backticks(self):
        from forge.tools.invocation import _build_code_regions

        content = "use ``foo `bar` baz`` here"
        regions = _build_code_regions(content)
        assert len(regions) == 1
        start, end = regions[0]
        assert content[start:end] == "``foo `bar` baz``"

    def test_tilde_open_not_closed_by_backtick(self):
        """REGRESSION: tilde fence must not close on triple-backtick.

        Old regex matched (~~~|```).*?(~~~|```) which let any fence type
        close any other type. With nested fences, the outer ~~~ would
        close prematurely on an inner ```, exposing content.
        """
        from forge.tools.invocation import _build_code_regions

        content = (
            "~~~outer\n"
            "explanation here\n"
            "```\n"
            "EXPOSED CONTENT\n"
            "```\n"
            "still inside outer\n"
            "~~~\n"
        )
        regions = _build_code_regions(content)
        # The whole outer ~~~ block must be one region containing everything.
        # In particular, "EXPOSED CONTENT" must be inside a region.
        exposed_pos = content.index("EXPOSED CONTENT")
        in_region = any(s <= exposed_pos < e for s, e in regions)
        assert in_region, f"Position {exposed_pos} not in any region {regions}"

    def test_backtick_open_not_closed_by_tilde(self):
        """Symmetric: backtick fence not closed by tilde."""
        from forge.tools.invocation import _build_code_regions

        content = (
            "```\n"
            "EXPOSED\n"
            "~~~\n"
            "ALSO EXPOSED\n"
            "```\n"
        )
        regions = _build_code_regions(content)
        for marker in ("EXPOSED", "ALSO EXPOSED"):
            pos = content.index(marker)
            in_region = any(s <= pos < e for s, e in regions)
            assert in_region, f"{marker!r} at {pos} not in {regions}"

    def test_inner_fence_with_fewer_markers(self):
        """CommonMark: 4-backtick fence is NOT closed by 3-backtick fences."""
        from forge.tools.invocation import _build_code_regions

        content = (
            "````\n"
            "```\n"
            "DEEP\n"
            "```\n"
            "````\n"
        )
        regions = _build_code_regions(content)
        deep_pos = content.index("DEEP")
        in_region = any(s <= deep_pos < e for s, e in regions)
        assert in_region, f"DEEP at {deep_pos} not in {regions}"

    def test_unterminated_fence_covers_to_end(self):
        """Open fence with no close: protect everything after it."""
        from forge.tools.invocation import _build_code_regions

        content = "before\n```\nNEVER CLOSED\nstill open\n"
        regions = _build_code_regions(content)
        # There should be one region covering from ``` to end of content
        assert len(regions) == 1
        start, end = regions[0]
        assert content[start:].startswith("```")
        assert end == len(content)

    def test_indented_fence_up_to_three_spaces(self):
        """CommonMark: fences may have 0-3 leading spaces."""
        from forge.tools.invocation import _build_code_regions

        content = "   ```\nINSIDE\n   ```\n"
        regions = _build_code_regions(content)
        assert len(regions) == 1

    def test_four_space_indent_is_not_a_fence(self):
        """4 leading spaces means the line is not a valid fence opener.

        We don't enforce strict CommonMark indented-code detection. The
        inline-backtick scanner may still match the two triple-backtick
        runs as one inline span across the newlines, which is MORE
        protection (any inline command on the middle line gets skipped),
        not less. The important property is the safety direction: a
        command on the middle line must not leak through.
        """
        from forge.tools.invocation import parse_inline_commands

        # Use a real inline command on the middle line; it must NOT parse.
        content = (
            "    ```\n"
            'XCOMMITX message="should not run"X\n'
            "    ```\n"
        ).replace("XCOMMITX", "&lt;commit").replace('"X', '"/&gt;')
        commands = parse_inline_commands(content)
        assert len(commands) == 0

    def test_multiple_separate_fenced_blocks(self):
        from forge.tools.invocation import _build_code_regions

        content = (
            "first:\n```\nA\n```\n"
            "second:\n```\nB\n```\n"
        )
        regions = _build_code_regions(content)
        # Two separate fenced regions
        fenced = [(s, e) for s, e in regions if "\n" in content[s:e]]
        assert len(fenced) == 2

    def test_inline_span_outside_fence(self):
        """Inline backticks outside any fence are detected as their own regions."""
        from forge.tools.invocation import _build_code_regions

        content = "before `inline` then\n```\nfenced\n```\nafter `more`"
        regions = _build_code_regions(content)
        # Should have two inline spans + one fenced region = 3 regions
        assert len(regions) == 3


class TestCommandSkipping:
    """End-to-end: commands inside code regions are not parsed."""

    def test_command_in_simple_fence_skipped(self):
        from forge.tools.invocation import parse_inline_commands

        content = (
            "Example:\n```\n"
            '<commit message="example"/>\n'
            "```\n"
        )
        commands = parse_inline_commands(content)
        assert len(commands) == 0

    def test_command_in_tilde_fence_skipped(self):
        from forge.tools.invocation import parse_inline_commands

        content = (
            "Example:\n~~~\n"
            '<commit message="example"/>\n'
            "~~~\n"
        )
        commands = parse_inline_commands(content)
        assert len(commands) == 0

    def test_command_in_inline_backticks_skipped(self):
        from forge.tools.invocation import parse_inline_commands

        content = 'Use `<commit message="x"/>` to commit.'
        commands = parse_inline_commands(content)
        assert len(commands) == 0

    def test_real_command_outside_fence_works(self):
        from forge.tools.invocation import parse_inline_commands

        content = (
            "Example:\n```\n"
            '<commit message="example"/>\n'
            "```\n"
            "And the real one:\n"
            '<commit message="real"/>\n'
        )
        commands = parse_inline_commands(content)
        assert len(commands) == 1
        assert commands[0].args["message"] == "real"

    def test_command_in_tilde_with_nested_backticks_skipped(self):
        """REGRESSION for the exact bug: tilde fence containing a nested
        triple-backtick example block, with the dangerous command inside
        the inner block. Old regex would expose the command.
        """
        from forge.tools.invocation import parse_inline_commands

        content = (
            "~~~text\n"
            "Here's an example:\n"
            "```\n"
            '<commit message="should not run"/>\n'
            "```\n"
            "End of explanation.\n"
            "~~~\n"
        )
        commands = parse_inline_commands(content)
        assert len(commands) == 0

    def test_command_in_quad_backtick_fence_with_inner_triple_skipped(self):
        from forge.tools.invocation import parse_inline_commands

        content = (
            "````\n"
            "```\n"
            '<commit message="deeply nested"/>\n'
            "```\n"
            "````\n"
        )
        commands = parse_inline_commands(content)
        assert len(commands) == 0

    def test_command_in_unterminated_fence_skipped(self):
        from forge.tools.invocation import parse_inline_commands

        content = (
            "Example (incomplete):\n```\n"
            '<commit message="streaming, not done yet"/>\n'
        )
        commands = parse_inline_commands(content)
        assert len(commands) == 0

    def test_delete_file_command_in_fence_skipped(self):
        """The bug that started it all: dalate-file inside a fence
        must not execute. Using parametrized 'commit' as proxy is fine
        because the code-region check is uniform across all inline tools,
        but we add this explicit test for the offending tool.
        """
        from forge.tools.invocation import parse_inline_commands

        content = (
            "If you wanted to delete a file you would write:\n"
            "```\n"
            '<delete file="example.py"/>\n'
            "```\n"
            "But don't actually do that here."
        )
        commands = parse_inline_commands(content)
        assert len(commands) == 0