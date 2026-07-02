"""
Git LFS pointer detection and resolution.

pygit2 reads git objects directly from the object database and never runs
git's smudge/clean filters (those only apply to the working tree). So for an
LFS-tracked file, ``blob.data`` is the small pointer text, not the real
content:

    version https://git-lfs.github.com/spec/v1
    oid sha256:4d7a2146...
    size 12345

This module detects that pointer and resolves the real bytes from the local
LFS object store at ``<gitdir>/lfs/objects/<oid[0:2]>/<oid[2:4]>/<oid>``.

LFS is an implementation detail: callers get real bytes for free. If a blob is
a pointer but the object isn't present locally (not fetched), we raise rather
than hand back the pointer text — errors are holy, no silent fallbacks.
"""

from pathlib import Path

# An LFS pointer always begins with this version line. The spec allows other
# version URLs in principle, but git-lfs itself only ever writes this one.
_LFS_VERSION_PREFIX = b"version https://git-lfs.github.com/spec/v1"

# Pointers are tiny; a real file starting with the version line by coincidence
# would still have to be small AND parse as a valid pointer, so cap the check.
_MAX_POINTER_SIZE = 1024


class LFSObjectMissingError(FileNotFoundError):
    """Raised when a blob is an LFS pointer but the object isn't fetched locally."""


def is_lfs_pointer(data: bytes) -> bool:
    """Return True if ``data`` is a git-lfs pointer blob."""
    if len(data) > _MAX_POINTER_SIZE:
        return False
    return data.startswith(_LFS_VERSION_PREFIX)


def parse_lfs_pointer(data: bytes) -> tuple[str, int]:
    """Parse an LFS pointer blob, returning ``(oid_hex, size)``.

    The oid is the ``sha256:<hex>`` value with the algorithm prefix stripped.
    Raises ValueError if the pointer is malformed.
    """
    oid: str | None = None
    size: int | None = None
    for line in data.decode("utf-8").splitlines():
        if line.startswith("oid sha256:"):
            oid = line[len("oid sha256:") :].strip()
        elif line.startswith("size "):
            size = int(line[len("size ") :].strip())
    if oid is None or size is None:
        raise ValueError(f"Malformed LFS pointer (missing oid/size): {data!r}")
    return oid, size


def lfs_object_path(gitdir: str, oid: str) -> Path:
    """Return the local LFS object store path for a sha256 ``oid``.

    git-lfs shards objects as ``lfs/objects/<oid[0:2]>/<oid[2:4]>/<oid>``.
    """
    return Path(gitdir) / "lfs" / "objects" / oid[0:2] / oid[2:4] / oid


def resolve_lfs_bytes(gitdir: str, path: str, pointer: bytes) -> bytes:
    """Resolve the real bytes for an LFS pointer blob from the local store.

    ``gitdir`` is the repository's git directory (``pygit2.Repository.path``).
    ``path`` is only used for the error message. Raises LFSObjectMissingError
    if the object hasn't been fetched into the local LFS store.
    """
    oid, size = parse_lfs_pointer(pointer)
    obj_path = lfs_object_path(gitdir, oid)
    if not obj_path.exists():
        raise LFSObjectMissingError(
            f"LFS object for {path!r} (oid {oid}, {size} bytes) is not in the local "
            f"store at {obj_path}. Run 'git lfs fetch' / 'git lfs pull' to download it."
        )
    return obj_path.read_bytes()
