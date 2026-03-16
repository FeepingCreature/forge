"""
Web read tool - fetch a webpage and extract its content.

Uses the scout (cheap) model to extract relevant information from raw HTML,
producing a clean readable summary.

This is a conditionally-enabled built-in tool. Enable it per-repo by adding
"web_read" to the "enabled_tools" list in .forge/config.json.
"""

import re
import urllib.request
from typing import Any

from forge.config.settings import Settings
from forge.llm.client import LLMClient

# Mark this tool as requiring explicit opt-in
CONDITIONAL = True


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM."""
    return {
        "type": "function",
        "function": {
            "name": "web_read",
            "description": (
                "Fetch a webpage and extract its text content. Uses a smaller model to "
                "parse the HTML and return clean, readable text.\n\n"
                "Good for:\n"
                "- Reading documentation pages\n"
                "- Checking API references\n"
                "- Reading blog posts or articles\n"
                "- Extracting code examples from web pages\n\n"
                "Optionally pass a question to focus the extraction on specific information."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch",
                    },
                    "question": {
                        "type": "string",
                        "description": (
                            "Optional: focus extraction on answering this question. "
                            "Without this, returns the full page content."
                        ),
                    },
                },
                "required": ["url"],
            },
        },
    }


def execute(vfs: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Fetch a webpage and extract its content."""
    url = args.get("url", "")
    question = args.get("question", "")

    if not url:
        return {"success": False, "error": "No URL specified"}

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Fetch the page
    body = _fetch_page(url)
    if body is None:
        return {"success": False, "error": f"Failed to fetch {url}"}

    # Strip obvious non-content (scripts, styles, nav) before sending to LLM
    cleaned = _strip_non_content(body)

    # Truncate to avoid blowing up the cheap model's context
    max_chars = 100_000
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + "\n\n[... truncated ...]"

    # Use scout model to extract content
    content = _extract_with_llm(url, cleaned, question)
    if content is None:
        return {"success": False, "error": "Failed to extract content from page"}

    return {
        "success": True,
        "url": url,
        "content": content,
    }


def _fetch_page(url: str) -> str | None:
    """Fetch a webpage and return its HTML."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            # Check content type
            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                # For plain text or other readable formats, still try
                if "application/json" in content_type:
                    body = response.read().decode("utf-8", errors="replace")
                    return body
                # Skip binary content
                if any(
                    t in content_type
                    for t in ["image/", "audio/", "video/", "application/octet"]
                ):
                    return None

            return response.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _strip_non_content(html_text: str) -> str:
    """Remove scripts, styles, and other non-content HTML to reduce size."""
    # Remove script and style blocks
    text = re.sub(r"<script[^>]*>.*?</script>", "", html_text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Remove SVG blocks (often large, not useful as text)
    text = re.sub(r"<svg[^>]*>.*?</svg>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML comments
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    # Remove common non-content elements
    for tag in ["nav", "footer", "header"]:
        text = re.sub(
            rf"<{tag}[^>]*>.*?</{tag}>", "", text, flags=re.DOTALL | re.IGNORECASE
        )
    # Collapse excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _extract_with_llm(url: str, html_text: str, question: str) -> str | None:
    """Use the scout model to extract readable content from HTML."""
    settings = Settings()
    api_key = settings.get_api_key()
    model = settings.get_summarization_model()

    if not api_key:
        return None

    client = LLMClient(api_key, model)

    if question:
        instruction = (
            f"Extract the relevant information from this webpage to answer: {question}\n\n"
            "Include code examples, API signatures, and specific details. "
            "Skip navigation, ads, and boilerplate."
        )
    else:
        instruction = (
            "Extract the main content from this webpage as clean, readable text. "
            "Preserve code blocks, headings, lists, and important formatting. "
            "Skip navigation, ads, sidebars, and boilerplate."
        )

    prompt = f"""You are a web content extractor. Given raw HTML from a webpage, extract the useful content.

URL: {url}

{instruction}

RAW HTML:
{html_text}"""

    messages = [{"role": "user", "content": prompt}]

    response = client.chat(messages)

    choices = response.get("choices", [])
    if not choices:
        return None

    content: str = choices[0].get("message", {}).get("content", "")
    return content