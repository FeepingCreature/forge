"""
Settings management for Forge
"""

import json
import os
from pathlib import Path
from typing import Optional


class Settings:
    """Manages application settings"""
    
    DEFAULT_SETTINGS = {
        "llm": {
            "api_key": "",
            "model": "anthropic/claude-3.5-sonnet",
            "base_url": "https://openrouter.ai/api/v1"
        },
        "editor": {
            "font_size": 10,
            "tab_width": 4,
            "show_line_numbers": True,
            "highlight_current_line": True
        },
        "ui": {
            "theme": "light",
            "editor_ai_split": [2, 1]  # Ratio for splitter
        },
        "git": {
            "auto_commit": False,
            "commit_message_model": "anthropic/claude-3-haiku"
        }
    }
    
    def __init__(self, config_path: Optional[Path] = None):
        """Initialize settings"""
        if config_path is None:
            config_path = Path.home() / ".config" / "forge" / "settings.json"
        
        self.config_path = config_path
        self.settings = self.DEFAULT_SETTINGS.copy()
        self.load()
        
    def load(self):
        """Load settings from file"""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r') as f:
                    loaded = json.load(f)
                    # Merge with defaults to handle new settings
                    self._merge_settings(self.settings, loaded)
            except Exception as e:
                print(f"Error loading settings: {e}")
                
    def save(self):
        """Save settings to file"""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, 'w') as f:
                json.dump(self.settings, f, indent=2)
        except Exception as e:
            print(f"Error saving settings: {e}")
            
    def _merge_settings(self, base, updates):
        """Recursively merge settings dictionaries"""
        for key, value in updates.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._merge_settings(base[key], value)
            else:
                base[key] = value
                
    def get(self, path: str, default=None):
        """Get a setting by dot-separated path (e.g., 'llm.api_key')"""
        parts = path.split('.')
        value = self.settings
        
        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return default
                
        return value
        
    def set(self, path: str, value):
        """Set a setting by dot-separated path"""
        parts = path.split('.')
        target = self.settings
        
        for part in parts[:-1]:
            if part not in target:
                target[part] = {}
            target = target[part]
            
        target[parts[-1]] = value
        
    def get_api_key(self) -> str:
        """Get API key from settings or environment"""
        api_key = self.get('llm.api_key', '')
        if not api_key:
            api_key = os.environ.get('OPENROUTER_API_KEY', '')
        return api_key
