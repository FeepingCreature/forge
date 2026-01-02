"""
Settings management for Forge
"""

import json
import os
from pathlib import Path
from typing import Any

from forge.constants import DEFAULT_SUMMARIZATION_MODEL


class Settings:
    """Manages application settings"""

    DEFAULT_SETTINGS = {
        "llm": {
            "api_key": "",
            "model": "anthropic/claude-3.5-sonnet",
            "base_url": "https://openrouter.ai/api/v1",
            "parallel_summarization": 8,  # Number of parallel requests for summarization
            "summary_token_budget": 10000,  # Max tokens for file summaries before listing only
        },
        "editor": {
            "font_size": 10,
            "tab_width": 4,
            "show_line_numbers": True,
            "highlight_current_line": True,
        },
        "ui": {
            "theme": "light",
            "editor_ai_split": [2, 1],  # Ratio for splitter
        },
        "git": {"auto_commit": False},
    }

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialize settings"""
        if config_path is None:
            config_path = Path.home() / ".config" / "forge" / "settings.json"

        self.config_path = config_path
        self.settings = self.DEFAULT_SETTINGS.copy()
        self.load()

    def load(self) -> None:
        """Load settings from file"""
        if self.config_path.exists():
            with open(self.config_path) as f:
                loaded = json.load(f)
                # Merge with defaults to handle new settings
                self._merge_settings(self.settings, loaded)

    def save(self) -> None:
        """Save settings to file"""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w") as f:
            json.dump(self.settings, f, indent=2)

    def _merge_settings(self, base: dict[str, Any], updates: dict[str, Any]) -> None:
        """Recursively merge settings dictionaries"""
        for key, value in updates.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                # Both are dicts, safe to recurse
                base_dict: dict[str, Any] = base[key]
                value_dict: dict[str, Any] = value
                self._merge_settings(base_dict, value_dict)
            else:
                base[key] = value

    def get(self, path: str, default: Any = None) -> Any:
        """Get a setting by dot-separated path (e.g., 'llm.api_key')"""
        parts = path.split(".")
        value: Any = self.settings

        for part in parts:
            if isinstance(value, dict):
                value_dict: dict[str, Any] = value
                if part in value_dict:
                    value = value_dict[part]
                else:
                    return default
            else:
                return default

        return value

    def set(self, path: str, value: Any) -> None:
        """Set a setting by dot-separated path"""
        parts = path.split(".")
        target: Any = self.settings

        for part in parts[:-1]:
            if part not in target:
                target[part] = {}
            target = target[part]

        target[parts[-1]] = value

    def get_api_key(self) -> str:
        """Get API key from settings or environment"""
        api_key: str = str(self.get("llm.api_key", ""))
        if not api_key:
            api_key = os.environ.get("OPENROUTER_API_KEY", "")
        return api_key

    def get_summarization_model(self) -> str:
        """Get the model to use for summarization, commits, asks, and completions.

        This is the 'cheap/fast' model used for everything except the main agentic flow.
        Falls back to haiku if not configured.
        """
        model: str = str(self.get("llm.summarization_model", DEFAULT_SUMMARIZATION_MODEL))
        return model

    def get_parallel_summarization(self) -> int:
        """Get the number of parallel requests to use for summarization.

        Controls how many LLM requests run concurrently when generating file summaries.
        Higher values speed up initial summarization but use more API quota.
        """
        parallel: int = int(self.get("llm.parallel_summarization", 8))
        return max(1, parallel)  # At least 1

    def get_summary_token_budget(self) -> int:
        """Get the token budget for file summaries.

        Summaries are generated in breadth-first order (by path depth) until
        this budget is reached. Files beyond the budget are listed without
        summaries, with a note to use scout for investigation.
        """
        budget: int = int(self.get("llm.summary_token_budget", 10000))
        return max(1000, budget)  # At least 1k
