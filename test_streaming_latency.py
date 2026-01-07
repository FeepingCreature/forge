#!/usr/bin/env python3
"""
Test script to measure streaming latency for tool calls.
Measures time from stream start to first token, and time from tool name to first args chunk.

Usage:
  python test_streaming_latency.py              # Use OpenRouter (default)
  python test_streaming_latency.py --anthropic  # Use Anthropic API directly
"""

import argparse
import json
import os
from datetime import datetime

import requests

OPENROUTER_MODEL = "anthropic/claude-opus-4.5"
ANTHROPIC_MODEL = "claude-opus-4-5"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "think",
            "description": "Think through a problem step by step",
            "parameters": {
                "type": "object",
                "properties": {
                    "scratchpad": {"type": "string", "description": "Your reasoning"},
                    "conclusion": {"type": "string", "description": "Your conclusion"},
                },
                "required": ["scratchpad", "conclusion"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_replace",
            "description": "Search and replace in a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string"},
                    "search": {"type": "string"},
                    "replace": {"type": "string"},
                },
                "required": ["filepath", "search", "replace"],
            },
        },
    },
]

MESSAGES = [
    {"role": "system", "content": "You are a helpful assistant. Use the think tool to reason through problems."},
    {"role": "user", "content": "Use the think tool to think about what makes a good API design. Be thorough in your scratchpad."},
]

# Anthropic uses a different tool format
ANTHROPIC_TOOLS = [
    {
        "name": "think",
        "description": "Think through a problem step by step",
        "input_schema": {
            "type": "object",
            "properties": {
                "scratchpad": {"type": "string", "description": "Your reasoning"},
                "conclusion": {"type": "string", "description": "Your conclusion"},
            },
            "required": ["scratchpad", "conclusion"],
        },
    },
    {
        "name": "search_replace",
        "description": "Search and replace in a file",
        "input_schema": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string"},
                "search": {"type": "string"},
                "replace": {"type": "string"},
            },
            "required": ["filepath", "search", "replace"],
        },
    },
]


def test_openrouter(api_key: str):
    """Test streaming via OpenRouter API"""
    print(f"Testing OpenRouter streaming with {OPENROUTER_MODEL}")
    print("=" * 60)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/FeepingCreature/forge",
        "X-Title": "Forge Latency Test",
        # Enable fine-grained tool streaming for Anthropic models
        "x-anthropic-beta": "fine-grained-tool-streaming-2025-05-14",
    }

    payload = {
        "model": OPENROUTER_MODEL,
        "messages": MESSAGES,
        "tools": TOOLS,
        "stream": True,
    }

    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers=headers,
        json=payload,
        stream=True,
    )

    if not response.ok:
        print(f"ERROR: {response.status_code} {response.reason}")
        print(response.text)
        return

    process_openai_stream(response)


def test_anthropic(api_key: str):
    """Test streaming via Anthropic API directly"""
    print(f"Testing Anthropic streaming with {ANTHROPIC_MODEL}")
    print("=" * 60)

    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
        # Enable fine-grained tool streaming
        "anthropic-beta": "fine-grained-tool-streaming-2025-05-14",
    }

    # Anthropic uses different message format (no system in messages)
    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 4096,
        "system": MESSAGES[0]["content"],
        "messages": [{"role": "user", "content": MESSAGES[1]["content"]}],
        "tools": ANTHROPIC_TOOLS,
        "stream": True,
    }

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers=headers,
        json=payload,
        stream=True,
    )

    if not response.ok:
        print(f"ERROR: {response.status_code} {response.reason}")
        print(response.text)
        return

    process_anthropic_stream(response)


