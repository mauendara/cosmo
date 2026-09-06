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
    # Real Codex CLI 0.153.0 validation found that a directory-level
    # `.codex -> .agent/codex` link prevents its workspace-write sandbox from
    # starting: the sandbox rejects the symlink mount before any tool runs.
    # Hooks are injected explicitly and skills are discovered below
    # `.agents/skills`, so `.codex` is neither safe nor needed.
    "codex": ((".agents/skills", "skills"),),
    "claude": (
        ("CLAUDE.md", "CLAUDE.md"),
        (".claude", ""),
        ("agents", "agents"),
        ("skills", "skills"),
    ),
    # Same four names: the tool reading them is still Claude Code, so it
    # still looks for `.claude`/`CLAUDE.md` -- only the `.agent/<harness>/`
    # tree they resolve into differs.
    "ori-claude": (
        ("CLAUDE.md", "CLAUDE.md"),
        (".claude", ""),
        ("agents", "agents"),
        ("skills", "skills"),
    ),
    # Also the real `claude` binary underneath (harness.claude_openrouter.
    # adapter.ClaudeOpenRouterAdapter routes it at OpenRouter directly, no
    # `ori` involved) -- same four names again.
    "claude-openrouter": (
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
    # "created" | "refreshed" | "removed_legacy" | "skipped_conflict" |
    # "skipped_missing_target"
    status: str
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

    if harness == "codex":
        legacy = target / ".codex"
        # Phase 5 real validation invalidated the earlier discovery-link
        # design. Remove only the exact relative link Cosmo used to create;
        # any other symlink, real file, or directory is repository-owned.
        if legacy.is_symlink() and os.readlink(legacy) == ".agent/codex":
            legacy.unlink()
            results.append(
                SymlinkResult(
                    link_name=".codex",
                    link_path=legacy,
                    points_to="",
                    status="removed_legacy",
                    detail="removed obsolete Cosmo symlink",
                )
            )

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

        # Validate a nested parent before even inspecting the child path. If
        # `.agents` is itself a symlink, child operations could otherwise
        # escape the repository and modify the symlink target.
        parent = link_path.parent
        if parent != target and (parent.is_symlink() or (parent.exists() and not parent.is_dir())):
            results.append(
                SymlinkResult(
                    link_name=link_name,
                    link_path=link_path,
                    points_to="",
                    status="skipped_conflict",
                    detail=f"{parent} is not a real directory -- not modified",
                )
            )
            continue

        relative = os.path.relpath(real_target, start=link_path.parent)
        status = "created"
        if link_path.is_symlink():
            # A symlink is not automatically ours. Refresh only the exact link
            # Cosmo would have created; replacing an unrelated link could
            # redirect or destroy a repository's existing agent setup.
            if os.readlink(link_path) != relative:
                results.append(
                    SymlinkResult(
                        link_name=link_name,
                        link_path=link_path,
                        points_to="",
                        status="skipped_conflict",
                        detail=f"{link_path} is a non-Cosmo symlink -- not overwritten",
                    )
                )
                continue
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

        # Codex discovers project skills below `.agents/skills`. The parent is
        # intentionally a real directory: repositories may place other agent
        # metadata beside Cosmo's link without either side owning `.agents`.
        if parent != target:
            parent.mkdir(parents=True, exist_ok=True)
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
