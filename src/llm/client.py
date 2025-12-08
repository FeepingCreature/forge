"""
LLM client for communicating with OpenRouter
"""

import requests
import json
from typing import List, Dict, Optional, Iterator, Callable


class LLMClient:
    """Client for OpenRouter API"""
    
    def __init__(self, api_key: str, model: str = "anthropic/claude-3.5-sonnet"):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://openrouter.ai/api/v1"
        
    def chat(self, messages: List[Dict[str, str]], tools: Optional[List[Dict]] = None) -> Dict:
        """Send chat request to LLM (non-streaming)"""
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
    
    def chat_stream(self, messages: List[Dict[str, str]], tools: Optional[List[Dict]] = None) -> Iterator[Dict]:
        """Send chat request to LLM with streaming"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        
        if tools:
            payload["tools"] = tools
            
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=payload,
            stream=True
        )
        
        response.raise_for_status()
        
        # Parse SSE stream
        for line in response.iter_lines():
            if line:
                line = line.decode('utf-8')
                if line.startswith('data: '):
                    data = line[6:]  # Remove 'data: ' prefix
                    if data == '[DONE]':
                        break
                    try:
                        yield json.loads(data)
                    except json.JSONDecodeError:
                        continue
