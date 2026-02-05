"""
Utility for caching external JavaScript libraries locally.

Downloads JS files from CDN on first use and serves from cache thereafter.
This improves startup time and allows offline usage.
"""

import hashlib
import urllib.request
from pathlib import Path

# Cache directory for JS files
JS_CACHE_DIR = Path.home() / ".cache" / "forge" / "js"

# External scripts to cache with their CDN URLs
EXTERNAL_SCRIPTS = {
    "mathjax": "https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js",
    "mermaid": "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js",
}


def _get_cache_path(name: str, url: str) -> Path:
    """Get cache path for a script, including URL hash for cache busting."""
    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
    return JS_CACHE_DIR / f"{name}-{url_hash}.js"


def _download_script(url: str, cache_path: Path) -> bool:
    """Download script from URL to cache path. Returns True on success."""
    try:
        JS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=10) as response:
            content = response.read()
            cache_path.write_bytes(content)
        return True
    except Exception as e:
        print(f"[JS Cache] Failed to download {url}: {e}")
        return False


def get_script_tag(name: str, onload: str | None = None) -> str:
    """Get HTML script tag for a cached script.

    Returns a file:// URL if cached, otherwise falls back to CDN URL.
    Downloads and caches on first access.

    Args:
        name: Script name (e.g., 'mathjax', 'mermaid')
        onload: Optional JavaScript to run when script loads

    Returns:
        HTML <script> tag string
    """
    if name not in EXTERNAL_SCRIPTS:
        raise ValueError(f"Unknown script: {name}")

    url = EXTERNAL_SCRIPTS[name]
    cache_path = _get_cache_path(name, url)

    # Build onload attribute if provided
    onload_attr = f' onload="{onload}"' if onload else ""

    # Ensure script is cached
    if not cache_path.exists():
        _download_script(url, cache_path)

    # Try to use cached version
    if cache_path.exists():
        return f'<script src="file://{cache_path}"{onload_attr}></script>'

    # Fall back to CDN
    return f'<script src="{url}"{onload_attr}></script>'


def get_all_script_tags() -> str:
    """Get script tags for all external scripts, with appropriate onload handlers."""
    tags = []
    for name in EXTERNAL_SCRIPTS:
        if name == "mermaid":
            # Initialize mermaid when it loads
            tags.append(get_script_tag(name, onload="initMermaid()"))
        else:
            tags.append(get_script_tag(name))
    return "\n            ".join(tags)


def precache_all() -> None:
    """Pre-download all scripts to cache. Call during app startup."""
    for name, url in EXTERNAL_SCRIPTS.items():
        cache_path = _get_cache_path(name, url)
        if not cache_path.exists():
            _download_script(url, cache_path)
