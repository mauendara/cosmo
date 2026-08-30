"""Locating Cosmo's own `templates/` tree and listing what's in it (spec 10.3).

`templates/` lives at the repo root, alongside `src/`, not inside the
installed package -- so it is resolved relative to this module's own file
location (`.../src/cosmo/bootstrap/discover.py` -> repo root is three
parents up) rather than via `importlib.resources`, which only covers files
shipped *inside* a package. This only resolves correctly for an editable
install (`uv tool install --editable .`), which is this project's documented
install method (see docs/handoff.md); a future packaged/wheel distribution
would need to ship `templates/` as real package data instead, and is
deliberately not solved here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cosmo


class TemplatesRootNotFoundError(FileNotFoundError):
    """Raised when Cosmo's own templates/ tree cannot be located.

    Fails loudly rather than silently operating on an empty template set --
    an editable install pointed at a moved or stripped-down checkout should
    error, not quietly produce empty `.agent/` directories."""


def templates_root() -> Path:
    package_dir = Path(cosmo.__file__).resolve().parent  # .../src/cosmo
    repo_root = package_dir.parent.parent  # .../src -> repo root
    root = repo_root / "templates"
    if not root.is_dir():
        raise TemplatesRootNotFoundError(
            f"Cosmo's templates/ directory was not found at {root}. "
            f"This requires an editable install (`uv tool install --editable .`) "
            f"from a full checkout of Cosmo's own repository."
        )
    return root


def harness_template_dir(harness: str, *, root: Path | None = None) -> Path:
    base = (root or templates_root()) / "harness" / harness
    if not base.is_dir():
        raise TemplatesRootNotFoundError(
            f"no template for harness {harness!r} at {base} -- "
            f"see `cosmo templates list` for what's available"
        )
    return base


def project_template_dir(name: str, *, root: Path | None = None) -> Path:
    base = (root or templates_root()) / "projects" / name
    if not base.is_dir():
        raise TemplatesRootNotFoundError(
            f"no project template named {name!r} at {base} -- "
            f"see `cosmo templates list` for what's available"
        )
    return base


@dataclass(frozen=True, slots=True)
class TemplatesListing:
    harnesses: list[str]
    project_templates: list[str]


def list_templates(*, root: Path | None = None) -> TemplatesListing:
    base = root or templates_root()
    harnesses = sorted(p.name for p in (base / "harness").iterdir() if p.is_dir())
    projects = sorted(p.name for p in (base / "projects").iterdir() if p.is_dir())
    return TemplatesListing(harnesses=harnesses, project_templates=projects)
