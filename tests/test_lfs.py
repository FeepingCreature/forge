"""Tests for git-lfs pointer detection and resolution (forge/vfs/lfs.py)."""

import pytest

from forge.vfs.lfs import (
    LFSObjectMissingError,
    is_lfs_pointer,
    is_lfs_tracked,
    lfs_object_path,
    make_lfs_pointer,
    parse_lfs_attributes,
    parse_lfs_pointer,
    resolve_lfs_bytes,
    write_lfs_object,
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
        # workdir=None means no fetch is attempted, so it raises immediately.
        with pytest.raises(LFSObjectMissingError, match="could not be fetched"):
            resolve_lfs_bytes(str(gitdir), "images/x.png", POINTER)


class TestMakeLFSPointer:
    def test_roundtrips_with_parse(self):
        data = b"the real image bytes"
        pointer = make_lfs_pointer(data)
        assert is_lfs_pointer(pointer)
        oid, size = parse_lfs_pointer(pointer)
        assert size == len(data)
        # oid is the sha256 of the content
        import hashlib

        assert oid == hashlib.sha256(data).hexdigest()

    def test_canonical_three_line_form(self):
        pointer = make_lfs_pointer(b"x").decode()
        lines = pointer.splitlines()
        assert lines[0] == "version https://git-lfs.github.com/spec/v1"
        assert lines[1].startswith("oid sha256:")
        assert lines[2] == "size 1"


class TestWriteLFSObject:
    def test_writes_and_returns_oid(self, tmp_path):
        import hashlib

        gitdir = tmp_path / ".git"
        data = b"hello lfs"
        oid = write_lfs_object(str(gitdir), data)
        assert oid == hashlib.sha256(data).hexdigest()
        assert lfs_object_path(str(gitdir), oid).read_bytes() == data

    def test_idempotent(self, tmp_path):
        gitdir = tmp_path / ".git"
        data = b"hello lfs"
        oid1 = write_lfs_object(str(gitdir), data)
        oid2 = write_lfs_object(str(gitdir), data)
        assert oid1 == oid2

    def test_write_then_resolve_roundtrip(self, tmp_path):
        gitdir = tmp_path / ".git"
        data = b"roundtrip bytes"
        write_lfs_object(str(gitdir), data)
        pointer = make_lfs_pointer(data)
        assert resolve_lfs_bytes(str(gitdir), "a.png", pointer) == data


class TestParseLFSAttributes:
    def test_extracts_filter_lfs_patterns(self):
        text = (
            "*.png filter=lfs diff=lfs merge=lfs -text\n"
            "*.txt text\n"
            "*.psd filter=lfs diff=lfs merge=lfs -text\n"
        )
        assert parse_lfs_attributes(text) == ["*.png", "*.psd"]

    def test_ignores_comments_and_blanks(self):
        text = "# a comment\n\n*.bin filter=lfs -text\n"
        assert parse_lfs_attributes(text) == ["*.bin"]

    def test_no_lfs_lines(self):
        assert parse_lfs_attributes("*.txt text\n") == []


class TestIsLFSTracked:
    def test_basename_glob_matches_nested(self):
        assert is_lfs_tracked("assets/img/logo.png", ["*.png"])

    def test_non_matching_extension(self):
        assert not is_lfs_tracked("a/b.txt", ["*.png"])

    def test_pattern_with_slash_matches_full_path(self):
        assert is_lfs_tracked("assets/logo.png", ["assets/*.png"])
        assert not is_lfs_tracked("other/logo.png", ["assets/*.png"])

    def test_empty_patterns(self):
        assert not is_lfs_tracked("a.png", [])
