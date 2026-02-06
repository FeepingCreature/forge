"""Tests for partial mermaid diagram repair during streaming."""

import pytest

from forge.ui.chat_streaming import _repair_partial_mermaid


class TestRepairPartialMermaid:
    """Test best-effort repair of incomplete mermaid syntax."""

    def test_complete_diagram_unchanged(self):
        """A complete diagram should pass through unchanged."""
        content = "graph TD\n    A[Start] --> B[End]"
        result = _repair_partial_mermaid(content)
        assert result == content

    def test_empty_content(self):
        """Empty content returns as-is."""
        assert _repair_partial_mermaid("") == ""
        assert _repair_partial_mermaid("   ") == "   "

    def test_unclosed_square_bracket(self):
        """Unclosed [ in node definition gets closed."""
        content = "graph TD\n    A[Start] --> B[Processing"
        result = _repair_partial_mermaid(content)
        assert result.count("[") == result.count("]")
        assert "Processing]" in result

    def test_unclosed_paren(self):
        """Unclosed ( in node definition gets closed."""
        content = "graph TD\n    A[Start] --> B(Round"
        result = _repair_partial_mermaid(content)
        assert result.count("(") == result.count(")")

    def test_unclosed_curly_brace(self):
        """Unclosed { in node definition gets closed."""
        content = "graph TD\n    A[Start] --> B{Decision"
        result = _repair_partial_mermaid(content)
        assert result.count("{") == result.count("}")

    def test_unclosed_subgraph(self):
        """Unclosed subgraph gets 'end' appended."""
        content = "graph TD\n    subgraph SG1\n        A --> B"
        result = _repair_partial_mermaid(content)
        assert "end" in result.split("\n")[-1] or result.rstrip().endswith("end")

    def test_multiple_unclosed_subgraphs(self):
        """Multiple unclosed subgraphs each get 'end'."""
        content = "graph TD\n    subgraph SG1\n        A --> B\n    subgraph SG2\n        C --> D"
        result = _repair_partial_mermaid(content)
        # Should have 2 more 'end' lines than the original
        end_count = len([l for l in result.split("\n") if l.strip() == "end"])
        assert end_count == 2

    def test_trailing_incomplete_line_trimmed(self):
        """A trailing line that's clearly mid-token gets trimmed."""
        content = "graph TD\n    A[Start] --> B[End]\n    C[Proc"
        result = _repair_partial_mermaid(content)
        # The incomplete line "C[Proc" should be trimmed
        # (it doesn't end with ], so it's incomplete, gets trimmed)
        assert "C[Proc" not in result

    def test_trailing_arrow_line_kept(self):
        """A line ending with an arrow is considered complete."""
        content = "graph TD\n    A[Start] --> B[End]\n    B --> C"
        result = _repair_partial_mermaid(content)
        assert "B --> C" in result

    def test_unclosed_quote(self):
        """Unclosed double quote gets closed."""
        content = 'graph TD\n    A["Start] --> B["End'
        result = _repair_partial_mermaid(content)
        assert result.count('"') % 2 == 0

    def test_sequence_diagram_preserved(self):
        """Sequence diagram lines are mostly self-closing."""
        content = "sequenceDiagram\n    Alice->>Bob: Hello\n    Bob-->>Alice: Hi"
        result = _repair_partial_mermaid(content)
        assert result == content

    def test_type_declaration_only(self):
        """Just a type declaration is valid."""
        assert "graph TD" in _repair_partial_mermaid("graph TD")
        assert "sequenceDiagram" in _repair_partial_mermaid("sequenceDiagram")

    def test_comment_line_kept(self):
        """Mermaid comment lines (%%) are kept."""
        content = "graph TD\n    %% This is a comment"
        result = _repair_partial_mermaid(content)
        assert "%% This is a comment" in result

    def test_subgraph_with_end_balanced(self):
        """Already-closed subgraphs don't get extra 'end'."""
        content = "graph TD\n    subgraph SG1\n        A --> B\n    end"
        result = _repair_partial_mermaid(content)
        end_count = len([l for l in result.split("\n") if l.strip() == "end"])
        assert end_count == 1

    def test_flowchart_lr(self):
        """flowchart LR variant works."""
        content = "flowchart LR\n    A[Start] --> B[Mid"
        result = _repair_partial_mermaid(content)
        assert result.count("[") == result.count("]")

    def test_mixed_brackets(self):
        """Multiple bracket types in one diagram."""
        content = "graph TD\n    A[Box] --> B{Decision}\n    B -->|Yes| C(Round"
        result = _repair_partial_mermaid(content)
        assert result.count("[") == result.count("]")
        assert result.count("{") == result.count("}")
        assert result.count("(") == result.count(")")

    def test_node_with_style_class(self):
        """Lines ending with ::: are kept."""
        content = "graph TD\n    A[Start]:::highlight"
        result = _repair_partial_mermaid(content)
        assert ":::highlight" in result