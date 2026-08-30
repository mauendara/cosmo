"""Root-level harness-facing symlinks (spec 10.2), relative-only.

An absolute or cross-repo symlink breaks the moment a target repo moves
between the droplet and a developer's WSL2 box, or is cloned elsewhere --
every link created here is computed relative to its own location so it
survives the whole repo moving as a unit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Spec 10.2: link name -> path relative to `.agent/<harness>/` it points at.
# "" means the `.agent/<harness>/` directory itself.
HARNESS_ROOT_LINKS: dict[str, tuple[tuple[str, str], ...]] = {
    "claude": (
        ("CLAUDE.md", "CLAUDE.md"),
        (".claude", ""),
        ("agents", "agents"),
        ("skills", "skills"),
    ),
}


@dataclass(frozen=True, slots=True)
class SymlinkResult:
    link_name: str
    link_path: Path
    points_to: str  # the relative target actually written, for assertions
    status: str  # "created" | "refreshed" | "skipped_conflict" | "skipped_missing_target"
    detail: str


def create_root_symlinks(target: Path, harness: str) -> list[SymlinkResult]:
    """Create or refresh this harness's root-level symlinks in `target`.

    Only refreshes links this function itself owns (existing symlinks at the
    same path); a real file or directory already occupying a link's path is
    left untouched and reported as a conflict rather than clobbered -- that
    path may be the developer's own content, not something Cosmo put there.
    """
    agent_dir = target / ".agent" / harness
    results: list[SymlinkResult] = []

    for link_name, rel_within_agent in HARNESS_ROOT_LINKS.get(harness, ()):
        link_path = target / link_name
        real_target = agent_dir if rel_within_agent == "" else agent_dir / rel_within_agent

        if not real_target.exists():
            results.append(
                SymlinkResult(
                    link_name=link_name,
                    link_path=link_path,
                    points_to="",
                    status="skipped_missing_target",
                    detail=f"{real_target} does not exist -- nothing to link to",
                )
            )
            continue

        status = "created"
        if link_path.is_symlink():
            link_path.unlink()
            status = "refreshed"
        elif link_path.exists():
            results.append(
                SymlinkResult(
                    link_name=link_name,
                    link_path=link_path,
                    points_to="",
                    status="skipped_conflict",
                    detail=f"{link_path} already exists and is not a symlink -- not overwritten",
                )
            )
            continue

        relative = os.path.relpath(real_target, start=link_path.parent)
        os.symlink(relative, link_path)
        results.append(
            SymlinkResult(
                link_name=link_name,
                link_path=link_path,
                points_to=relative,
                status=status,
                detail=relative,
            )
        )

    return results
