"""Tests for git-lfs pointer detection and resolution (forge/vfs/lfs.py)."""

import pytest

from forge.vfs.lfs import (
    LFSObjectMissingError,
    is_lfs_pointer,
    lfs_object_path,
    parse_lfs_pointer,
    resolve_lfs_bytes,
)

POINTER = (
    b"version https://git-lfs.github.com/spec/v1\n"
    b"oid sha256:4d7a214614ab2935c943f9e0ff69d22eadbb8f32b1258daaa5e2ca24d17e2393\n"
    b"size 12345\n"
)
OID = "4d7a214614ab2935c943f9e0ff69d22eadbb8f32b1258daaa5e2ca24d17e2393"


class TestIsLFSPointer:
    def test_recognizes_pointer(self):
        assert is_lfs_pointer(POINTER)

    def test_rejects_png_magic(self):
        assert not is_lfs_pointer(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    def test_rejects_plain_text(self):
        assert not is_lfs_pointer(b"hello world")

    def test_rejects_empty(self):
        assert not is_lfs_pointer(b"")

    def test_rejects_large_blob_even_with_prefix(self):
        # A real file that happens to start with the version line but is big
        # is not a pointer (pointers are tiny).
        big = POINTER + b"x" * 2000
        assert not is_lfs_pointer(big)


class TestParseLFSPointer:
    def test_parses_oid_and_size(self):
        oid, size = parse_lfs_pointer(POINTER)
        assert oid == OID
        assert size == 12345

    def test_missing_oid_raises(self):
        with pytest.raises(ValueError, match="Malformed LFS pointer"):
            parse_lfs_pointer(b"version https://git-lfs.github.com/spec/v1\nsize 5\n")

    def test_missing_size_raises(self):
        with pytest.raises(ValueError, match="Malformed LFS pointer"):
            parse_lfs_pointer(b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\n")


class TestLFSObjectPath:
    def test_shards_by_oid(self):
        path = lfs_object_path("/repo/.git", OID)
        assert path.as_posix() == f"/repo/.git/lfs/objects/4d/7a/{OID}"


class TestResolveLFSBytes:
    def test_returns_real_bytes_when_present(self, tmp_path):
        gitdir = tmp_path / ".git"
        obj = lfs_object_path(str(gitdir), OID)
        obj.parent.mkdir(parents=True)
        obj.write_bytes(b"real image bytes")

        result = resolve_lfs_bytes(str(gitdir), "images/x.png", POINTER)
        assert result == b"real image bytes"

    def test_raises_when_object_missing(self, tmp_path):
        gitdir = tmp_path / ".git"
        with pytest.raises(LFSObjectMissingError, match="not in the local"):
            resolve_lfs_bytes(str(gitdir), "images/x.png", POINTER)
