"""Structural invariant for `src/cosmo/gate/` (spec 2.2, plan Phase 6): the
validation gate bypasses the LLM harness entirely, so nothing in this
package may import `cosmo.harness` -- the same `ast`-based guarantee
`test_git_boundary.py` built for `cosmo.git.merge` (Phase 5's handoff
explicitly floated extending this ban to `cosmo.gate` too).
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "cosmo"
GATE_MODULE = SRC / "gate"


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


def test_gate_never_imports_the_harness_layer() -> None:
    """Spec 2.2: `validate()` "bypasses the LLM harness entirely (direct
    Docker invocation)". Checks real `import`/`from ... import` statements
    via `ast`, not a text search, so a docstring explaining this invariant
    (like this file's own, and `runner.py`'s) never trips it."""
    offenders = [
        str(p.relative_to(SRC))
        for p in GATE_MODULE.rglob("*.py")
        if "__pycache__" not in p.parts and _imports_harness(p)
    ]
    assert not offenders, f"src/cosmo/gate/ must never import the harness layer: {offenders}"
