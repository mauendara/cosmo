"""`cancel_and_reap`: reap-failure event emission (spec 2.4 step 6).

Goes through the real `StoreWriter`/`EventEmitter` from Phase 1 -- the point
of this module is that it uses that machinery rather than a path of its own,
so the test proves the event actually lands in the store, not just that a
mock was called.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cosmo.config import CosmoConfig, load_config
from cosmo.events import EventEmitter
from cosmo.proc.orphans import SweepResult
from cosmo.proc.reap import cancel_and_reap
from cosmo.store import StoreWriter

NO_USER_CONFIG = Path("/nonexistent/config.toml")


def _config(tmp_path: Path) -> CosmoConfig:
    cfg = load_config(config_path=NO_USER_CONFIG)
    paths = cfg.paths.model_copy(
        update={"data_dir": tmp_path, "work_dir": tmp_path / "work", "log_dir": tmp_path / "logs"}
    )
    return cfg.model_copy(update={"paths": paths})


class _StubProcess:
    def __init__(self, *, cancel_result: bool) -> None:
        self._cancel_result = cancel_result
        self.cancel_calls: list[float] = []

    def cancel(self, *, grace_s: float) -> bool:
        self.cancel_calls.append(grace_s)
        return self._cancel_result


def test_a_clean_reap_emits_no_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cosmo.proc.reap.sweep",
        lambda run_id, task_id, worktree_path, **kw: SweepResult(
            removed_containers=[], worktree_holder_pids=[]
        ),
    )
    cfg = _config(tmp_path)
    writer = StoreWriter(cfg.paths.db_path)
    emitter = EventEmitter(writer)
    process = _StubProcess(cancel_result=True)

    outcome = cancel_and_reap(
        process,  # type: ignore[arg-type]
        run_id="run-1",
        task_id="task-1",
        worktree_path=tmp_path / "worktree",
        config=cfg,
        emitter=emitter,
    )

    assert outcome.fully_reaped is True
    assert process.cancel_calls == [cfg.timeouts.kill_grace]
    row = writer.connection.execute("SELECT COUNT(*) AS n FROM events").fetchone()
    assert row["n"] == 0
    writer.close()


def test_a_failed_killpg_emits_task_failed_with_environment_error_and_breaker_weight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "cosmo.proc.reap.sweep",
        lambda run_id, task_id, worktree_path, **kw: SweepResult(
            removed_containers=[], worktree_holder_pids=[]
        ),
    )
    cfg = _config(tmp_path)
    writer = StoreWriter(cfg.paths.db_path)
    emitter = EventEmitter(writer)
    process = _StubProcess(cancel_result=False)

    outcome = cancel_and_reap(
        process,  # type: ignore[arg-type]
        run_id="run-1",
        task_id="task-1",
        worktree_path=tmp_path / "worktree",
        config=cfg,
        emitter=emitter,
    )

    assert outcome.fully_reaped is False
    row = writer.connection.execute("SELECT * FROM events").fetchone()
    assert row["event_type"] == "task.failed"
    assert row["severity"] == "critical"
    payload = json.loads(row["payload"])
    assert payload["failure_type"] == "environment_error"
    assert payload["circuit_breaker_weight"] == cfg.circuit_breaker.reap_failure_weight
    writer.close()


def test_a_worktree_holder_after_a_clean_killpg_still_counts_as_reap_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "cosmo.proc.reap.sweep",
        lambda run_id, task_id, worktree_path, **kw: SweepResult(
            removed_containers=[], worktree_holder_pids=[4321]
        ),
    )
    cfg = _config(tmp_path)
    writer = StoreWriter(cfg.paths.db_path)
    emitter = EventEmitter(writer)
    process = _StubProcess(cancel_result=True)

    outcome = cancel_and_reap(
        process,  # type: ignore[arg-type]
        run_id="run-1",
        task_id="task-1",
        worktree_path=tmp_path / "worktree",
        config=cfg,
        emitter=emitter,
    )

    assert outcome.fully_reaped is False
    row = writer.connection.execute("SELECT * FROM events").fetchone()
    assert row is not None
    writer.close()
