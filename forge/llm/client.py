"""
LLM client for communicating with OpenRouter
"""

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import requests

from forge.llm.cost_tracker import COST_TRACKER


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
            "HTTP-Referer": "http://localhost",
            "X-Title": "Forge",
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
            "HTTP-Referer": "http://localhost",
            "X-Title": "Forge",
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

            if not response.ok:
                # Include response body in error for debugging
                try:
                    error_body = response.text
                except Exception:
                    error_body = "(could not read response body)"
                raise requests.HTTPError(
                    f"{response.status_code} {response.reason} for {response.url}\n\nResponse body:\n{error_body}",
                    response=response,
                )

            result: dict[str, Any] = response.json()
            print("✅ LLM Response received")

            # Fetch cost info from response
            generation_id = result.get("id")
            if generation_id:
                self._fetch_and_record_cost(generation_id)

            return result

        # If we exhausted all retries, raise the last error
        assert response is not None
        try:
            error_body = response.text
        except Exception:
            error_body = "(could not read response body)"
        raise requests.HTTPError(
            f"{response.status_code} {response.reason} for {response.url}\n\nResponse body:\n{error_body}",
            response=response,
        )

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
            "HTTP-Referer": "http://localhost",
            "X-Title": "Forge",
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

            if not response.ok:
                # Include response body in error for debugging
                try:
                    error_body = response.text
                except Exception:
                    error_body = "(could not read response body)"
                raise requests.HTTPError(
                    f"{response.status_code} {response.reason} for {response.url}\n\nResponse body:\n{error_body}",
                    response=response,
                )
            break
        else:
            # Exhausted all retries
            assert response is not None
            if not response.ok:
                try:
                    error_body = response.text
                except Exception:
                    error_body = "(could not read response body)"
                raise requests.HTTPError(
                    f"{response.status_code} {response.reason} for {response.url}\n\nResponse body:\n{error_body}",
                    response=response,
                )

        print("📡 Streaming response started")

        generation_id: str | None = None

        # Parse SSE stream
        for line in response.iter_lines():
            if line:
                line = line.decode("utf-8")
                if line.startswith("data: "):
                    data = line[6:]  # Remove 'data: ' prefix
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        # Capture generation ID for cost lookup
                        if "id" in chunk and generation_id is None:
                            generation_id = chunk["id"]

                        # Check for error in the chunk (content filtering, etc.)
                        if "error" in chunk:
                            error_info = chunk["error"]
                            error_msg = error_info.get("message", "Unknown streaming error")
                            error_code = error_info.get("code", "")
                            metadata = error_info.get("metadata", {})
                            provider = metadata.get("provider_name", "unknown")
                            raise RuntimeError(
                                f"LLM streaming error (provider={provider}, code={error_code}): {error_msg}"
                            )

                        yield chunk
                    except json.JSONDecodeError:
                        continue

        # Fetch cost info after streaming completes
        if generation_id:
            self._fetch_and_record_cost(generation_id)

    def _fetch_and_record_cost(self, generation_id: str) -> None:
        """Fetch generation cost from OpenRouter and record it."""
        # OpenRouter provides cost info via the generation endpoint
        # We need to poll briefly as the cost may not be immediately available
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # Try a few times with short delays
        for _attempt in range(3):
            try:
                response = requests.get(
                    f"{self.base_url}/generation?id={generation_id}",
                    headers=headers,
                    timeout=5,
                )
                if response.ok:
                    data = response.json().get("data", {})
                    total_cost = data.get("total_cost")
                    if total_cost is not None:
                        COST_TRACKER.add_cost(float(total_cost))
                        print(
                            f"💰 Request cost: ${total_cost:.6f} (total: ${COST_TRACKER.total_cost:.4f})"
                        )
                        return
                # Cost not ready yet, wait and retry
                time.sleep(0.5)
            except Exception as e:
                print(f"⚠️ Failed to fetch cost: {e}")
                break
