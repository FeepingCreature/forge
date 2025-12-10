"""
LLM client for communicating with OpenRouter
"""

import json
from collections.abc import Iterator
from typing import Any

import requests


class LLMClient:
    """Client for OpenRouter API"""

    def __init__(self, api_key: str, model: str = "anthropic/claude-3.5-sonnet") -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = "https://openrouter.ai/api/v1"

    def chat(
        self, messages: list[dict[str, str]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
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

        response = requests.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)

        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    def chat_stream(
        self, messages: list[dict[str, str]], tools: list[dict[str, Any]] | None = None
    ) -> Iterator[dict[str, Any]]:
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
            f"{self.base_url}/chat/completions", headers=headers, json=payload, stream=True
        )

        response.raise_for_status()

        # Parse SSE stream
        for line in response.iter_lines():
            if line:
                line = line.decode("utf-8")
                if line.startswith("data: "):
                    data = line[6:]  # Remove 'data: ' prefix
                    if data == "[DONE]":
                        break
                    try:
                        yield json.loads(data)
                    except json.JSONDecodeError:
                        continue
