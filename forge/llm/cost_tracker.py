"""
Global cost tracker for OpenRouter API usage.

Accumulates costs across branches and the entire program session.
"""

from PySide6.QtCore import QObject, Signal


class CostTracker(QObject):
    """Singleton tracker for accumulated OpenRouter costs."""

    # Emitted when cost changes, with new total
    cost_updated = Signal(float)

    _instance: "CostTracker | None" = None

    def __new__(cls) -> "CostTracker":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        # Only initialize once
        if hasattr(self, "_initialized"):
            return
        super().__init__()
        self._initialized = True
        self._total_cost = 0.0
        self._request_count = 0

    @property
    def total_cost(self) -> float:
        """Get total accumulated cost in USD."""
        return self._total_cost

    @property
    def request_count(self) -> int:
        """Get total number of API requests."""
        return self._request_count

    def add_cost(self, cost: float) -> None:
        """Add a cost from an API request."""
        self._total_cost += cost
        self._request_count += 1
        self.cost_updated.emit(self._total_cost)

    def reset(self) -> None:
        """Reset the tracker (mainly for testing)."""
        self._total_cost = 0.0
        self._request_count = 0
        self.cost_updated.emit(0.0)


# Global instance
COST_TRACKER = CostTracker()
