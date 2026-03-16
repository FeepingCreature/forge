"""
Web search tool - search DuckDuckGo via HTML scraping.

This is a conditionally-enabled built-in tool. Enable it per-repo by adding
"web_search" to the "enabled_tools" list in .forge/config.json.
"""

import html
import re
import urllib.parse
import urllib.request
from typing import Any

# Mark this tool as requiring explicit opt-in
CONDITIONAL = True


def get_skill() -> tuple[str, str] | None:
    """Return skill documentation for web tools."""
    return (
        "web_tools",
        """\
# Web Search and Web Read Tools

These tools are **conditionally enabled** per-repository. To enable them,
add to your `.forge/config.json`:

```json
{
  "enabled_tools": ["web_search", "web_read"]
}
```

## web_search

Search the web using DuckDuckGo. Returns titles, URLs, and snippets.

```
web_search(query="python asyncio tutorial", max_results=5)
```

## web_read

Fetch a webpage and extract its content as clean text. Optionally focus
extraction on a specific question.

```
web_read(url="https://docs.python.org/3/library/asyncio.html")
web_read(url="https://example.com/api", question="What are the auth endpoints?")
```

## Typical Workflow

1. Search for what you need: `web_search(query="...")`
2. Read promising results: `web_read(url="...", question="...")`
3. Use the information in your work
""",
    )


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM."""
    return {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web using DuckDuckGo. Returns titles, URLs, and snippets "
                "for the top results. Use this to find documentation, look up APIs, "
                "or research solutions.\n\n"
                "Tip: Use specific, targeted queries for best results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return (default: 10)",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        },
    }


def execute(vfs: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Execute a DuckDuckGo search and return parsed results."""
    query = args.get("query", "")
    max_results = args.get("max_results", 10)

    if not query:
        return {"success": False, "error": "No query specified"}

    results = _search_ddg(query, max_results)
    if results is None:
        return {"success": False, "error": "Search request failed"}

    if not results:
        return {"success": True, "results": [], "message": "No results found"}

    return {
        "success": True,
        "results": results,
        "message": f"Found {len(results)} results",
    }


def _search_ddg(query: str, max_results: int) -> list[dict[str, str]] | None:
    """Search DuckDuckGo HTML and parse results.

    Uses the HTML version of DuckDuckGo (html.duckduckgo.com) which returns
    a simple page we can parse without JavaScript rendering.
    """
    url = "https://html.duckduckgo.com/html/"
    data = urllib.parse.urlencode({"q": query}).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.5",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            body = response.read().decode("utf-8", errors="replace")
    except Exception as e:
        return None

    return _parse_ddg_html(body, max_results)


def _parse_ddg_html(body: str, max_results: int) -> list[dict[str, str]]:
    """Parse DuckDuckGo HTML results page.

    The HTML version uses a consistent structure:
    - Each result is in a div with class "result"
    - Title is in an <a class="result__a"> tag
    - Snippet is in an <a class="result__snippet"> tag
    - URL is in the href of result__a (goes through a redirect)
    """
    results: list[dict[str, str]] = []

    # Match each result block
    result_blocks = re.findall(
        r'<div[^>]*class="[^"]*result\b[^"]*"[^>]*>(.*?)</div>\s*(?=<div|$)',
        body,
        re.DOTALL,
    )

    # Fallback: try matching result links and snippets directly
    if not result_blocks:
        result_blocks = re.findall(
            r'class="result__body">(.*?)</div>',
            body,
            re.DOTALL,
        )

    if not result_blocks:
        # Try a more lenient approach - find all result__a links
        return _parse_ddg_html_fallback(body, max_results)

    for block in result_blocks:
        if len(results) >= max_results:
            break

        result = _parse_result_block(block)
        if result:
            results.append(result)

    return results


def _parse_result_block(block: str) -> dict[str, str] | None:
    """Parse a single result block from DuckDuckGo HTML."""
    # Extract title and URL from result__a
    title_match = re.search(
        r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        block,
        re.DOTALL,
    )
    if not title_match:
        return None

    raw_url = title_match.group(1)
    raw_title = title_match.group(2)

    # Extract snippet
    snippet_match = re.search(
        r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        block,
        re.DOTALL,
    )
    snippet = _clean_html(snippet_match.group(1)) if snippet_match else ""

    # Clean up URL - DuckDuckGo wraps URLs in a redirect
    url = _extract_url(raw_url)
    title = _clean_html(raw_title)

    if not title or not url:
        return None

    return {"title": title, "url": url, "snippet": snippet}


def _parse_ddg_html_fallback(body: str, max_results: int) -> list[dict[str, str]]:
    """Fallback parser that finds result links more leniently."""
    results: list[dict[str, str]] = []

    # Find all result__a links
    links = re.finditer(
        r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        body,
        re.DOTALL,
    )

    for match in links:
        if len(results) >= max_results:
            break

        raw_url = match.group(1)
        raw_title = match.group(2)

        url = _extract_url(raw_url)
        title = _clean_html(raw_title)

        if title and url:
            # Try to find a nearby snippet
            pos = match.end()
            snippet_match = re.search(
                r'class="result__snippet"[^>]*>(.*?)</a>',
                body[pos : pos + 2000],
                re.DOTALL,
            )
            snippet = _clean_html(snippet_match.group(1)) if snippet_match else ""

            results.append({"title": title, "url": url, "snippet": snippet})

    return results


def _extract_url(raw_url: str) -> str:
    """Extract the actual URL from DuckDuckGo's redirect wrapper."""
    # DDG wraps URLs like: //duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com&...
    if "uddg=" in raw_url:
        match = re.search(r"uddg=([^&]+)", raw_url)
        if match:
            return urllib.parse.unquote(match.group(1))

    # Sometimes URLs are direct
    if raw_url.startswith("http"):
        return raw_url

    # Handle protocol-relative URLs
    if raw_url.startswith("//"):
        return "https:" + raw_url

    return raw_url


def _clean_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    # Remove tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode HTML entities
    text = html.unescape(text)
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text