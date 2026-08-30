"""`cosmo run` with no `--task` (plan Phase 8 exit criteria): CLI glue only
-- routing to `run.loop.run_queue`, `--dry-run`'s DAG-order preview, and
`queue add`'s cycle rejection at enqueue. `run_queue` itself is
`test_run_loop.py`'s job, monkeypatched out here the same way
`test_cli_run.py` monkeypatches `task.machine.run_task` for the
single-task path."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import cosmo.cli.main as cli_main
from cosmo.cli.main import app
from cosmo.config import load_config
from cosmo.harness.fake import FakeHarnessAdapter
from cosmo.run.types import RunOutcome, RunSummary
from cosmo.store import StoreWriter
from cosmo.store.enums import RunStatus, StopReason

runner = CliRunner()


def _register(repo: Path, *, harness: str = "claude") -> None:
    """`cosmo run` now validates `--repo` against a real registration
    (`cli.main._resolve_project_repo`) -- register it directly, matching
    `cosmo init`'s own `str(path.resolve())` storage convention."""
    writer = StoreWriter(load_config().paths.db_path)
    try:
        writer.register_project(target_path=str(repo.resolve()), harness=harness)
    finally:
        writer.close()


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSMO_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


@pytest.fixture(autouse=True)
def _fake_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_main, "get_adapter", lambda name: FakeHarnessAdapter)


def _outcome(**overrides: object) -> RunOutcome:
    base: dict[str, object] = {
        "run_id": "run-1",
        "status": RunStatus.STOPPED,
        "stop_reason": StopReason.QUEUE_EMPTY,
        "summary": RunSummary(),
        "execution_order": [],
    }
    base.update(overrides)
    return RunOutcome(**base)  # type: ignore[arg-type]


def test_dry_run_prints_no_eligible_tasks_on_an_empty_queue(tmp_path: Path) -> None:
    _register(tmp_path)
    result = runner.invoke(app, ["run", "--repo", str(tmp_path), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "no eligible" in result.output.lower()


def test_dry_run_prints_the_resolved_dependency_order(tmp_path: Path) -> None:
    _register(tmp_path)
    runner.invoke(app, ["queue", "add", "openspec/changes/a", "--task-id", "a"])
    runner.invoke(
        app, ["queue", "add", "openspec/changes/b", "--task-id", "b", "--depends-on", "a"]
    )

    result = runner.invoke(app, ["run", "--repo", str(tmp_path), "--dry-run"])

    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line and line[0].isdigit()]
    assert lines == ["1. a", "2. b"]


def test_dry_run_reports_a_cycle_cleanly(tmp_path: Path) -> None:
    # `queue add` itself rejects a cycle at enqueue (see the dedicated test
    # below) -- bypass it here via the writer directly, so `--dry-run`'s
    # own defensive `DagCycleError` handling is exercised for real.
    _register(tmp_path)
    cfg = load_config()
    writer = StoreWriter(cfg.paths.db_path)
    try:
        writer.queue_add(
            task_id="a", spec_path="openspec/changes/a", depends_on=["b"], max_attempts=2
        )
        writer.queue_add(
            task_id="b", spec_path="openspec/changes/b", depends_on=["a"], max_attempts=2
        )
    finally:
        writer.close()

    result = runner.invoke(app, ["run", "--repo", str(tmp_path), "--dry-run"])

    assert result.exit_code == 1
    assert "cycle" in result.output.lower()


def test_queue_add_rejects_a_cycle_at_enqueue(tmp_path: Path) -> None:
    runner.invoke(
        app, ["queue", "add", "openspec/changes/a", "--task-id", "a", "--depends-on", "b"]
    )

    result = runner.invoke(
        app, ["queue", "add", "openspec/changes/b", "--task-id", "b", "--depends-on", "a"]
    )

    assert result.exit_code == 1
    assert "cycle" in result.output.lower()


def test_no_task_flag_routes_to_run_queue_and_prints_the_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(tmp_path)
    runner.invoke(app, ["queue", "add", "openspec/changes/a", "--task-id", "a"])
    captured: dict[str, Any] = {}

    def _fake_run_queue(**kwargs: Any) -> RunOutcome:
        captured.update(kwargs)
        return _outcome(status=RunStatus.STOPPED, stop_reason=StopReason.QUEUE_EMPTY)

    monkeypatch.setattr(cli_main, "run_queue", _fake_run_queue)

    result = runner.invoke(app, ["run", "--repo", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "queue_empty" in result.output
    assert captured["repo_path"] == tmp_path.resolve()
    assert captured["base_branch"] == "develop"
    assert captured["harness_name"] == "claude"


def test_a_paused_or_non_terminal_outcome_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(tmp_path)
    monkeypatch.setattr(
        cli_main,
        "run_queue",
        lambda **_kw: _outcome(status=RunStatus.PAUSED, stop_reason=None),
    )

    result = runner.invoke(app, ["run", "--repo", str(tmp_path)])

    assert result.exit_code == 1
    assert "paused" in result.output


def test_blocked_remaining_exits_nonzero_and_is_not_styled_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v7: a stop caused by every remaining task being BLOCKED must read as
    a failure (nonzero exit, not green) -- it used to share QUEUE_EMPTY's
    exit-0/green treatment, which is exactly the bug this stop reason
    exists to fix."""
    _register(tmp_path)
    monkeypatch.setattr(
        cli_main,
        "run_queue",
        lambda **_kw: _outcome(status=RunStatus.STOPPED, stop_reason=StopReason.BLOCKED_REMAINING),
    )

    result = runner.invoke(app, ["run", "--repo", str(tmp_path)])

    assert result.exit_code == 1
    assert "blocked_remaining" in result.output
