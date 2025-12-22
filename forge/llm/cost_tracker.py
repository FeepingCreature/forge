"""
Global cost tracker for OpenRouter API usage.

Accumulates costs across branches and the entire program session.
Also tracks daily costs in a cache file for cross-session awareness.
"""

import json
from datetime import date
from pathlib import Path

from PySide6.QtCore import QObject, Signal

# Cache file for daily costs
CACHE_DIR = Path.home() / ".cache" / "forge"
DAILY_COSTS_FILE = CACHE_DIR / "daily_costs.json"


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
        self._daily_cost = self._load_daily_cost()

    def _load_daily_cost(self) -> float:
        """Load today's accumulated cost from cache."""
        today = date.today().isoformat()
        try:
            if DAILY_COSTS_FILE.exists():
                data = json.loads(DAILY_COSTS_FILE.read_text())
                return float(data.get(today, 0.0))
        except (json.JSONDecodeError, OSError, ValueError):
            pass
        return 0.0

    def _save_daily_cost(self) -> None:
        """Save today's cost to cache."""
        today = date.today().isoformat()
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            # Load existing data, update today, prune old entries
            data: dict[str, float] = {}
            if DAILY_COSTS_FILE.exists():
                try:
                    data = json.loads(DAILY_COSTS_FILE.read_text())
                except (json.JSONDecodeError, ValueError):
                    data = {}
            data[today] = self._daily_cost
            # Keep only last 7 days
            recent_dates = sorted(data.keys(), reverse=True)[:7]
            data = {d: data[d] for d in recent_dates}
            DAILY_COSTS_FILE.write_text(json.dumps(data, indent=2))
        except OSError:
            pass  # Non-critical, just skip

    @property
    def total_cost(self) -> float:
        """Get total accumulated cost in USD (this session)."""
        return self._total_cost

    @property
    def daily_cost(self) -> float:
        """Get total accumulated cost today (across sessions)."""
        return self._daily_cost

    @property
    def request_count(self) -> int:
        """Get total number of API requests."""
        return self._request_count

    def add_cost(self, cost: float) -> None:
        """Add a cost from an API request."""
        self._total_cost += cost
        self._daily_cost += cost
        self._request_count += 1
        self._save_daily_cost()
        self.cost_updated.emit(self._total_cost)

    def reset(self) -> None:
        """Reset the tracker (mainly for testing)."""
        self._total_cost = 0.0
        self._request_count = 0
        self.cost_updated.emit(0.0)


# Global instance
COST_TRACKER = CostTracker()
