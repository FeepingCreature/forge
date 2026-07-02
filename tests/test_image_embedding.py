"""Tests for output image embedding (forge.session.image_embedding)."""

import base64
import io

from forge.session.image_embedding import (
    IMAGES_DIR,
    LOW_RES_MAX_DIM,
    EmbeddedImage,
    embed_images_in_markdown,
)


class FakeVFS:
    """Minimal in-memory bytes VFS for embedding tests."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def file_exists(self, path: str) -> bool:
        return path in self.files

    def read_file_bytes(self, path: str) -> bytes:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def write_file_bytes(self, path: str, content: bytes) -> None:
        self.files[path] = content


def _png_bytes(width: int = 800, height: int = 600, color=(255, 0, 0)) -> bytes:
    from PIL import Image

    img = Image.new("RGB", (width, height), color)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _decode_data_url_dims(data_url: str) -> tuple[int, int]:
    from PIL import Image

    assert data_url.startswith("data:image/jpeg;base64,")
    b64 = data_url.split(",", 1)[1]
    raw = base64.b64decode(b64)
    with Image.open(io.BytesIO(raw)) as img:
        return img.size


class TestEmbedBasic:
    def test_resolves_and_rewrites_reference(self) -> None:
        vfs = FakeVFS()
        vfs.files["assets/pic.png"] = _png_bytes()
        content = "Here is a picture:\n\n![a red square](assets/pic.png)\n"

        rewritten, embedded = embed_images_in_markdown(vfs, content)

        assert len(embedded) == 1
        img = embedded[0]
        assert img.full_path.startswith(IMAGES_DIR + "/")
        assert img.full_path.endswith(".png")
        assert img.low_res_path.endswith(".low.jpg")
        # Reference rewritten to full-quality embedded path, alt preserved.
        assert f"![a red square]({img.full_path})" in rewritten
        assert "assets/pic.png" not in rewritten

    def test_stores_full_and_low_res_in_vfs(self) -> None:
        vfs = FakeVFS()
        original = _png_bytes()
        vfs.files["pic.png"] = original

        _, embedded = embed_images_in_markdown(vfs, "![x](pic.png)")

        img = embedded[0]
        # Full-quality copy is the exact original bytes.
        assert vfs.files[img.full_path] == original
        # Low-res copy exists and is a distinct (smaller-dimension) JPEG.
        assert img.low_res_path in vfs.files
        w, h = _decode_data_url_dims(img.data_url)
        assert max(w, h) <= LOW_RES_MAX_DIM

    def test_data_url_is_low_res_jpeg(self) -> None:
        vfs = FakeVFS()
        vfs.files["pic.png"] = _png_bytes(1024, 256)

        _, embedded = embed_images_in_markdown(vfs, "![x](pic.png)")

        w, h = _decode_data_url_dims(embedded[0].data_url)
        # Aspect ratio preserved, longest side clamped.
        assert w == LOW_RES_MAX_DIM
        assert h == LOW_RES_MAX_DIM // 4

    def test_sha256_content_addressed_path(self) -> None:
        import hashlib

        vfs = FakeVFS()
        data = _png_bytes()
        vfs.files["pic.png"] = data
        sha = hashlib.sha256(data).hexdigest()

        _, embedded = embed_images_in_markdown(vfs, "![x](pic.png)")

        assert embedded[0].full_path == f"{IMAGES_DIR}/{sha}.png"


class TestEmbedEdgeCases:
    def test_nonexistent_path_left_untouched(self) -> None:
        vfs = FakeVFS()
        content = "![missing](does/not/exist.png)"

        rewritten, embedded = embed_images_in_markdown(vfs, content)

        assert rewritten == content
        assert embedded == []

    def test_non_image_extension_ignored(self) -> None:
        vfs = FakeVFS()
        vfs.files["notes.txt"] = b"hello"
        content = "![text](notes.txt)"

        rewritten, embedded = embed_images_in_markdown(vfs, content)

        assert rewritten == content
        assert embedded == []

    def test_already_embedded_path_untouched(self) -> None:
        vfs = FakeVFS()
        content = f"![x]({IMAGES_DIR}/abc123.png)"

        rewritten, embedded = embed_images_in_markdown(vfs, content)

        assert rewritten == content
        assert embedded == []

    def test_idempotent_reembedding(self) -> None:
        vfs = FakeVFS()
        vfs.files["pic.png"] = _png_bytes()

        once, _ = embed_images_in_markdown(vfs, "![x](pic.png)")
        twice, embedded2 = embed_images_in_markdown(vfs, once)

        assert twice == once
        assert embedded2 == []

    def test_same_path_twice_processed_once(self) -> None:
        vfs = FakeVFS()
        vfs.files["pic.png"] = _png_bytes()
        content = "![a](pic.png) and again ![b](pic.png)"

        rewritten, embedded = embed_images_in_markdown(vfs, content)

        assert len(embedded) == 1
        full = embedded[0].full_path
        assert rewritten == f"![a]({full}) and again ![b]({full})"

    def test_undecodable_image_left_untouched(self) -> None:
        vfs = FakeVFS()
        vfs.files["broken.png"] = b"not really an image"
        content = "![broken](broken.png)"

        rewritten, embedded = embed_images_in_markdown(vfs, content)

        assert rewritten == content
        assert embedded == []

    def test_no_images_returns_content_unchanged(self) -> None:
        vfs = FakeVFS()
        content = "Just some **markdown** with a [link](http://example.com)."

        rewritten, embedded = embed_images_in_markdown(vfs, content)

        assert rewritten == content
        assert embedded == []

    def test_multiple_distinct_images(self) -> None:
        vfs = FakeVFS()
        vfs.files["a.png"] = _png_bytes(color=(255, 0, 0))
        vfs.files["b.jpg"] = _png_bytes(color=(0, 255, 0))
        content = "![a](a.png)\n![b](b.jpg)"

        rewritten, embedded = embed_images_in_markdown(vfs, content)

        assert len(embedded) == 2
        assert "a.png" not in rewritten
        assert "b.jpg" not in rewritten


class TestEmbeddedImageDataclass:
    def test_fields(self) -> None:
        img = EmbeddedImage(full_path="f", low_res_path="l", data_url="d")
        assert img.full_path == "f"
        assert img.low_res_path == "l"
        assert img.data_url == "d"
