"""
LLM client for communicating with OpenRouter
"""

import requests
import json
from typing import List, Dict, Optional


class LLMClient:
    """Client for OpenRouter API"""
    
    def __init__(self, api_key: str, model: str = "anthropic/claude-3.5-sonnet"):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://openrouter.ai/api/v1"
        
    def chat(self, messages: List[Dict[str, str]], tools: Optional[List[Dict]] = None) -> Dict:
        """Send chat request to LLM"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": self.model,
            "messages": messages,
        }
        
        if tools:
            payload["tools"] = tools
            
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=payload
        )
        
        response.raise_for_status()
        return response.json()
