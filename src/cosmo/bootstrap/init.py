"""`cosmo init` orchestration (spec 10.4 steps 1-7)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cosmo.bootstrap.assets import SyncResult, sync_harness_assets
from cosmo.bootstrap.docs import DocsCopyResult, copy_project_docs
from cosmo.bootstrap.openspec import OpenSpecResult, ensure_openspec_initialized
from cosmo.bootstrap.symlinks import SymlinkResult, create_root_symlinks
from cosmo.events import EventEmitter
from cosmo.store import StoreWriter
from cosmo.store.reader import find_project_by_path


class NotAGitRepoError(ValueError):
    """Spec 10.4 step 1: `cosmo init` verifies but never creates a git repo --
    that decision belongs to the developer."""


@dataclass(frozen=True, slots=True)
class InitResult:
    target: Path
    harness: str
    project_template: str
    openspec: OpenSpecResult
    docs: DocsCopyResult
    assets: SyncResult
    symlinks: list[SymlinkResult]
    project_id: str
    already_registered: bool


def run_init(
    target: Path,
    *,
    harness: str,
    project_template: str,
    force_docs: bool,
    writer: StoreWriter,
    db_path: Path,
    templates_root: Path | None = None,
) -> InitResult:
    resolved = target.resolve()
    if not (resolved / ".git").exists():
        raise NotAGitRepoError(
            f"{resolved} is not a git repository (no .git) -- "
            f"run `git init` yourself first; cosmo init never does this for you"
        )

    # Step 2.
    openspec_result = ensure_openspec_initialized(resolved)

    # Step 3.
    docs_result = copy_project_docs(
        project_template, resolved, force=force_docs, templates_root=templates_root
    )

    # Steps 4 and 7 (sync_harness_assets emits agent_assets.synced itself).
    emitter = EventEmitter(writer)
    assets_result = sync_harness_assets(
        resolved, harness, emitter=emitter, templates_root=templates_root
    )

    # Step 5.
    symlink_results = create_root_symlinks(resolved, harness)

    # Step 6 -- idempotent: re-running init against an already-registered
    # path must not fail the whole run (plan Phase 4 exit criterion: init is
    # safe to re-run).
    existing = find_project_by_path(db_path, str(resolved))
    if existing is not None:
        project_id = existing.project_id
        already_registered = True
    else:
        project_id = writer.register_project(
            target_path=str(resolved), harness=harness, project_template=project_template
        )
        already_registered = False

    return InitResult(
        target=resolved,
        harness=harness,
        project_template=project_template,
        openspec=openspec_result,
        docs=docs_result,
        assets=assets_result,
        symlinks=symlink_results,
        project_id=project_id,
        already_registered=already_registered,
    )
