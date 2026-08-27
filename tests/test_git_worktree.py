"""Worktree lifecycle against real `git` (plan Phase 5) -- no fake, the same
posture Phase 4 took toward `openspec init` (probed by hand, found fast and
offline, used for real).

Every commit-creating git invocation passes `-c user.name=.../-c
user.email=...` explicitly rather than relying on a global identity: this
sandbox has none configured (found during Phase 5), and CI/a fresh dev box
may not either.
"""

from __future__ import annotations

import os
import shutil
import stat
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
from cosmo.store.enums import BlockedReason, TaskStatus
from cosmo.store.reader import get_task

AUTHOR = ("Test", "test@example.com")
FAKE_DOCKER = Path(__file__).resolve().parent / "fixtures" / "fake_docker.sh"


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


def test_sweep_retains_blocked_and_queued_worktrees_and_prunes_everything_else(
    tmp_path: Path,
) -> None:
    """A `QUEUED` task with `worktree_path` still set is retained alongside
    a `BLOCKED` one -- found by hand: a run guard (wall clock or quota) can
    send a task back to `QUEUED` mid-run without touching `worktree_path`
    (`run.loop._requeue`), and a later `cosmo run` picking it back up under
    a *different* run_id needs that worktree intact, not pruned out from
    under it before `_run_one_task` ever gets a chance to reuse it. A task
    genuinely interrupted mid-state (stuck in `IMPLEMENTING`, not `QUEUED`)
    is still pruned -- that's a real crash artifact, not a graceful pause."""
    repo = _repo_on_develop(tmp_path)
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    writer.queue_add(task_id="blocked-task", spec_path="p1", max_attempts=2)
    writer.queue_add(task_id="queued-task", spec_path="p2", max_attempts=2)
    writer.queue_add(task_id="crashed-task", spec_path="p3", max_attempts=2)
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
    queued_info = create_worktree(
        repo_path=repo,
        work_dir=work_dir,
        run_id="run-1",
        task_id="queued-task",
        spec_id="queued-task",
        base_branch="develop",
        harness="claude",
        writer=writer,
        emitter=emitter,
    )
    crashed_info = create_worktree(
        repo_path=repo,
        work_dir=work_dir,
        run_id="run-1",
        task_id="crashed-task",
        spec_id="crashed-task",
        base_branch="develop",
        harness="claude",
        writer=writer,
        emitter=emitter,
    )

    writer.queue_block("blocked-task", BlockedReason.MERGE_CONFLICT)
    # queued-task: left exactly as `create_worktree` set it -- still
    # `queued`, `worktree_path` set, simulating a graceful mid-run requeue.
    writer.queue_transition("crashed-task", TaskStatus.IMPLEMENTING)
    writer.close()

    outcome = sweep_stale_worktrees(repo_path=repo, work_dir=work_dir, db_path=db_path)

    assert set(outcome.retained) == {blocked_info.path, queued_info.path}
    assert outcome.removed == [crashed_info.path]
    assert blocked_info.path.is_dir()
    assert queued_info.path.is_dir()
    assert not crashed_info.path.exists()


def test_sweep_on_an_empty_or_missing_work_dir_is_a_noop(tmp_path: Path) -> None:
    repo = _repo_on_develop(tmp_path)
    db_path = tmp_path / "cosmo.db"
    StoreWriter(db_path).close()

    outcome = sweep_stale_worktrees(
        repo_path=repo, work_dir=tmp_path / "does-not-exist", db_path=db_path
    )
    assert outcome.removed == []
    assert outcome.retained == []


def _worktree_with_an_unremovable_subdirectory(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A `0o000` subdirectory blocks `shutil.rmtree`'s own directory walk
    (`PermissionError` on `listdir`, swallowed by `ignore_errors=True`) the
    same way a Docker-gate-container-written root-owned `backend/target/`
    does to an unprivileged host user -- a real, reproducible stand-in for
    that failure mode that needs neither root nor a real gate run to set
    up. Returns (repo, worktree_path, the locked subdirectory) so a test
    can restore its permissions for cleanup even if the fallback under
    test doesn't actually remove it (the fake-docker case)."""
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

    locked = info.path / "backend" / "target"
    locked.mkdir(parents=True)
    (locked / "leftover.class").write_bytes(b"\x00")
    os.chmod(locked, 0o000)
    return repo, info.path, locked


def test_remove_worktree_invokes_docker_with_the_parent_mount_and_entry_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, worktree_path, locked = _worktree_with_an_unremovable_subdirectory(tmp_path)
    log = tmp_path / "docker.log"
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    try:
        remove_worktree(
            repo_path=repo,
            worktree_path=worktree_path,
            branch="task/add-foo",
            docker_bin=str(FAKE_DOCKER),
        )
        assert worktree_path.exists()  # fake docker never really deletes anything
        invocation = log.read_text()
        assert "run" in invocation and "--rm" in invocation
        assert f"{worktree_path.parent}:/cosmo-cleanup" in invocation
        assert f"/cosmo-cleanup/{worktree_path.name}" in invocation
    finally:
        os.chmod(locked, stat.S_IRWXU)


@pytest.mark.skipif(
    shutil.which("docker") is None or os.environ.get("COSMO_GATE_DOCKER_E2E") != "1",
    reason="real root-container cleanup against real docker -- opt in with COSMO_GATE_DOCKER_E2E=1",
)
def test_remove_worktree_really_deletes_an_unremovable_directory_via_real_docker(
    tmp_path: Path,
) -> None:
    """The real half of the fake-docker test above: an actual disposable
    container, run as root, really does remove what the unprivileged host
    user cannot -- the same mechanism (not just the same argv) as the real
    Phase 6/7 finding this fixes (docs/v3-implementation-state.md)."""
    repo, worktree_path, _locked = _worktree_with_an_unremovable_subdirectory(tmp_path)

    remove_worktree(repo_path=repo, worktree_path=worktree_path, branch="task/add-foo")

    assert not worktree_path.exists()
