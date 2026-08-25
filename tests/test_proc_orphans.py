"""Orphan sweep: labeled containers and worktree-holding processes
(spec 2.4 steps 4-5).

Docker is faked via a recording script rather than a live daemon -- this
sandbox has no working `docker` (Desktop's WSL2 integration isn't wired up
here), and the plan's own guidance for Phase 2 ("use a test fixture script...
rather than claude -p") applies equally to a Phase 2 test standing in for a
gate container.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from cosmo.proc.orphans import find_worktree_holders, sweep, sweep_containers

FAKE_DOCKER = Path(__file__).resolve().parent / "fixtures" / "fake_docker.sh"


def test_sweep_containers_filters_on_both_labels_and_removes_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "docker_calls.log"
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    monkeypatch.setenv("FAKE_DOCKER_CONTAINERS", "abc123 def456")

    removed = sweep_containers("run-1", "task-1", docker_bin=str(FAKE_DOCKER))

    assert removed == ["abc123", "def456"]
    calls = log.read_text().splitlines()
    assert any(
        "ps" in c
        and "label=orchestrator.run_id=run-1" in c
        and "label=orchestrator.task_id=task-1" in c
        for c in calls
    )
    assert any(c.startswith("rm -f abc123 def456") for c in calls)


def test_sweep_containers_removes_nothing_when_none_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "docker_calls.log"
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    monkeypatch.setenv("FAKE_DOCKER_CONTAINERS", "")

    removed = sweep_containers("run-1", "task-1", docker_bin=str(FAKE_DOCKER))

    assert removed == []
    assert not any(c.startswith("rm") for c in log.read_text().splitlines())


def test_sweep_containers_does_not_treat_a_failed_docker_invocation_as_container_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: the real WSL2 Docker Desktop shim exits 1 and prints its
    "could not be found" banner to *stdout*. Naively parsing stdout lines as
    container ids would try to `docker rm -f` that error message."""
    log = tmp_path / "docker_calls.log"
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    monkeypatch.setenv("FAKE_DOCKER_FAIL", "the command 'docker' could not be found")

    removed = sweep_containers("run-1", "task-1", docker_bin=str(FAKE_DOCKER))

    assert removed == []
    assert not any(c.startswith("rm") for c in log.read_text().splitlines())


def test_find_worktree_holders_detects_a_process_with_cwd_inside(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"], cwd=worktree)
    try:
        deadline = time.monotonic() + 2.0
        holders: list[int] = []
        while time.monotonic() < deadline:
            holders = find_worktree_holders(worktree)
            if holder.pid in holders:
                break
            time.sleep(0.02)
        assert holder.pid in holders
    finally:
        holder.kill()
        holder.wait(timeout=2.0)

    assert holder.pid not in find_worktree_holders(worktree)


def test_sweep_combines_container_removal_and_holder_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "docker_calls.log"
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    monkeypatch.setenv("FAKE_DOCKER_CONTAINERS", "leaked1")
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    result = sweep("run-1", "task-1", worktree, docker_bin=str(FAKE_DOCKER))

    assert result.removed_containers == ["leaked1"]
    assert result.worktree_holder_pids == []
    assert result.clean is True
