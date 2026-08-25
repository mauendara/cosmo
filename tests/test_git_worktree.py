"""Worktree lifecycle against real `git` (plan Phase 5) -- no fake, the same
posture Phase 4 took toward `openspec init` (probed by hand, found fast and
offline, used for real).

Every commit-creating git invocation passes `-c user.name=.../-c
user.email=...` explicitly rather than relying on a global identity: this
sandbox has none configured (found during Phase 5), and CI/a fresh dev box
may not either.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cosmo.events import EventEmitter
from cosmo.git.worktree import (
    WorktreeError,
    create_worktree,
    remove_worktree,
    sweep_stale_worktrees,
)
from cosmo.store import StoreWriter
from cosmo.store.enums import BlockedReason
from cosmo.store.reader import get_task

AUTHOR = ("Test", "test@example.com")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            f"user.name={AUTHOR[0]}",
            "-c",
            f"user.email={AUTHOR[1]}",
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _repo_on_develop(tmp_path: Path) -> Path:
    repo = tmp_path / "target-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    _git(repo, "branch", "-M", "develop")
    return repo


def test_create_worktree_adds_branch_and_syncs_harness_assets(tmp_path: Path) -> None:
    repo = _repo_on_develop(tmp_path)
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    writer.queue_add(task_id="add-foo", spec_path="openspec/changes/add-foo", max_attempts=2)
    emitter = EventEmitter(writer)

    info = create_worktree(
        repo_path=repo,
        work_dir=tmp_path / "work",
        run_id="run-1",
        task_id="add-foo",
        spec_id="add-foo",
        base_branch="develop",
        harness="claude",
        writer=writer,
        emitter=emitter,
    )

    assert info.path == tmp_path / "work" / "run-1" / "add-foo"
    assert info.path.is_dir()
    assert info.branch == "task/add-foo"
    assert (info.path / ".agent" / "claude" / "CLAUDE.md").is_file()
    assert (repo / ".git" / "hooks" / "pre-commit").is_file()

    task = get_task(db_path, "add-foo")
    assert task is not None
    assert task.worktree_path == str(info.path)
    writer.close()


def test_create_worktree_raises_on_a_duplicate_branch(tmp_path: Path) -> None:
    repo = _repo_on_develop(tmp_path)
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    writer.queue_add(task_id="add-foo", spec_path="p", max_attempts=2)
    writer.queue_add(task_id="add-foo-2", spec_path="p2", max_attempts=2)
    emitter = EventEmitter(writer)

    create_worktree(
        repo_path=repo,
        work_dir=tmp_path / "work",
        run_id="run-1",
        task_id="add-foo",
        spec_id="add-foo",
        base_branch="develop",
        harness="claude",
        writer=writer,
        emitter=emitter,
    )
    with pytest.raises(WorktreeError):
        create_worktree(
            repo_path=repo,
            work_dir=tmp_path / "work",
            run_id="run-1",
            task_id="add-foo-2",
            spec_id="add-foo",  # same spec_id -> same branch name
            base_branch="develop",
            harness="claude",
            writer=writer,
            emitter=emitter,
        )
    writer.close()


def test_remove_worktree_deletes_directory_and_branch(tmp_path: Path) -> None:
    repo = _repo_on_develop(tmp_path)
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    writer.queue_add(task_id="add-foo", spec_path="p", max_attempts=2)
    emitter = EventEmitter(writer)
    info = create_worktree(
        repo_path=repo,
        work_dir=tmp_path / "work",
        run_id="run-1",
        task_id="add-foo",
        spec_id="add-foo",
        base_branch="develop",
        harness="claude",
        writer=writer,
        emitter=emitter,
    )
    writer.close()

    remove_worktree(repo_path=repo, worktree_path=info.path, branch=info.branch)

    assert not info.path.exists()
    branches = subprocess.run(
        ["git", "-C", str(repo), "branch", "--list", info.branch],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert branches.strip() == ""


def test_sweep_retains_blocked_worktrees_and_prunes_everything_else(tmp_path: Path) -> None:
    repo = _repo_on_develop(tmp_path)
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    writer.queue_add(task_id="blocked-task", spec_path="p1", max_attempts=2)
    writer.queue_add(task_id="stale-task", spec_path="p2", max_attempts=2)
    emitter = EventEmitter(writer)
    work_dir = tmp_path / "work"

    blocked_info = create_worktree(
        repo_path=repo,
        work_dir=work_dir,
        run_id="run-1",
        task_id="blocked-task",
        spec_id="blocked-task",
        base_branch="develop",
        harness="claude",
        writer=writer,
        emitter=emitter,
    )
    stale_info = create_worktree(
        repo_path=repo,
        work_dir=work_dir,
        run_id="run-1",
        task_id="stale-task",
        spec_id="stale-task",
        base_branch="develop",
        harness="claude",
        writer=writer,
        emitter=emitter,
    )

    writer.queue_block("blocked-task", BlockedReason.MERGE_CONFLICT)
    writer.close()

    outcome = sweep_stale_worktrees(repo_path=repo, work_dir=work_dir, db_path=db_path)

    assert outcome.retained == [blocked_info.path]
    assert outcome.removed == [stale_info.path]
    assert blocked_info.path.is_dir()
    assert not stale_info.path.exists()


def test_sweep_on_an_empty_or_missing_work_dir_is_a_noop(tmp_path: Path) -> None:
    repo = _repo_on_develop(tmp_path)
    db_path = tmp_path / "cosmo.db"
    StoreWriter(db_path).close()

    outcome = sweep_stale_worktrees(
        repo_path=repo, work_dir=tmp_path / "does-not-exist", db_path=db_path
    )
    assert outcome.removed == []
    assert outcome.retained == []
