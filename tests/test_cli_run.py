"""`cosmo run --task <id>` (plan Phase 7 exit criterion command): the CLI's
own glue -- task lookup/status validation, worktree creation, and building
the right `TaskContext` -- tested with `task.machine.run_task` itself
monkeypatched out. The state machine's own behavior is `test_task_machine
.py`'s job; `cosmo run` has no gate-injection seam (by design -- a real
invocation always uses the real Docker gate), so driving it end to end
through the actual CLI entry point without Docker isn't possible here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import cosmo.cli.main as cli_main
from cosmo.cli.main import app
from cosmo.config import load_config
from cosmo.harness import get_adapter as real_get_adapter
from cosmo.harness.fake import FakeHarnessAdapter
from cosmo.store import StoreWriter
from cosmo.store.enums import TaskStatus
from cosmo.store.reader import get_task
from cosmo.task.types import TaskContext

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSMO_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


@pytest.fixture(autouse=True)
def _fake_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_main, "get_adapter", lambda name: FakeHarnessAdapter)


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
    # `cosmo run` now validates `--repo` against a real registration
    # (`cli.main._resolve_project_repo`) -- register it directly rather
    # than a full `cosmo init` (this file fakes the harness adapter and has
    # no need for `openspec/`/`docs/`/template sync).
    writer = StoreWriter(load_config().paths.db_path)
    try:
        writer.register_project(target_path=str(repo.resolve()), harness="claude")
    finally:
        writer.close()
    return repo


def test_run_rejects_an_unknown_task(tmp_path: Path) -> None:
    repo = _repo_on_develop(tmp_path)

    result = runner.invoke(app, ["run", "--task", "nope", "--repo", str(repo)])

    assert result.exit_code == 1
    assert "no such task" in result.output


def test_run_rejects_an_unknown_harness_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Found by hand: an unknown --harness used to escape as a raw traceback
    # instead of the same clean error `cosmo doctor` already gives. Restores
    # the real `get_adapter` for this one test -- the module-level
    # `_fake_adapter` fixture would otherwise mask the real registry lookup
    # this test exists to exercise.
    monkeypatch.setattr(cli_main, "get_adapter", real_get_adapter)
    repo = _repo_on_develop(tmp_path)
    runner.invoke(app, ["queue", "add", "openspec/changes/add-foo", "--task-id", "add-foo"])

    result = runner.invoke(
        app, ["run", "--task", "add-foo", "--repo", str(repo), "--harness", "bogus"]
    )

    assert result.exit_code == 1
    # A clean `typer.Exit` (-> SystemExit), not the raw `UnknownHarnessError`
    # escaping uncaught.
    assert isinstance(result.exception, SystemExit)
    assert "unknown harness" in result.output


def test_run_rejects_a_task_that_is_not_queued(tmp_path: Path) -> None:
    repo = _repo_on_develop(tmp_path)
    runner.invoke(app, ["queue", "add", "openspec/changes/add-foo", "--task-id", "add-foo"])
    runner.invoke(app, ["queue", "block", "add-foo", "--reason", "environment"])

    result = runner.invoke(app, ["run", "--task", "add-foo", "--repo", str(repo)])

    assert result.exit_code == 1
    assert "not queued" in result.output


def test_run_task_reconciles_a_crash_orphaned_task_before_the_status_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found live: `cosmo run --task` never called `run.recovery.
    reconcile_interrupted_tasks` at all -- a real `kill -9` against a
    `cosmo run --task` process left the task stuck at whatever non-`queued`
    status it was mid-attempt, and the *next* `cosmo run --task <same-id>`
    hit the `not queued` check and refused outright, forever, with no way
    to recover short of `queue retry` (a genuine fresh start, discarding
    the worktree). Simulates the crash directly (no real process kill
    needed to set this up, matching this project's own fake/unit-coverage
    convention) by pushing the task straight to `implementing` -- the
    status a real crash mid-`IMPLEMENTING` would leave behind."""
    repo = _repo_on_develop(tmp_path)
    runner.invoke(app, ["queue", "add", "openspec/changes/add-foo", "--task-id", "add-foo"])
    db_path = load_config().paths.db_path
    writer = StoreWriter(db_path)
    try:
        writer.queue_transition("add-foo", TaskStatus.PROPOSING)
        writer.queue_transition("add-foo", TaskStatus.PROPOSED)
        writer.queue_transition("add-foo", TaskStatus.IMPLEMENTING)
    finally:
        writer.close()

    def _fake_run_task(*, ctx: TaskContext, **kwargs: Any) -> TaskStatus:
        return TaskStatus.DONE

    monkeypatch.setattr(cli_main, "run_task", _fake_run_task)

    result = runner.invoke(app, ["run", "--task", "add-foo", "--repo", str(repo)])

    assert result.exit_code == 0, result.output
    assert "not queued" not in result.output
    assert "done" in result.output

    from cosmo.store.reader import list_events, list_task_failures

    events = list_events(db_path, task_id="add-foo", limit=200)
    assert any(e.event_type == "task.interrupted" for e in events)
    failures = list_task_failures(db_path, "add-foo")
    assert any(
        f.failure_type == "environment_error" and f.run_id is None and "crashed" in f.error_summary
        for f in failures
    )


