"""Git graph visualization components."""

from forge.ui.git_graph.widget import GitGraphView

# Backwards compatibility alias
GitGraphScrollArea = GitGraphView

__all__ = ["GitGraphView", "GitGraphScrollArea"]
