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

import contextlib
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


def find_last_commit_touching(worktree_path: Path, relative_path: str) -> str | None:
    """The SHA of the most recent commit on `worktree_path`'s current
    branch that touched `relative_path`, or `None` if it was never
    committed. Used by `cli.main.queue_retry` to find the commit PROPOSING
    left behind (`openspec/changes/<spec_id>/tasks.md`) -- a structural git
    fact, not a commit-message string, so this stays consistent with spec
    4's "prose parsing is prohibited as a signal" even though it's a CLI
    convenience rather than a classification decision."""
    result = _run_git(worktree_path, "log", "--format=%H", "-1", "--", relative_path)
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def reset_worktree_to_commit(
    worktree_path: Path, commit: str, *, docker_bin: str = "docker"
) -> None:
    """Hard-resets `worktree_path` to `commit`, then discards every
    untracked file/directory -- used by a genuine retry that wants to
    discard a failed `IMPLEMENTING` attempt (committed or not, tracked or
    not -- e.g. a scaffolded `frontend/` never `git add`ed) while keeping
    an already-valid `PROPOSING` commit intact, rather than removing the
    whole worktree and starting over from `base_branch`.

    `git clean -fdx` alone can leave root-owned entries behind -- the same
    root-owned-by-a-gate-container problem `remove_worktree` already has a
    fallback for (confirmed live: a real blocked task's `node_modules_old`
    survived `git clean -fdx` intact, root ownership unchanged). A dry-run
    clean immediately after the real one lists exactly what didn't actually
    go; each such entry gets the same throwaway-root-container removal
    `_force_remove_root_owned` already does for whole-worktree teardown.
    Without this, a retried attempt inherits the exact cruft the *previous*
    attempt couldn't clean up either, permission-denied in a loop."""
    _run_git(worktree_path, "reset", "--hard", commit)
    _run_git(worktree_path, "clean", "-fdx")
    for leftover in _remaining_clean_targets(worktree_path):
        _force_remove_root_owned(leftover, docker_bin=docker_bin)


def _remaining_clean_targets(worktree_path: Path) -> list[Path]:
    """What a dry-run `git clean -fdxn` still lists right after the real
    `git clean -fdx` ran -- i.e., entries the real clean could not actually
    remove."""
    result = _run_git(worktree_path, "clean", "-fdxn")
    if result.returncode != 0:
        return []
    prefix = "Would remove "
    return [
        worktree_path / line.removeprefix(prefix).rstrip("/")
        for line in result.stdout.splitlines()
        if line.startswith(prefix)
    ]


_CLEANUP_IMAGE = "alpine:3.21"


def remove_worktree(
    *, repo_path: Path, worktree_path: Path, branch: str | None = None, docker_bin: str = "docker"
) -> None:
    """`git worktree remove --force`, then delete the task branch (spec 3.2:
    `DONE` removes the worktree and deletes the branch; pass `branch=None` to
    skip the delete, e.g. when the branch name is unknown at sweep time).

    Falls back to `worktree prune` plus a manual `shutil.rmtree` when git no
    longer recognizes the directory (e.g. it was already partially removed
    by hand) -- a half-corrupted worktree must not be able to jam teardown.

    If the directory is *still* there after both of those (confirmed by
    hand, twice -- Phase 6 and Phase 7's own state-doc sections: a gate
    container writes build output, e.g. Maven's `backend/target/`, as root
    inside the container, which an unprivileged `shutil.rmtree` can never
    unlink), falls back once more to the same throwaway root container
    used by hand both times: bind-mount the parent directory and `rm -rf`
    the one entry, as root, inside a disposable Alpine container. Best-
    effort like the `shutil.rmtree` fallback above it -- a leftover
    directory is a disk-space problem to flag, never a reason to fail
    task teardown outright.
    """
    result = _run_git(repo_path, "worktree", "remove", "--force", str(worktree_path))
    if result.returncode != 0:
        _run_git(repo_path, "worktree", "prune")
        if worktree_path.exists():
            shutil.rmtree(worktree_path, ignore_errors=True)
    if worktree_path.exists():
        _force_remove_root_owned(worktree_path, docker_bin=docker_bin)
        _run_git(repo_path, "worktree", "prune")
    if branch is not None:
        _run_git(repo_path, "branch", "-D", branch)


def _force_remove_root_owned(path: Path, *, docker_bin: str) -> None:
    # No `docker` on PATH, or it hung -- best-effort, same posture as the
    # `shutil.rmtree(ignore_errors=True)` fallback above.
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        subprocess.run(
            [
                docker_bin,
                "run",
                "--rm",
                "-v",
                f"{path.parent}:/cosmo-cleanup",
                _CLEANUP_IMAGE,
                "rm",
                "-rf",
                f"/cosmo-cleanup/{path.name}",
            ],
            capture_output=True,
            timeout=60.0,
            check=False,
        )


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
    (cleanly, by crash, or by pause) -- the only distinction that matters is
    per-task:

    - A `BLOCKED` task's worktree is retained for inspection (spec 3.2/3.4).
    - A `QUEUED` task that still carries a `worktree_path` is *also*
      retained: found by hand -- a run guard (wall clock or quota) can send
      a task back to `QUEUED` mid-run without touching `worktree_path` at
      all (`run.loop._requeue`), and if the process that paused later gets
      killed and a fresh `cosmo run` picks the task back up, this sweep used
      to delete that worktree before `run.loop._run_one_task` ever got a
      chance to reuse it -- destroying a fully-proposed (or partially
      implemented) attempt for a task that was never actually blocked, only
      interrupted. This is safe now specifically because `cli.main.queue_
      retry` is the *only* place that clears `worktree_path` (and does so
      by physically removing the worktree itself, synchronously, before
      writing that) -- a `QUEUED` task's `worktree_path` being set is
      therefore unambiguous evidence of "safe to resume," never "abandoned
      by a human who asked to start over."
    - Everything else -- a `DONE` task whose teardown didn't finish, or a
      worktree left mid-task by a crash (a task stuck in `PROPOSING`/
      `IMPLEMENTING`/etc., not `QUEUED` -- spec 3.2: "no mid-state
      resumption") -- is pruned.

    `run_state` is deliberately not consulted: nothing writes to it yet
    (Phase 8), and at startup every one of its rows would read non-running
    anyway.
    """
    removed: list[Path] = []
    retained: list[Path] = []
    if not work_dir.is_dir():
        return SweepOutcome(removed=removed, retained=retained)

    retained_paths = {
        Path(t.worktree_path).resolve()
        for t in list_tasks(db_path)
        if t.worktree_path and t.status in ("blocked", "queued")
    }
    branches = _worktree_branches(repo_path)

    for run_dir in sorted(p for p in work_dir.iterdir() if p.is_dir()):
        for task_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
            resolved = task_dir.resolve()
            if resolved in retained_paths:
                retained.append(task_dir)
                continue
            remove_worktree(
                repo_path=repo_path, worktree_path=task_dir, branch=branches.get(resolved)
            )
            removed.append(task_dir)
        if run_dir.is_dir() and not any(run_dir.iterdir()):
            run_dir.rmdir()
    return SweepOutcome(removed=removed, retained=retained)