def test_run_task_reports_a_held_run_lock_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_on_develop(tmp_path)
    runner.invoke(app, ["queue", "add", "openspec/changes/add-foo", "--task-id", "add-foo"])

    from cosmo.run.recovery import RunLockHeldError

    def _fake_acquire_run_lock(data_dir: Path) -> None:
        raise RunLockHeldError(f"another cosmo run already holds a lock in {data_dir}")

    monkeypatch.setattr(cli_main, "acquire_run_lock", _fake_acquire_run_lock)

    result = runner.invoke(app, ["run", "--task", "add-foo", "--repo", str(repo)])

    assert result.exit_code == 1
    assert "already holds a lock" in result.output


def test_run_creates_the_worktree_and_drives_run_task_to_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_on_develop(tmp_path)
    runner.invoke(app, ["queue", "add", "openspec/changes/add-foo", "--task-id", "add-foo"])

    captured: dict[str, Any] = {}

    def _fake_run_task(*, ctx: TaskContext, **kwargs: Any) -> TaskStatus:
        captured["ctx"] = ctx
        return TaskStatus.DONE

    monkeypatch.setattr(cli_main, "run_task", _fake_run_task)

    result = runner.invoke(app, ["run", "--task", "add-foo", "--repo", str(repo)])

    assert result.exit_code == 0, result.output
    assert "done" in result.output

    ctx = captured["ctx"]
    assert ctx.task_id == "add-foo"
    assert ctx.spec_path == "openspec/changes/add-foo"
    assert ctx.base_branch == "develop"
    assert ctx.branch == "task/add-foo"
    assert ctx.worktree_path.is_dir()
    assert (ctx.worktree_path / ".agent" / "claude" / "CLAUDE.md").is_file()

    db_path = load_config().paths.db_path
    task = get_task(db_path, "add-foo")
    assert task is not None
    assert task.worktree_path is not None


def test_run_reuses_an_existing_worktree_instead_of_recreating_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: a `QUEUED` task can already have a `worktree_path` --
    e.g. `queue retry`'s kept-worktree path, or a prior `cosmo run` process
    that died mid-attempt. `run_cmd` used to call `create_worktree`
    unconditionally, which names the branch `task/<spec_id>` regardless of
    `run_id` -- a second, brand-new worktree for the same task collided
    with the branch the first worktree still had checked out, and
    `cosmo run --task` failed on `git worktree add` before ever invoking the
    harness. Same reuse rule as `run.loop._run_one_task` (spec 3.2)."""
    repo = _repo_on_develop(tmp_path)
    runner.invoke(app, ["queue", "add", "openspec/changes/add-foo", "--task-id", "add-foo"])

    captured: dict[str, Any] = {}

    def _fake_run_task(*, ctx: TaskContext, **kwargs: Any) -> TaskStatus:
        captured["ctx"] = ctx
        return TaskStatus.DONE

    monkeypatch.setattr(cli_main, "run_task", _fake_run_task)

    first = runner.invoke(app, ["run", "--task", "add-foo", "--repo", str(repo)])
    assert first.exit_code == 0, first.output
    first_worktree = captured["ctx"].worktree_path
    assert first_worktree.is_dir()

    db_path = load_config().paths.db_path
    writer = StoreWriter(db_path)
    try:
        writer.queue_retry("add-foo", clear_worktree=False)
    finally:
        writer.close()

    second = runner.invoke(app, ["run", "--task", "add-foo", "--repo", str(repo)])

    assert second.exit_code == 0, second.output
    assert captured["ctx"].worktree_path == first_worktree
    assert captured["ctx"].branch == "task/add-foo"


def test_run_exits_nonzero_when_the_task_ends_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_on_develop(tmp_path)
    runner.invoke(app, ["queue", "add", "openspec/changes/add-foo", "--task-id", "add-foo"])
    monkeypatch.setattr(cli_main, "run_task", lambda **_kwargs: TaskStatus.BLOCKED)

    result = runner.invoke(app, ["run", "--task", "add-foo", "--repo", str(repo)])

    assert result.exit_code == 1
    assert "blocked" in result.output
