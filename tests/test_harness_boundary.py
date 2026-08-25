"""The harness abstraction boundary, enforced by test rather than by discipline.

Spec 2: "Cosmo never talks to a specific harness directly." A generic doctor
check for ANTHROPIC_API_KEY would be meaningless to a Cursor or Codex adapter and
would hardcode Claude into the core. These tests fail if that creeps back in.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "cosmo"

# Modules permitted to name a specific harness.
ALLOWED_HARNESS_AWARE = {
    SRC / "harness" / "claude" / "__init__.py",
    SRC / "harness" / "claude" / "adapter.py",
    SRC / "harness" / "claude" / "stream.py",
    SRC / "harness" / "registry.py",  # maps names to classes; that is its job
    SRC / "config" / "defaults.toml",  # configuration data, not logic
    # Spec 10.2: which paths a harness expects symlinked at the target repo's
    # root is a genuinely per-harness convention (Claude wants `.claude` and
    # `CLAUDE.md`; a future harness would want its own names) -- this module's
    # whole job is knowing that mapping, the same shape as the adapter/registry
    # entries above.
    SRC / "bootstrap" / "symlinks.py",
}

HARNESS_SPECIFIC_TOKENS = [
    "ANTHROPIC_API_KEY",
    "stream-json",
    "--permission-mode",
    "dangerously-skip-permissions",
    "max-turns",
]


def _core_python_files() -> list[Path]:
    return [
        p
        for p in SRC.rglob("*.py")
        if p not in ALLOWED_HARNESS_AWARE and "__pycache__" not in p.parts
    ]


@pytest.mark.parametrize("token", HARNESS_SPECIFIC_TOKENS)
def test_core_never_names_harness_specific_tokens(token: str) -> None:
    offenders = [str(p.relative_to(SRC)) for p in _core_python_files() if token in p.read_text()]
    assert not offenders, (
        f"{token!r} is harness-specific and must not appear in core modules: {offenders}. "
        f"Put it behind the adapter's preflight()/capabilities instead (spec 2)."
    )


def test_core_never_hardcodes_a_harness_name() -> None:
    """The literal 'claude' may appear only in the adapter, registry, and config data."""
    pattern = re.compile(r"""["']claude["']""")
    offenders = [
        str(p.relative_to(SRC)) for p in _core_python_files() if pattern.search(p.read_text())
    ]
    assert not offenders, f"core modules hardcode a harness name: {offenders}"


def test_doctor_module_imports_no_concrete_adapter() -> None:
    source = (SRC / "doctor.py").read_text()
    assert "cosmo.harness" not in source, (
        "core doctor checks must not import the harness layer at all; the CLI "
        "composes core checks with the resolved adapter's preflight()"
    )
