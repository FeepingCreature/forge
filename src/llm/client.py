"""
LLM client for communicating with OpenRouter
"""

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import requests


class LLMClient:
    """Client for OpenRouter API"""

    def __init__(self, api_key: str, model: str = "anthropic/claude-3.5-sonnet") -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = "https://openrouter.ai/api/v1"

    def get_available_models(self) -> list[dict[str, Any]]:
        """Fetch list of available models from OpenRouter"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        response = requests.get(f"{self.base_url}/models", headers=headers)
        response.raise_for_status()

        data: dict[str, Any] = response.json()
        models: list[dict[str, Any]] = data.get("data", [])
        return models

    def chat(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
        max_retries: int = 5,
    ) -> dict[str, Any]:
        """Send chat request to LLM (non-streaming) with retry on rate limit"""
        print(f"🌐 LLM Request: {self.model} (non-streaming, {len(messages)} messages)")

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
            print(f"   Tools available: {len(tools)}")

        # Debug: dump full request to file
        debug_dir = Path("/tmp/forge_debug")
        debug_dir.mkdir(exist_ok=True)
        debug_file = debug_dir / f"request_{int(time.time() * 1000)}.json"
        debug_file.write_text(json.dumps(payload, indent=2))
        print(f"   📝 Request dumped to: {debug_file}")

        for attempt in range(max_retries):
            response = requests.post(
                f"{self.base_url}/chat/completions", headers=headers, json=payload
            )

            if response.status_code == 429:
                # Rate limited - back off and retry
                wait_time = 2**attempt  # Exponential backoff: 1, 2, 4, 8, 16 seconds
                print(
                    f"⏳ Rate limited (429), waiting {wait_time}s before retry {attempt + 1}/{max_retries}"
                )
                time.sleep(wait_time)
                continue

            response.raise_for_status()
            result: dict[str, Any] = response.json()
            print("✅ LLM Response received")
            return result

        # If we exhausted all retries, raise the last error
        response.raise_for_status()
        # This line won't be reached, but satisfies type checker
        return {}

    def chat_stream(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
        max_retries: int = 5,
    ) -> Iterator[dict[str, Any]]:
        """Send chat request to LLM with streaming and retry on rate limit"""
        print(f"🌐 LLM Request: {self.model} (streaming, {len(messages)} messages)")

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
            print(f"   Tools available: {len(tools)}")

        # Debug: dump full request to file
        debug_dir = Path("/tmp/forge_debug")
        debug_dir.mkdir(exist_ok=True)
        debug_file = debug_dir / f"request_stream_{int(time.time() * 1000)}.json"
        debug_file.write_text(json.dumps(payload, indent=2))
        print(f"   📝 Request dumped to: {debug_file}")

        response = None
        for attempt in range(max_retries):
            response = requests.post(
                f"{self.base_url}/chat/completions", headers=headers, json=payload, stream=True
            )

            if response.status_code == 429:
                # Rate limited - back off and retry
                wait_time = 2**attempt  # Exponential backoff: 1, 2, 4, 8, 16 seconds
                print(
                    f"⏳ Rate limited (429), waiting {wait_time}s before retry {attempt + 1}/{max_retries}"
                )
                time.sleep(wait_time)
                continue

            response.raise_for_status()
            break
        else:
            # Exhausted all retries
            assert response is not None
            response.raise_for_status()

        print("📡 Streaming response started")

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
