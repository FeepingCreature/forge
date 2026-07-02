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

LFS is an implementation detail: callers get real bytes for free. On read, if a
blob is a pointer but the object isn't present locally, we first try to fetch
it via the ``git lfs`` CLI, and only raise if that fails — errors are holy, no
silent fallbacks. On commit, bytes written to an LFS-tracked path are "cleaned":
the real bytes go into the local LFS store and a pointer blob is committed to
the tree in their place.
"""

import fnmatch
import hashlib
import subprocess
from pathlib import Path

# An LFS pointer always begins with this version line. The spec allows other
# version URLs in principle, but git-lfs itself only ever writes this one.
_LFS_VERSION_LINE = "version https://git-lfs.github.com/spec/v1"
_LFS_VERSION_PREFIX = _LFS_VERSION_LINE.encode()

# Pointers are tiny; a real file starting with the version line by coincidence
# would still have to be small AND parse as a valid pointer, so cap the check.
_MAX_POINTER_SIZE = 1024


class LFSObjectMissingError(FileNotFoundError):
    """Raised when a blob is an LFS pointer and the object can't be obtained."""


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


def resolve_lfs_bytes(gitdir: str, path: str, pointer: bytes, workdir: str | None = None) -> bytes:
    """Resolve the real bytes for an LFS pointer blob.

    ``gitdir`` is the repository's git directory (``pygit2.Repository.path``).
    ``workdir`` is the repository working directory, needed to run ``git lfs``
    for autofetch; if None, no fetch is attempted.

    First checks the local LFS store. On a miss, attempts a ``git lfs pull``
    for this path and re-checks. Raises LFSObjectMissingError if the object
    still can't be obtained.
    """
    oid, size = parse_lfs_pointer(pointer)
    obj_path = lfs_object_path(gitdir, oid)
    if obj_path.exists():
        return obj_path.read_bytes()

    # Not in the local store — try to fetch it over the network via the CLI.
    if workdir is not None:
        git_lfs_pull(workdir, path)

    if not obj_path.exists():
        raise LFSObjectMissingError(
            f"LFS object for {path!r} (oid {oid}, {size} bytes) is not in the local "
            f"store at {obj_path} and could not be fetched. Check network access and "
            f"that 'git lfs' is installed, then run 'git lfs pull'."
        )
    return obj_path.read_bytes()


def git_lfs_pull(workdir: str, path: str) -> None:
    """Fetch a single LFS object into the local store via the ``git lfs`` CLI.

    Shelling out to git-lfs (the reference implementation) means remotes,
    ``.lfsconfig``, auth, and the batch API are all handled correctly. We scope
    the pull to just ``path`` so we don't drag down every LFS object.

    Failures (git-lfs not installed, network down, path not on the remote) are
    left for the caller to surface: this returns normally and the caller
    re-checks the store, raising LFSObjectMissingError if still absent.
    """
    subprocess.run(
        ["git", "lfs", "pull", "--include", path, "--exclude", ""],
        cwd=workdir,
        capture_output=True,
        check=False,
    )


def make_lfs_pointer(data: bytes) -> bytes:
    """Build the git-lfs pointer text for ``data``.

    The pointer is the canonical 3-line form git-lfs writes:

        version https://git-lfs.github.com/spec/v1
        oid sha256:<hex>
        size <bytes>
    """
    oid = hashlib.sha256(data).hexdigest()
    return f"{_LFS_VERSION_LINE}\noid sha256:{oid}\nsize {len(data)}\n".encode()


def write_lfs_object(gitdir: str, data: bytes) -> str:
    """Write ``data`` into the local LFS object store, returning its sha256 oid.

    Idempotent: if the object already exists it is left untouched (content is
    addressed by hash, so an existing file is by definition identical).
    """
    oid = hashlib.sha256(data).hexdigest()
    obj_path = lfs_object_path(gitdir, oid)
    if not obj_path.exists():
        obj_path.parent.mkdir(parents=True, exist_ok=True)
        obj_path.write_bytes(data)
    return oid


def parse_lfs_attributes(gitattributes: str) -> list[str]:
    """Return the glob patterns marked ``filter=lfs`` in a ``.gitattributes``.

    Each non-comment line is ``<pattern> attr1 attr2 ...`` (e.g.
    ``*.png filter=lfs diff=lfs merge=lfs -text``). We collect the pattern of
    every line whose attributes include ``filter=lfs``.
    """
    patterns: list[str] = []
    for raw in gitattributes.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        pattern, attrs = fields[0], fields[1:]
        if "filter=lfs" in attrs:
            patterns.append(pattern)
    return patterns


def is_lfs_tracked(path: str, patterns: list[str]) -> bool:
    """Return True if ``path`` matches any ``filter=lfs`` gitattributes pattern.

    Patterns are matched gitattributes-style: a pattern without a slash matches
    against the basename (so ``*.png`` matches ``a/b/c.png``); a pattern with a
    slash matches against the full path.
    """
    basename = path.rsplit("/", 1)[-1]
    for pattern in patterns:
        target = path if "/" in pattern else basename
        if fnmatch.fnmatch(target, pattern):
            return True
    return False
