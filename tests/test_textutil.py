"""Tests for the small pure-function helpers in forge.textutil."""

from forge.textutil import (
    indent_block,
    normalize_whitespace,
    pluralize,
    strip_trailing_blank_lines,
    truncate_middle,
)


class TestTruncateMiddle:
    def test_short_text_unchanged(self):
        assert truncate_middle("hello", 10) == "hello"

    def test_exact_length_unchanged(self):
        assert truncate_middle("hello", 5) == "hello"

    def test_truncates_from_middle(self):
        result = truncate_middle("abcdefghij", 7)
        assert len(result) == 7
        assert result.startswith("a")
        assert result.endswith("j")
        assert "\u2026" in result

    def test_custom_ellipsis(self):
        result = truncate_middle("abcdefghij", 7, ellipsis="...")
        assert len(result) == 7
        assert "..." in result

    def test_max_len_zero(self):
        assert truncate_middle("anything", 0) == ""

    def test_max_len_smaller_than_ellipsis(self):
        assert truncate_middle("abcdef", 1) == "a"


class TestNormalizeWhitespace:
    def test_collapses_runs(self):
        assert normalize_whitespace("a   b\t\tc") == "a b c"

    def test_strips_ends(self):
        assert normalize_whitespace("  hi there  ") == "hi there"

    def test_newlines_become_spaces(self):
        assert normalize_whitespace("a\nb\nc") == "a b c"

    def test_empty(self):
        assert normalize_whitespace("") == ""


class TestIndentBlock:
    def test_indents_each_line(self):
        assert indent_block("a\nb", ">>") == ">>a\n>>b"

    def test_blank_lines_untouched(self):
        assert indent_block("a\n\nb") == "    a\n\n    b"

    def test_default_prefix(self):
        assert indent_block("x") == "    x"


class TestStripTrailingBlankLines:
    def test_removes_trailing_blanks(self):
        assert strip_trailing_blank_lines("a\nb\n\n\n") == "a\nb"

    def test_keeps_interior_blanks(self):
        assert strip_trailing_blank_lines("a\n\nb\n") == "a\n\nb"

    def test_all_blank(self):
        assert strip_trailing_blank_lines("\n\n\n") == ""


class TestPluralize:
    def test_singular(self):
        assert pluralize(1, "file") == "1 file"

    def test_plural_default_s(self):
        assert pluralize(3, "file") == "3 files"

    def test_zero_is_plural(self):
        assert pluralize(0, "file") == "0 files"

    def test_explicit_plural(self):
        assert pluralize(2, "mouse", "mice") == "2 mice"

    def test_negative_is_plural(self):
        assert pluralize(-1, "file") == "-1 files"
