"""`template_version` (spec 9.2): "a hash of the source template tree".

Underspecified in the spec -- there is no single obviously-correct way to
hash a directory tree, and different reasonable implementations produce
different (but equally valid) answers. This one: for every regular file
under the tree, sorted by POSIX-style relative path, hash the file's
content; then hash the newline-joined `"<relative_path> <content_hash>"`
manifest. Sorting first makes the result independent of filesystem
iteration order; hashing content (not mtime) means a copy with identical
bytes produces an identical version, and any real content change -- however
small -- changes it. Recorded here, not left implicit, exactly because the
spec calls out that the choice needs documenting.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# Excluded everywhere a template tree is walked (here and in `assets.py`'s
# copytree). A hook script under templates/harness/claude/hooks/ imports a
# sibling module (`_hooklib.py`), and simply running it -- including from
# this project's own test suite -- leaves a `__pycache__/*.pyc` next to it.
# Without this exclusion that bytecode becomes part of the hashed/copied
# template tree: non-deterministic across machines/Python builds, and stale
# bytecode would get copied into every target repo's `.agent/`.
_IGNORED_DIR_NAMES = frozenset({"__pycache__"})
_IGNORED_SUFFIXES = frozenset({".pyc", ".pyo"})


def _is_ignored(path: Path) -> bool:
    return any(part in _IGNORED_DIR_NAMES for part in path.parts) or path.suffix in (
        _IGNORED_SUFFIXES
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_template_version(tree: Path) -> str:
    """Sha256 over a sorted `path content_hash` manifest of every file under
    `tree`. Deterministic across machines and filesystem orderings."""
    entries = sorted(p for p in tree.rglob("*") if p.is_file() and not _is_ignored(p))
    manifest_lines = [
        f"{entry.relative_to(tree).as_posix()} {_file_sha256(entry)}" for entry in entries
    ]
    manifest = "\n".join(manifest_lines)
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()
