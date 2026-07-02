"""
Output image embedding for assistant messages.

When the model (or a tool) references a repo image in its own chat output via
markdown ``![alt](path)``, that reference becomes a *permanent* part of the
conversation history and is replayed into every future API request. To keep
that sustainable we store two variants at finalization time:

- ``.forge/images/<sha256>.<ext>`` — full-quality copy of the original bytes,
  what the user's rendered chat displays. Committed/versioned like any file.
- ``.forge/images/<sha256>.low.jpg`` — a Pillow-downscaled (max 512px longest
  side, JPEG quality 70) copy. This is the copy embedded as base64 into the
  model-facing ``IMAGE_CONTENT`` block, since it is paid for on every single
  subsequent request forever.

The markdown reference is rewritten in place to point at the full-quality
``.forge/images/<sha256>.<ext>`` path so the user-facing rendering survives
the original source file later being edited or deleted.

This module is deliberately UI- and session-agnostic: it operates purely on a
VFS-like object (anything exposing ``file_exists``/``read_file_bytes``/
``write_file_bytes``) plus a markdown string, and returns the rewritten
markdown and a list of embed descriptors for the caller to feed into the
prompt manager.
"""

import base64
import hashlib
import io
import re
import warnings
from dataclasses import dataclass
from typing import Protocol

from PIL import UnidentifiedImageError

from forge.constants import IMAGE_EXTENSIONS

# Longest-side pixel cap and JPEG quality for the model-facing low-res copy.
# (See IMAGE_TODO.md "Resolved decisions".)
LOW_RES_MAX_DIM = 512
LOW_RES_JPEG_QUALITY = 70

IMAGES_DIR = ".forge/images"

# Markdown image syntax: ![alt](path) — capture alt and path separately.
# Path stops at whitespace or closing paren; we ignore optional "title".
_IMAGE_REF_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<path>[^)\s]+)\)")


class _BytesVFS(Protocol):
    def file_exists(self, path: str) -> bool: ...

    def read_file_bytes(self, path: str) -> bytes: ...

    def write_file_bytes(self, path: str, content: bytes) -> None: ...


@dataclass
class EmbeddedImage:
    """Descriptor for one embedded image, for the prompt manager.

    ``full_path`` is the committed full-quality file (also the rewritten
    markdown target). ``data_url`` is the base64 data URL of the *low-res*
    copy, i.e. what actually gets replayed to the model.
    """

    full_path: str
    low_res_path: str
    data_url: str


def _ext_of(path: str) -> str:
    """Return the lowercased extension (with dot) of a path, or ''."""
    dot = path.rfind(".")
    slash = max(path.rfind("/"), path.rfind("\\"))
    if dot <= slash:
        return ""
    return path[dot:].lower()


def _is_image_path(path: str) -> bool:
    return _ext_of(path) in IMAGE_EXTENSIONS


def _make_low_res_jpeg(data: bytes) -> bytes:
    """Downscale to <=LOW_RES_MAX_DIM longest side and re-encode as JPEG.

    Raises on undecodable input; the caller decides how to handle failure.
    """
    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        # Flatten alpha/palette to RGB so JPEG encoding always succeeds.
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.thumbnail((LOW_RES_MAX_DIM, LOW_RES_MAX_DIM), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=LOW_RES_JPEG_QUALITY)
        return out.getvalue()


def _low_res_sibling(full_path: str) -> str:
    """Given a full-quality ``.forge/images/<sha>.<ext>`` path, return the
    sibling ``.forge/images/<sha>.low.jpg`` low-res path.

    Assumes ``full_path`` is a ``.forge/images/`` path (caller checks).
    """
    ext = _ext_of(full_path)
    base = full_path[: -len(ext)] if ext else full_path
    return f"{base}.low.jpg"


def find_embedded_image_refs(content: str) -> list[str]:
    """Return the distinct ``.forge/images/<sha>.<ext>`` full-quality paths
    referenced by markdown image syntax in ``content``, in first-seen order.

    Only already-embedded refs (those under ``.forge/images/``) are returned;
    ``.low.jpg`` siblings are skipped since they are model-facing only. Used
    by replay to re-append IMAGE_CONTENT blocks for historical messages.
    """
    seen: list[str] = []
    for m in _IMAGE_REF_RE.finditer(content):
        path = m.group("path")
        if not path.startswith(IMAGES_DIR + "/"):
            continue
        if path.endswith(".low.jpg"):
            continue
        if path not in seen:
            seen.append(path)
    return seen


def embed_images_in_markdown(vfs: _BytesVFS, content: str) -> tuple[str, list[EmbeddedImage]]:
    """Scan ``content`` for markdown image refs resolving to existing VFS
    images, store dual-quality copies under ``.forge/images/``, rewrite the
    refs in place, and return ``(rewritten_content, embedded_images)``.

    Refs that don't resolve to an existing image file are left untouched
    (no fallback guessing). Already-rewritten ``.forge/images/...`` refs are
    left untouched so re-finalization/replay is idempotent.
    """
    embedded: list[EmbeddedImage] = []
    # Cache so the same source path referenced twice is only processed once.
    processed: dict[str, str] = {}

    def _replace(match: "re.Match[str]") -> str:
        alt = match.group("alt")
        path = match.group("path")

        # Already an embedded path — leave alone (idempotent).
        if path.startswith(IMAGES_DIR + "/"):
            return match.group(0)

        if not _is_image_path(path):
            return match.group(0)

        if path in processed:
            return f"![{alt}]({processed[path]})"

        if not vfs.file_exists(path):
            return match.group(0)

        try:
            data = vfs.read_file_bytes(path)
        except (FileNotFoundError, OSError):
            return match.group(0)

        ext = _ext_of(path) or ".png"
        sha = hashlib.sha256(data).hexdigest()
        full_path = f"{IMAGES_DIR}/{sha}{ext}"
        low_path = f"{IMAGES_DIR}/{sha}.low.jpg"

        try:
            low_bytes = _make_low_res_jpeg(data)
        except (UnidentifiedImageError, OSError) as exc:
            # Corrupted/undecodable image: warn and leave the reference
            # untouched rather than crashing or embedding something the model
            # can't see. We only swallow *decode* errors (UnidentifiedImageError
            # for unrecognized data, OSError for truncated/broken files); any
            # other exception is a real bug and propagates.
            warnings.warn(
                f"Could not decode image {path!r} for embedding ({exc}); "
                "leaving reference untouched",
                stacklevel=2,
            )
            return match.group(0)

        vfs.write_file_bytes(full_path, data)
        vfs.write_file_bytes(low_path, low_bytes)

        b64 = base64.b64encode(low_bytes).decode("ascii")
        data_url = f"data:image/jpeg;base64,{b64}"
        embedded.append(
            EmbeddedImage(full_path=full_path, low_res_path=low_path, data_url=data_url)
        )
        processed[path] = full_path
        return f"![{alt}]({full_path})"

    rewritten = _IMAGE_REF_RE.sub(_replace, content)
    return rewritten, embedded
