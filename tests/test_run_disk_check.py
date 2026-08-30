"""Spec 9.5's pre-run disk check, wired into `run.loop.run_queue` itself
(Phase 9) -- not just a `cosmo doctor` advisory. A run whose target data
path has less free space than `disk.min_free_gb` aborts with
`stop_reason=disk_low` before any task starts, no worktree ever created."""

from __future__ import annotations

import subprocess
from pathlib import Path

from cosmo.config import CosmoConfig, load_config
from cosmo.events import EventEmitter
from cosmo.harness.fake import FakeHarnessAdapter, FakeOutcome, ScriptedCall
from cosmo.run.loop import run_queue
from cosmo.store import StoreWriter
from cosmo.store.enums import RunStatus, StopReason
from cosmo.store.reader import get_task, list_events

NO_USER_CONFIG = Path("/nonexistent/config.toml")


def _config(tmp_path: Path, **overrides: object) -> CosmoConfig:
    cfg = load_config(config_path=NO_USER_CONFIG)
    paths = cfg.paths.model_copy(
        update={
            "data_dir": tmp_path / "data",
            "work_dir": tmp_path / "work",
            "log_dir": tmp_path / "logs",
        }
    )
    updates: dict[str, object] = {"paths": paths}
    updates.update(overrides)
    return cfg.model_copy(update=updates)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


def _repo_on_develop(tmp_path: Path) -> Path:
    repo = tmp_path / "target-repo"
    repo.mkdir()
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "init", "-q")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "README.md")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "base")
    _git(repo, "branch", "-M", "develop")
    return repo


def test_a_run_aborts_before_any_task_when_free_space_is_below_the_floor(
    tmp_path: Path,
) -> None:
    # No real disk has a petabyte free -- deterministic regardless of host state.
    disk = load_config(config_path=NO_USER_CONFIG).disk.model_copy(
        update={"min_free_gb": 1_000_000_000.0}
    )
    cfg = _config(tmp_path, disk=disk)
    repo = _repo_on_develop(tmp_path)
    writer = StoreWriter(cfg.paths.db_path)
    writer.queue_add(task_id="a", spec_path="openspec/changes/a", max_attempts=2)
    emitter = EventEmitter(writer)
    adapter = FakeHarnessAdapter(cfg, script=ScriptedCall(outcome=FakeOutcome.SUCCESS))

    def _gate_runner(**_kwargs: object) -> None:
        raise AssertionError("gate must never run -- the disk check should abort first")

    try:
        outcome = run_queue(
            config=cfg,
            writer=writer,
            emitter=emitter,
            adapter=adapter,
            repo_path=repo,
            base_branch="develop",
            harness_name="claude",
            gate_runner=_gate_runner,  # type: ignore[arg-type]
        )
    finally:
        writer.close()

    assert outcome.status is RunStatus.STOPPED
    assert outcome.stop_reason is StopReason.DISK_LOW
    assert outcome.summary.completed == 0
    assert adapter.calls == []  # propose()/implement() never invoked

    task = get_task(cfg.paths.db_path, "a")
    assert task is not None
    assert task.status == "queued"  # never touched
    assert task.worktree_path is None

    events = list_events(cfg.paths.db_path, run_id=outcome.run_id, event_type="run.stopped")
    # Exactly one row, not two: an earlier version emitted its own detailed
    # `run.stopped` inline at the abort site *and* fell through to the
    # generic post-loop emission every non-PAUSED stop gets, double-counting
    # every disk_low/DAG-cycle abort in `cosmo events tail`.
    assert len(events) == 1
    assert events[0].severity == "critical"
    assert events[0].payload.get("reason") == "disk_low"
    assert "detail" in events[0].payload
