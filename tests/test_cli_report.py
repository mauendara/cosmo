"""`cosmo report` (plan Phase 9): renders one run's `run_state` row plus its
`run.summary` event payload for post-run triage."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cosmo.cli.main import app
from cosmo.config import load_config
from cosmo.events import EventEmitter, EventType, Severity
from cosmo.store import StoreWriter
from cosmo.store.enums import RunStatus

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSMO_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


def _seed_run(db_path: Path, *, run_id: str = "run-1") -> None:
    writer = StoreWriter(db_path)
    try:
        writer.run_create(
            run_id=run_id,
            harness="claude",
            permission_mode="dontAsk",
            max_turns=80,
            base_branch="develop",
        )
        writer.run_transition(run_id, RunStatus.RUNNING)
        emitter = EventEmitter(writer)
        emitter.emit(
            event_type=EventType.RUN_SUMMARY,
            severity=Severity.INFO,
            run_id=run_id,
            payload={
                "completed": 2,
                "blocked": 1,
                "blocked_by_reason": {"cost": 1},
                "requeued": 0,
                "retried": 1,
                "flaky_detected": ["some.spec.ts"],
                "repeated_merge_conflict_tasks": [],
                "knowledge_files_near_cap": [],
                "total_duration_seconds": 120.0,
                "total_cost_usd": 3.5,
            },
        )
        writer.run_transition(run_id, RunStatus.STOPPED, stop_reason=None)
    finally:
        writer.close()


def test_report_with_no_runs_at_all_fails_cleanly(tmp_path: Path) -> None:
    result = runner.invoke(app, ["report"])
    assert result.exit_code == 1
    assert "no runs recorded" in result.output.lower()


def test_report_renders_the_latest_run_by_default(tmp_path: Path) -> None:
    cfg = load_config()
    _seed_run(cfg.paths.db_path)

    result = runner.invoke(app, ["report"])

    assert result.exit_code == 0, result.output
    assert "run-1" in result.output
    assert "completed:     2" in result.output
    assert "blocked:       1" in result.output
    assert "- cost: 1" in result.output
    assert "some.spec.ts" in result.output


def test_report_selects_an_explicit_run_id(tmp_path: Path) -> None:
    cfg = load_config()
    _seed_run(cfg.paths.db_path, run_id="run-a")
    _seed_run(cfg.paths.db_path, run_id="run-b")

    result = runner.invoke(app, ["report", "--run", "run-a"])

    assert result.exit_code == 0, result.output
    assert "run-a" in result.output


def test_report_on_an_unknown_run_id_fails_cleanly(tmp_path: Path) -> None:
    load_config()  # ensure a migrated db exists
    result = runner.invoke(app, ["report", "--run", "nope"])
    assert result.exit_code == 1
    assert "no such run" in result.output.lower()


def test_report_surfaces_tasks_recovered_from_an_interrupted_run(tmp_path: Path) -> None:
    """v5 improvements plan part 1: `run.recovery.reconcile_interrupted_
    tasks` emits `task.interrupted`; `cosmo report` surfaces a one-line
    summary rather than leaving it visible only through a targeted
    `cosmo events tail`."""
    cfg = load_config()
    _seed_run(cfg.paths.db_path, run_id="run-1")
    writer = StoreWriter(cfg.paths.db_path)
    EventEmitter(writer).emit(
        event_type=EventType.TASK_INTERRUPTED,
        severity=Severity.WARNING,
        run_id="run-1",
        task_id="scaffold-app",
        payload={"previous_status": "implementing"},
    )
    writer.close()

    result = runner.invoke(app, ["report", "--run", "run-1"])

    assert result.exit_code == 0, result.output
    assert "recovered from an interrupted run" in result.output
    assert "scaffold-app" in result.output


def test_report_omits_the_recovered_line_when_nothing_was_interrupted(tmp_path: Path) -> None:
    cfg = load_config()
    _seed_run(cfg.paths.db_path, run_id="run-1")

    result = runner.invoke(app, ["report", "--run", "run-1"])

    assert result.exit_code == 0, result.output
    assert "recovered from an interrupted run" not in result.output


def test_follow_stops_as_soon_as_the_run_is_already_stopped(tmp_path: Path) -> None:
    """v5 improvements plan part 4: `--follow` polls until the run reaches a
    terminal status -- already `stopped` here, so the loop must break
    before its first `sleep`, meaning this test needs no monkeypatching to
    stay fast."""
    cfg = load_config()
    _seed_run(cfg.paths.db_path)

    result = runner.invoke(app, ["report", "--run", "run-1", "--follow"])

    assert result.exit_code == 0, result.output
    assert "run-1" in result.output
    assert "completed:     2" in result.output
