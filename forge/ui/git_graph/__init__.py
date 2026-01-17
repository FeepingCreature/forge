"""Git graph visualization components."""

from forge.ui.git_graph.widget import GitGraphView

# FIXME: confusing alias - should use GitGraphView everywhere
GitGraphScrollArea = GitGraphView

__all__ = ["GitGraphView", "GitGraphScrollArea"]
