"""
Global cost tracker for OpenRouter API usage.

Accumulates costs across branches and the entire program session.
"""


class CostTracker:
    """Singleton tracker for accumulated OpenRouter costs."""

    _instance: "CostTracker | None" = None

    def __new__(cls) -> "CostTracker":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._total_cost = 0.0
            cls._instance._request_count = 0
        return cls._instance

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

    def reset(self) -> None:
        """Reset the tracker (mainly for testing)."""
        self._total_cost = 0.0
        self._request_count = 0


# Global instance
COST_TRACKER = CostTracker()
