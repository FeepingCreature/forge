"""Tests for ordered-list number preservation in the chat markdown renderer.

The Python `markdown` library (and CommonMark) renumber ordered lists
consecutively starting from the first item's number. That makes it impossible
for the user and the AI to refer to "item 7" and mean the same thing.

We work around this in `forge.ui.tool_rendering.render_markdown` by:
1. Enabling the `sane_lists` extension (so `<ol start="N">` is honored).
2. Post-processing the rendered HTML to inject `value="M"` on every `<li>`,
   matching the source-markdown number for that item exactly.

Same behaviour is mirrored in the hand-rolled renderer used for `.md` file
previews in `forge.ui.markdown_preview._markdown_to_html`.
"""

import re

from forge.ui.markdown_preview import _markdown_to_html
from forge.ui.tool_rendering import (
    _extract_ordered_list_numbers,
    _preserve_ordered_list_numbers,
    render_markdown,
)


def _li_values(html: str) -> list[int | None]:
    """Return the value="N" of every <li> in `html`, in order.

    A `<li>` without a value attribute returns None for that slot.
    """
    out: list[int | None] = []
    for m in re.finditer(r"<li\b([^>]*)>", html, flags=re.IGNORECASE):
        v = re.search(r'value\s*=\s*"(\d+)"', m.group(1))
        out.append(int(v.group(1)) if v else None)
    return out


# ---------------------------------------------------------------------------
# _extract_ordered_list_numbers
# ---------------------------------------------------------------------------


class TestExtractNumbers:
    def test_simple_sequence(self) -> None:
        assert _extract_ordered_list_numbers("1. a\n2. b\n3. c\n") == [[1, 2, 3]]

    def test_starting_offset(self) -> None:
        assert _extract_ordered_list_numbers("3. a\n4. b\n5. c\n") == [[3, 4, 5]]

    def test_gaps_preserved(self) -> None:
        assert _extract_ordered_list_numbers("1. a\n7. b\n99. c\n") == [[1, 7, 99]]

    def test_two_lists_separated_by_blank(self) -> None:
        src = "1. a\n2. b\n\n10. x\n11. y\n"
        assert _extract_ordered_list_numbers(src) == [[1, 2], [10, 11]]

    def test_two_lists_separated_by_paragraph(self) -> None:
        src = "1. a\n2. b\nsome prose\n10. x\n"
        assert _extract_ordered_list_numbers(src) == [[1, 2], [10]]

    def test_fenced_code_block_ignored(self) -> None:
        src = "1. a\n\n```\n1. not a list\n2. also not\n```\n\n5. b\n"
        assert _extract_ordered_list_numbers(src) == [[1], [5]]

    def test_tilde_fenced_code_block_ignored(self) -> None:
        src = "1. a\n\n~~~\n7. inside fence\n~~~\n\n5. b\n"
        assert _extract_ordered_list_numbers(src) == [[1], [5]]

    def test_no_lists(self) -> None:
        assert _extract_ordered_list_numbers("just some prose\nmore prose\n") == []


# ---------------------------------------------------------------------------
# _preserve_ordered_list_numbers (post-processor in isolation)
# ---------------------------------------------------------------------------


class TestPostProcessor:
    def test_injects_values_from_source(self) -> None:
        src = "1. a\n7. b\n99. c\n"
        html = "<ol>\n<li>a</li>\n<li>b</li>\n<li>c</li>\n</ol>"
        out = _preserve_ordered_list_numbers(src, html)
        assert _li_values(out) == [1, 7, 99]

    def test_replaces_existing_value_attribute(self) -> None:
        src = "5. a\n6. b\n"
        html = '<ol start="5">\n<li value="1">a</li>\n<li value="2">b</li>\n</ol>'
        out = _preserve_ordered_list_numbers(src, html)
        assert _li_values(out) == [5, 6]

    def test_no_lists_passthrough(self) -> None:
        html = "<p>hi</p>"
        assert _preserve_ordered_list_numbers("hi\n", html) == html

    def test_unordered_list_untouched(self) -> None:
        src = "- a\n- b\n"
        html = "<ul>\n<li>a</li>\n<li>b</li>\n</ul>"
        # No <ol>, so the post-processor must not touch <li>s.
        out = _preserve_ordered_list_numbers(src, html)
        assert "value=" not in out


# ---------------------------------------------------------------------------
# render_markdown (end-to-end via the chat renderer)
# ---------------------------------------------------------------------------


class TestRenderMarkdown:
    def test_simple_list_keeps_numbers(self) -> None:
        out = render_markdown("1. apple\n2. banana\n3. cherry\n")
        assert _li_values(out) == [1, 2, 3]

    def test_offset_start_preserved(self) -> None:
        out = render_markdown("3. apple\n4. banana\n5. cherry\n")
        assert _li_values(out) == [3, 4, 5]

    def test_gaps_preserved_end_to_end(self) -> None:
        # The whole point of this fix: 1, 7, 99 must render as 1, 7, 99
        # — not 1, 2, 3.
        out = render_markdown("1. first\n7. seventh\n99. ninetyninth\n")
        assert _li_values(out) == [1, 7, 99]

    def test_two_separate_lists(self) -> None:
        src = "1. a\n2. b\n\nSome text.\n\n10. x\n11. y\n"
        out = render_markdown(src)
        assert _li_values(out) == [1, 2, 10, 11]

    def test_ordered_list_in_code_block_not_renumbered(self) -> None:
        # Items inside a fenced code block must not be touched.
        src = "Here is code:\n\n```\n1. not a list item\n2. still not\n```\n\n5. real item\n"
        out = render_markdown(src)
        # Only one real <li>, with value="5"
        assert _li_values(out) == [5]

    def test_unordered_list_unaffected(self) -> None:
        out = render_markdown("- a\n- b\n- c\n")
        # No <li value="..."> on any item.
        assert _li_values(out) == [None, None, None]


# ---------------------------------------------------------------------------
# markdown_preview._markdown_to_html (preview tab renderer)
# ---------------------------------------------------------------------------


class TestPreviewRenderer:
    def test_ordered_list_renders_with_start_and_values(self) -> None:
        out = _markdown_to_html("1. a\n2. b\n3. c\n")
        assert '<ol start="1">' in out
        assert _li_values(out) == [1, 2, 3]

    def test_offset_start(self) -> None:
        out = _markdown_to_html("3. a\n4. b\n5. c\n")
        assert '<ol start="3">' in out
        assert _li_values(out) == [3, 4, 5]

    def test_gaps_preserved(self) -> None:
        out = _markdown_to_html("1. a\n7. b\n99. c\n")
        assert '<ol start="1">' in out
        assert _li_values(out) == [1, 7, 99]

    def test_unordered_still_works(self) -> None:
        out = _markdown_to_html("- a\n- b\n")
        assert "<ul>" in out
        assert "</ul>" in out
        assert _li_values(out) == [None, None]

    def test_ordered_list_after_unordered(self) -> None:
        # Switching list types should close the previous list cleanly.
        src = "- a\n- b\n\n1. x\n2. y\n"
        out = _markdown_to_html(src)
        assert "<ul>" in out and "</ul>" in out
        assert '<ol start="1">' in out and "</ol>" in out
        assert _li_values(out) == [None, None, 1, 2]