def process_openai_stream(response):
    """Process OpenAI-format SSE stream (used by OpenRouter)"""
    stream_start = datetime.now()
    first_token_time = None
    tool_timings: dict[int, dict] = {}

    print(f"\n[{0:6.2f}s] Stream started")

    for line in response.iter_lines():
        ts = (datetime.now() - stream_start).total_seconds()

        if not line:
            continue

        line = line.decode("utf-8")
        if not line.startswith("data: "):
            continue

        data = line[6:]
        if data == "[DONE]":
            print(f"[{ts:6.2f}s] [DONE]")
            break

        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue

        delta = chunk.get("choices", [{}])[0].get("delta", {})
        content = delta.get("content", "")
        tool_calls = delta.get("tool_calls", [])

        if content and first_token_time is None:
            first_token_time = ts
            print(f"[{ts:6.2f}s] FIRST CONTENT TOKEN: {repr(content[:50])}")

        for tc in tool_calls:
            idx = tc.get("index", 0)
            func = tc.get("function", {})
            name = func.get("name", "")
            args = func.get("arguments", "")

            if idx not in tool_timings:
                tool_timings[idx] = {"name": None, "name_time": None, "first_args_time": None}

            if name:
                tool_timings[idx]["name"] = name
                tool_timings[idx]["name_time"] = ts
                print(f"[{ts:6.2f}s] TOOL[{idx}] NAME: {name}")

            if args and tool_timings[idx]["first_args_time"] is None:
                tool_timings[idx]["first_args_time"] = ts
                latency = ts - tool_timings[idx]["name_time"] if tool_timings[idx]["name_time"] else 0
                print(f"[{ts:6.2f}s] TOOL[{idx}] FIRST ARGS (latency: {latency:.2f}s): {repr(args[:50])}")

    print_summary(first_token_time, tool_timings)


def process_anthropic_stream(response):
    """Process Anthropic SSE stream format"""
    stream_start = datetime.now()
    first_token_time = None
    tool_timings: dict[int, dict] = {}
    current_tool_idx = -1

    print(f"\n[{0:6.2f}s] Stream started")

    for line in response.iter_lines():
        ts = (datetime.now() - stream_start).total_seconds()

        if not line:
            continue

        line = line.decode("utf-8")
        if not line.startswith("data: "):
            continue

        data = line[6:]
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type", "")

        if event_type == "content_block_start":
            block = event.get("content_block", {})
            if block.get("type") == "tool_use":
                current_tool_idx += 1
                name = block.get("name", "")
                tool_timings[current_tool_idx] = {"name": name, "name_time": ts, "first_args_time": None}
                print(f"[{ts:6.2f}s] TOOL[{current_tool_idx}] NAME: {name}")
            elif block.get("type") == "text":
                text = block.get("text", "")
                if text and first_token_time is None:
                    first_token_time = ts
                    print(f"[{ts:6.2f}s] FIRST CONTENT TOKEN: {repr(text[:50])}")

        elif event_type == "content_block_delta":
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta":
                text = delta.get("text", "")
                if text and first_token_time is None:
                    first_token_time = ts
                    print(f"[{ts:6.2f}s] FIRST CONTENT TOKEN: {repr(text[:50])}")
            elif delta.get("type") == "input_json_delta":
                partial_json = delta.get("partial_json", "")
                if partial_json and current_tool_idx in tool_timings:
                    if tool_timings[current_tool_idx]["first_args_time"] is None:
                        tool_timings[current_tool_idx]["first_args_time"] = ts
                        latency = ts - tool_timings[current_tool_idx]["name_time"]
                        print(f"[{ts:6.2f}s] TOOL[{current_tool_idx}] FIRST ARGS (latency: {latency:.2f}s): {repr(partial_json[:50])}")

        elif event_type == "message_stop":
            print(f"[{ts:6.2f}s] [DONE]")
            break

    print_summary(first_token_time, tool_timings)


def print_summary(first_token_time, tool_timings):
    """Print timing summary"""
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    if first_token_time:
        print(f"Time to first content token: {first_token_time:.2f}s")

    for idx, timing in sorted(tool_timings.items()):
        name = timing["name"] or f"tool_{idx}"
        name_time = timing["name_time"]
        args_time = timing["first_args_time"]

        if name_time and args_time:
            latency = args_time - name_time
            print(f"Tool '{name}': name at {name_time:.2f}s, first args at {args_time:.2f}s, LATENCY: {latency:.2f}s")
        elif name_time:
            print(f"Tool '{name}': name at {name_time:.2f}s, NO ARGS RECEIVED")


def main():
    parser = argparse.ArgumentParser(description="Test LLM streaming latency for tool calls")
    parser.add_argument("--anthropic", action="store_true", help="Use Anthropic API directly instead of OpenRouter")
    args = parser.parse_args()

    if args.anthropic:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("ERROR: Set ANTHROPIC_API_KEY environment variable")
            return
        test_anthropic(api_key)
    else:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            print("ERROR: Set OPENROUTER_API_KEY environment variable")
            return
        test_openrouter(api_key)


if __name__ == "__main__":
    main()
