"""`cosmo init` orchestration (spec 10.4 steps 1-7)."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path

from cosmo.bootstrap.assets import SyncResult, sync_harness_assets
from cosmo.bootstrap.docs import DocsCopyResult, copy_project_docs
from cosmo.bootstrap.git_branch import (
    branch_exists,
    create_and_checkout_branch,
    current_branch,
    init_repo,
    is_git_repo,
    working_tree_is_clean,
)
from cosmo.bootstrap.openspec import OpenSpecResult, ensure_openspec_initialized
from cosmo.bootstrap.symlinks import SymlinkResult, create_root_symlinks
from cosmo.events import EventEmitter
from cosmo.store import StoreWriter
from cosmo.store.reader import find_project_by_path


class GitBranchOutcome(enum.Enum):
    """What `run_init`'s git-init/base-branch step actually did -- `cli.main.
    init` reports this back to the human rather than staying silent about a
    step that used to be a hard refusal (`NotAGitRepoError`, now removed)."""

    REPO_INITIALIZED_AND_BRANCH_CREATED = "repo_initialized_and_branch_created"
    BRANCH_CREATED = "branch_created"
    ALREADY_ON_BASE_BRANCH = "already_on_base_branch"
    SKIPPED_DIRTY = "skipped_dirty"


@dataclass(frozen=True, slots=True)
class InitResult:
    target: Path
    harness: str
    project_template: str
    git_branch: GitBranchOutcome
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
    base_branch: str,
    force_docs: bool,
    writer: StoreWriter,
    db_path: Path,
    templates_root: Path | None = None,
) -> InitResult:
    resolved = target.resolve()

    # Step 1. Uniform regardless of whether the repo already existed: a
    # freshly `git init`-ed repo has zero refs and a clean tree, so it falls
    # through the exact same "doesn't have base_branch yet, clean, create
    # it" path an existing-but-mismatched repo does -- no special-casing.
    repo_freshly_initialized = not is_git_repo(resolved)
    if repo_freshly_initialized:
        init_repo(resolved)

    if branch_exists(resolved, base_branch) or current_branch(resolved) == base_branch:
        git_branch_outcome = GitBranchOutcome.ALREADY_ON_BASE_BRANCH
    elif working_tree_is_clean(resolved):
        create_and_checkout_branch(resolved, base_branch)
        git_branch_outcome = (
            GitBranchOutcome.REPO_INITIALIZED_AND_BRANCH_CREATED
            if repo_freshly_initialized
            else GitBranchOutcome.BRANCH_CREATED
        )
    else:
        git_branch_outcome = GitBranchOutcome.SKIPPED_DIRTY

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
        git_branch=git_branch_outcome,
        openspec=openspec_result,
        docs=docs_result,
        assets=assets_result,
        symlinks=symlink_results,
        project_id=project_id,
        already_registered=already_registered,
    )
