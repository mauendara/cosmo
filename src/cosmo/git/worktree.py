"""Worktree lifecycle: create, teardown, startup sweep (spec 3.2, plan Phase 5).

Every task gets its own `git worktree` -- a dedicated working directory
sharing one `.git` object store -- rather than a branch checkout in a shared
directory. This isolates *code*, not runtime: ports, the database, and
`/dev/shm` remain shared (spec 3.2's own caveat, unaffected for the serial v1
loop).

`sync_harness_assets` (Phase 4) is called immediately after creation, before
`PROPOSING` starts (spec 10.5) -- this is its second real call site, the one
Phase 4's own handoff flagged as a seam.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from cosmo.bootstrap.assets import sync_harness_assets
from cosmo.events import EventEmitter
from cosmo.git.secrets import install_gitleaks_pre_commit_hook
from cosmo.store.reader import list_tasks
from cosmo.store.writer import StoreWriter


class WorktreeError(RuntimeError):
    """Raised when a `git worktree`/`git branch` invocation exits non-zero."""


@dataclass(frozen=True, slots=True)
class WorktreeInfo:
    task_id: str
    branch: str
    path: Path


def _run_git(
    repo_path: Path, *args: str, timeout: float = 60.0
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(repo_path), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorktreeError(f"could not run git {' '.join(args)}: {exc}") from exc


def create_worktree(
    *,
    repo_path: Path,
    work_dir: Path,
    run_id: str,
    task_id: str,
    spec_id: str,
    base_branch: str,
    harness: str,
    writer: StoreWriter,
    emitter: EventEmitter,
    templates_root: Path | None = None,
) -> WorktreeInfo:
    """`git worktree add <work_dir>/<run_id>/<task_id> -b task/<spec_id>
    <base_branch>`, then sync harness assets and install the gitleaks hook --
    both before `PROPOSING` starts (spec 10.5). `task_id` must already be a
    `task_queue` row (`writer.queue_add`); `worktree_path` is written here,
    for real, the moment `git worktree add` succeeds.
    """
    worktree_path = work_dir / run_id / task_id
    branch = f"task/{spec_id}"
    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    result = _run_git(repo_path, "worktree", "add", str(worktree_path), "-b", branch, base_branch)
    if result.returncode != 0:
        raise WorktreeError(
            f"git worktree add {worktree_path} failed: {(result.stderr or result.stdout).strip()}"
        )

    writer.queue_set_worktree_path(task_id, worktree_path)
    sync_harness_assets(
        worktree_path, harness, emitter=emitter, run_id=run_id, templates_root=templates_root
    )
    install_gitleaks_pre_commit_hook(repo_path)

    return WorktreeInfo(task_id=task_id, branch=branch, path=worktree_path)


def remove_worktree(*, repo_path: Path, worktree_path: Path, branch: str | None = None) -> None:
    """`git worktree remove --force`, then delete the task branch (spec 3.2:
    `DONE` removes the worktree and deletes the branch; pass `branch=None` to
    skip the delete, e.g. when the branch name is unknown at sweep time).

    Falls back to `worktree prune` plus a manual `shutil.rmtree` when git no
    longer recognizes the directory (e.g. it was already partially removed
    by hand) -- a half-corrupted worktree must not be able to jam teardown.
    """
    result = _run_git(repo_path, "worktree", "remove", "--force", str(worktree_path))
    if result.returncode != 0:
        _run_git(repo_path, "worktree", "prune")
        if worktree_path.exists():
            shutil.rmtree(worktree_path, ignore_errors=True)
    if branch is not None:
        _run_git(repo_path, "branch", "-D", branch)


def _worktree_branches(repo_path: Path) -> dict[Path, str]:
    """Parses `git worktree list --porcelain` into `{path: branch_name}`
    (branch omitted for a detached worktree). Used by the sweep so a stale
    directory's branch can be deleted too, not just the directory."""
    result = _run_git(repo_path, "worktree", "list", "--porcelain")
    if result.returncode != 0:
        return {}
    branches: dict[Path, str] = {}
    current_path: Path | None = None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line.removeprefix("worktree ")).resolve()
        elif line.startswith("branch refs/heads/") and current_path is not None:
            branches[current_path] = line.removeprefix("branch refs/heads/")
    return branches


@dataclass(frozen=True, slots=True)
class SweepOutcome:
    removed: list[Path]
    retained: list[Path]


def sweep_stale_worktrees(*, repo_path: Path, work_dir: Path, db_path: Path) -> SweepOutcome:
    """Startup sweep (spec 3.2), run before any run starts.

    Nothing is "running" at startup by definition, so every worktree
    currently on disk under `work_dir` belongs to a run that already ended
    (cleanly or by crash) -- the only distinction that matters is per-task:
    a `BLOCKED` task's worktree is retained for inspection (spec 3.2/3.4);
    everything else -- a `DONE` task whose teardown didn't finish, or a
    worktree left mid-task by a crash (spec 3.2: "no mid-state resumption",
    so that task restarts from a fresh worktree next time it runs) -- is
    pruned. `run_state` is deliberately not consulted: nothing writes to it
    yet (Phase 8), and at startup every one of its rows would read
    non-running anyway.
    """
    removed: list[Path] = []
    retained: list[Path] = []
    if not work_dir.is_dir():
        return SweepOutcome(removed=removed, retained=retained)

    blocked_paths = {
        Path(t.worktree_path).resolve()
        for t in list_tasks(db_path, status="blocked")
        if t.worktree_path
    }
    branches = _worktree_branches(repo_path)

    for run_dir in sorted(p for p in work_dir.iterdir() if p.is_dir()):
        for task_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
            resolved = task_dir.resolve()
            if resolved in blocked_paths:
                retained.append(task_dir)
                continue
            remove_worktree(
                repo_path=repo_path, worktree_path=task_dir, branch=branches.get(resolved)
            )
            removed.append(task_dir)
        if run_dir.is_dir() and not any(run_dir.iterdir()):
            run_dir.rmdir()
    return SweepOutcome(removed=removed, retained=retained)
