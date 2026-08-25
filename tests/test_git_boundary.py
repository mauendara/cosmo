"""Structural invariants for `src/cosmo/git/` (spec 3.2, 3.4, plan Phase 5
exit criteria) -- enforced by test rather than by discipline, the same
posture `test_harness_boundary.py` takes for the harness abstraction.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "cosmo"
GIT_MODULE = SRC / "git"

_MASTER_TOKEN = re.compile(r"\bmaster\b", re.IGNORECASE)


def _python_files() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


def test_master_is_never_a_merge_target() -> None:
    """Spec 3.2: merging `develop` -> `master` is manual, developer-performed,
    and explicitly out of scope. The only permitted appearance of the literal
    token is a comment explaining the exclusion."""
    offenders: list[str] = []
    for path in _python_files():
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if not _MASTER_TOKEN.search(line):
                continue
            if line.strip().startswith("#"):
                continue
            offenders.append(f"{path.relative_to(SRC)}:{lineno}: {line.strip()}")
    assert not offenders, f"'master' must never be named as a merge target (spec 3.2): {offenders}"


def _names_harness(dotted: str) -> bool:
    return dotted == "cosmo.harness" or dotted.startswith("cosmo.harness.")


def _imports_harness(path: Path) -> bool:
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(_names_harness(a.name) for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and _names_harness(node.module or ""):
            return True
    return False


def test_merge_ladder_never_imports_the_harness_layer() -> None:
    """Spec 3.4 step 2: the conflict is never handed back to the agent to
    resolve blind. Enforced structurally -- the merge/rebase code path must
    never have a harness adapter in scope at all, not merely by convention.
    Checks real `import`/`from ... import` statements (via `ast`), not a
    text search, so a docstring explaining this invariant doesn't trip it."""
    offenders = [str(p.relative_to(SRC)) for p in GIT_MODULE.glob("*.py") if _imports_harness(p)]
    assert not offenders, f"src/cosmo/git/ must never import the harness layer: {offenders}"
