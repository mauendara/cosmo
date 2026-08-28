"""Seeding a target repo's `docs/` from a project template (spec 10.4 step 3).

Never-overwrites by default -- `docs/` belongs to the target repo once
seeded (spec 10.1: "edited directly in the target repo once seeded"), so a
re-run of `cosmo init` must not clobber edits already made there. `--force`
is a caller decision (with its own confirmation prompt at the CLI layer);
this function only does what it's told.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from cosmo.bootstrap.discover import project_template_dir


@dataclass(frozen=True, slots=True)
class DocsCopyResult:
    created: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)


def copy_project_docs(
    project_template: str,
    target: Path,
    *,
    force: bool = False,
    templates_root: Path | None = None,
) -> DocsCopyResult:
    source = project_template_dir(project_template, root=templates_root) / "docs"
    dest_root = target / "docs"

    created: list[Path] = []
    skipped: list[Path] = []
    for src_file in sorted(p for p in source.rglob("*") if p.is_file()):
        rel = src_file.relative_to(source)
        dest_file = dest_root / rel
        if dest_file.exists() and not force:
            skipped.append(rel)
            continue
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dest_file)
        created.append(rel)

    # `docs/specs/` is deliberately not part of any project template's own
    # `docs/` (spec-batch content, not stack boilerplate) -- but it should
    # still exist right after `cosmo init` so it's discoverable, rather than
    # only appearing the first time `cosmo spec add` is run. Not counted in
    # `created`/`skipped`, which track template files only.
    (dest_root / "specs").mkdir(parents=True, exist_ok=True)

    return DocsCopyResult(created=created, skipped=skipped)
