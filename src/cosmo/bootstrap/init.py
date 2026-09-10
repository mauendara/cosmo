"""`cosmo init` orchestration (spec 10.4 steps 1-7)."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cosmo.bootstrap.assets import SyncResult, sync_harness_assets
from cosmo.bootstrap.docs import DocsCopyResult, copy_project_docs
from cosmo.bootstrap.git_branch import (
    branch_exists,
    checkout_branch,
    create_and_checkout_branch,
    create_branch_from,
    current_branch,
    init_repo,
    is_git_repo,
    stash_all,
    working_tree_is_clean,
)
from cosmo.bootstrap.openspec import OpenSpecResult, ensure_openspec_initialized
from cosmo.bootstrap.symlinks import SymlinkResult, create_root_symlinks
from cosmo.events import EventEmitter
from cosmo.store import StoreWriter
from cosmo.store.reader import find_project_by_path

_STASH_MESSAGE = "cosmo: pre-cosmo-branch stash"


class GitBranchOutcome(enum.Enum):
    """What `run_init`'s git-init/base-branch step actually did -- `cli.main.
    init` reports this back to the human rather than staying silent about a
    step that used to be a hard refusal (`NotAGitRepoError`, now removed).

    The `COSMO_BRANCH_*`/`ALREADY_ON_COSMO_BRANCH` members are `cosmo_branch`
    mode's counterparts to the `direct`-mode members above them; `SKIPPED_
    DIRTY` is unreachable in `cosmo_branch` mode by construction, since
    `stash_all` always resolves a dirty tree before the fork (see
    `InitResult.stashed` for whether it actually did anything)."""

    REPO_INITIALIZED_AND_BRANCH_CREATED = "repo_initialized_and_branch_created"
    BRANCH_CREATED = "branch_created"
    ALREADY_ON_BASE_BRANCH = "already_on_base_branch"
    SKIPPED_DIRTY = "skipped_dirty"
    COSMO_BRANCH_REPO_INITIALIZED_AND_CREATED = "cosmo_branch_repo_initialized_and_created"
    COSMO_BRANCH_CREATED = "cosmo_branch_created"
    ALREADY_ON_COSMO_BRANCH = "already_on_cosmo_branch"


@dataclass(frozen=True, slots=True)
class InitResult:
    target: Path
    harness: str
    project_template: str
    git_branch: GitBranchOutcome
    stashed: bool
    real_base_branch: str
    effective_base_branch: str
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
    base_branch_mode: Literal["direct", "cosmo_branch"] = "direct",
    cosmo_branch_name: str | None = None,
) -> InitResult:
    resolved = target.resolve()

    # Step 1. Uniform regardless of whether the repo already existed: a
    # freshly `git init`-ed repo has zero refs and a clean tree, so it falls
    # through the exact same "doesn't have base_branch yet, clean, create
    # it" path an existing-but-mismatched repo does -- no special-casing.
    repo_freshly_initialized = not is_git_repo(resolved)
    if repo_freshly_initialized:
        init_repo(resolved)

    stashed = False
    if base_branch_mode == "cosmo_branch":
        assert cosmo_branch_name is not None
        # Dirtiness is always resolved via stash first in this mode, so
        # `SKIPPED_DIRTY` never fires here -- unlike `direct` mode, which
        # gives up on a dirty tree instead.
        stashed = stash_all(resolved, _STASH_MESSAGE)

        if not (branch_exists(resolved, base_branch) or current_branch(resolved) == base_branch):
            create_and_checkout_branch(resolved, base_branch)

        if branch_exists(resolved, cosmo_branch_name):
            checkout_branch(resolved, cosmo_branch_name)
            git_branch_outcome = GitBranchOutcome.ALREADY_ON_COSMO_BRANCH
        else:
            if branch_exists(resolved, base_branch):
                create_branch_from(resolved, cosmo_branch_name, base_branch)
            else:
                # `base_branch` itself has zero commits yet (e.g. right after
                # the `create_and_checkout_branch` call above, or a repo that
                # was already on an unborn `base_branch`) -- `git checkout -b
                # <cosmo> <base_branch>` would fail outright, since an unborn
                # branch has no ref to name yet (see `branch_exists`'s own
                # docstring). Forking from current HEAD instead is equivalent:
                # HEAD *is* the unborn `base_branch` at this point.
                create_and_checkout_branch(resolved, cosmo_branch_name)
            git_branch_outcome = (
                GitBranchOutcome.COSMO_BRANCH_REPO_INITIALIZED_AND_CREATED
                if repo_freshly_initialized
                else GitBranchOutcome.COSMO_BRANCH_CREATED
            )
        effective_base_branch = cosmo_branch_name
    elif branch_exists(resolved, base_branch) or current_branch(resolved) == base_branch:
        git_branch_outcome = GitBranchOutcome.ALREADY_ON_BASE_BRANCH
        effective_base_branch = base_branch
    elif working_tree_is_clean(resolved):
        create_and_checkout_branch(resolved, base_branch)
        git_branch_outcome = (
            GitBranchOutcome.REPO_INITIALIZED_AND_BRANCH_CREATED
            if repo_freshly_initialized
            else GitBranchOutcome.BRANCH_CREATED
        )
        effective_base_branch = base_branch
    else:
        git_branch_outcome = GitBranchOutcome.SKIPPED_DIRTY
        effective_base_branch = base_branch

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
            target_path=str(resolved),
            harness=harness,
            project_template=project_template,
            base_branch_mode=base_branch_mode,
            real_base_branch=base_branch,
            cosmo_branch_name=cosmo_branch_name,
        )
        already_registered = False

    return InitResult(
        target=resolved,
        harness=harness,
        project_template=project_template,
        git_branch=git_branch_outcome,
        stashed=stashed,
        real_base_branch=base_branch,
        effective_base_branch=effective_base_branch,
        openspec=openspec_result,
        docs=docs_result,
        assets=assets_result,
        symlinks=symlink_results,
        project_id=project_id,
        already_registered=already_registered,
    )
