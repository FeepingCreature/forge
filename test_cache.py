#!/usr/bin/env python3
"""
Test script for Anthropic prompt caching via OpenRouter.

This script demonstrates how prompt caching works:
1. First request: cache_creation_input_tokens > 0, cache_read_input_tokens = 0
2. Second request: cache_creation_input_tokens = 0, cache_read_input_tokens > 0

Requires OPENROUTER_API_KEY environment variable.
"""

import os
import requests
import json

API_KEY = os.environ.get("OPENROUTER_API_KEY")
if not API_KEY:
    print("ERROR: Set OPENROUTER_API_KEY environment variable")
    exit(1)

BASE_URL = "https://openrouter.ai/api/v1"

# Generate a large system prompt (need 4096+ tokens for caching)
# Roughly 4 chars per token, so we need ~16K chars
LARGE_CONTENT = """
# Repository Documentation

This is a comprehensive guide to understanding the codebase.

## Architecture Overview

The system is built using a modular architecture with the following components:

### Core Components

1. **Data Layer**: Handles all data persistence and retrieval operations.
   - Database connections
   - ORM mappings
   - Query optimization
   - Connection pooling

2. **Business Logic Layer**: Contains all business rules and workflows.
   - Validation rules
   - Processing pipelines
   - State machines
   - Event handlers

3. **API Layer**: Exposes functionality to external consumers.
   - REST endpoints
   - GraphQL resolvers
   - WebSocket handlers
   - Authentication middleware

4. **Presentation Layer**: User interface components.
   - React components
   - State management
   - Routing
   - Theming

""" * 50  # Repeat to get enough tokens

print(f"System prompt size: {len(LARGE_CONTENT)} chars (~{len(LARGE_CONTENT)//4} tokens)")


def make_request(user_message: str) -> dict:
    """Make a chat request with cache control."""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    # Structure messages with cache_control on the system message
    messages = [
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": LARGE_CONTENT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        },
        {
            "role": "user",
            "content": user_message,
        },
    ]

    payload = {
        "model": "anthropic/claude-sonnet-4",
        "messages": messages,
        "max_tokens": 100,
    }

    response = requests.post(
        f"{BASE_URL}/chat/completions",
        headers=headers,
        json=payload,
    )
    response.raise_for_status()
    return response.json()


def print_usage(label: str, result: dict) -> None:
    """Print usage statistics from response."""
    usage = result.get("usage", {})
    print(f"\n=== {label} ===")
    print(f"Response: {result['choices'][0]['message']['content'][:100]}...")
    print(f"Usage:")
    print(f"  prompt_tokens: {usage.get('prompt_tokens', 'N/A')}")
    print(f"  completion_tokens: {usage.get('completion_tokens', 'N/A')}")
    print(f"  cache_creation_input_tokens: {usage.get('cache_creation_input_tokens', 'N/A')}")
    print(f"  cache_read_input_tokens: {usage.get('cache_read_input_tokens', 'N/A')}")
    
    # Also print raw usage for debugging
    print(f"  Raw usage dict: {json.dumps(usage, indent=4)}")


# First request - should create cache
print("\n" + "=" * 60)
print("Making FIRST request (should CREATE cache)...")
print("=" * 60)
result1 = make_request("What is the architecture of this system? Be brief.")
print_usage("FIRST REQUEST", result1)

# Second request - should read from cache
print("\n" + "=" * 60)
print("Making SECOND request (should READ from cache)...")
print("=" * 60)
result2 = make_request("What are the four layers? Be brief.")
print_usage("SECOND REQUEST", result2)

print("\n" + "=" * 60)
print("EXPECTED BEHAVIOR:")
print("  - First request: cache_creation_input_tokens > 0")
print("  - Second request: cache_read_input_tokens > 0")
print("=" * 60)
