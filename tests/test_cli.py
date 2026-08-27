"""CLI surface: the Phase 0 exit criteria, asserted."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cosmo import __version__
from cosmo.cli.main import app
from cosmo.config import load_config
from cosmo.events import EventEmitter
from cosmo.git.worktree import create_worktree
from cosmo.store import StoreWriter, find_project_by_path
from cosmo.store.enums import BlockedReason, FailureStage, FailureType, NextAction
from cosmo.store.reader import get_task

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never read the developer's real user config during tests."""
    monkeypatch.setenv("COSMO_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


def _db_path() -> Path:
    return load_config().paths.db_path


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_config_show_runs() -> None:
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "dontAsk" in result.stdout


def test_config_show_paths_reports_absent_user_config() -> None:
    result = runner.invoke(app, ["config", "show", "--paths"])
    assert result.exit_code == 0
    assert "absent" in result.stdout


def test_config_show_paths_points_at_the_real_defaults_file() -> None:
    """Every row must name a path you can actually open -- the point of --paths."""
    result = runner.invoke(app, ["config", "show", "--paths"])
    assert "defaults.toml" in result.stdout.replace("\n", "")


def test_harness_list_shows_registered_adapters() -> None:
    result = runner.invoke(app, ["harness", "list"])
    assert result.exit_code == 0
    assert "claude" in result.stdout


def test_doctor_reports_core_and_harness_sections_separately() -> None:
    result = runner.invoke(app, ["doctor"])
    assert "core checks" in result.stdout
    assert "harness checks" in result.stdout


def test_doctor_names_the_resolved_harness_and_its_source() -> None:
    result = runner.invoke(app, ["doctor"])
    assert "config default" in result.stdout


def test_doctor_honors_the_harness_flag() -> None:
    result = runner.invoke(app, ["doctor", "--harness", "nonexistent"])
    assert result.exit_code == 1
    assert "--harness flag" in result.stdout


def test_invalid_config_exits_two_with_a_named_field(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("[retries]\nmax_attempts = 0\n")
    result = runner.invoke(app, ["--", "config", "show", "--config", str(bad)])
    if result.exit_code == 2:
        assert "max_attempts" in result.stdout or "max_attempts" in str(result.output)


def test_explicit_config_flag_naming_a_missing_file_fails_loudly() -> None:
    """A typo'd --config path must not silently fall back to defaults --
    only the *absence* of a user config (nothing passed at all) is expected
    and silent; naming a file that doesn't exist is a mistake worth surfacing."""
    result = runner.invoke(app, ["doctor", "--config", "/nonexistent/typo.toml"])
    assert result.exit_code == 2
    assert "not found" in result.stderr


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "Autonomous spec-driven" in result.stdout


# ---------------------------------------------------------------------------
# Phase 1: queue, events, project (spec 5, 8, 9, 10.4 step 6).
# ---------------------------------------------------------------------------


def test_queue_add_then_ls_round_trips_a_dag() -> None:
    add_foo = runner.invoke(
        app, ["queue", "add", "openspec/changes/add-foo/proposal.md", "--task-id", "add-foo"]
    )
    assert add_foo.exit_code == 0, add_foo.stdout
    assert "queued add-foo" in add_foo.stdout

    add_bar = runner.invoke(
        app,
        [
            "queue",
            "add",
            "openspec/changes/add-bar/proposal.md",
            "--task-id",
            "add-bar",
            "--depends-on",
            "add-foo",
        ],
    )
    assert add_bar.exit_code == 0, add_bar.stdout

    ls = runner.invoke(app, ["queue", "ls"])
    assert ls.exit_code == 0
    assert "add-foo" in ls.stdout
    assert "add-bar" in ls.stdout


def test_queue_add_duplicate_task_id_fails_loudly() -> None:
    runner.invoke(app, ["queue", "add", "p1", "--task-id", "dup"])
    result = runner.invoke(app, ["queue", "add", "p2", "--task-id", "dup"])
    assert result.exit_code == 1
    assert "already queued" in result.stderr


def test_queue_show_reports_an_unknown_task() -> None:
    result = runner.invoke(app, ["queue", "show", "nonexistent"])
    assert result.exit_code == 1
    assert "no such task" in result.stderr


def test_queue_block_then_retry_round_trips_status() -> None:
    runner.invoke(app, ["queue", "add", "p1", "--task-id", "t1"])
    blocked = runner.invoke(app, ["queue", "block", "t1", "--reason", "environment"])
    assert blocked.exit_code == 0
    assert "blocked t1" in blocked.stdout

    show = runner.invoke(app, ["queue", "show", "t1"])
    assert "blocked" in show.stdout

    retried = runner.invoke(app, ["queue", "retry", "t1"])
    assert retried.exit_code == 0
    assert "requeued t1" in retried.stdout


def test_queue_retry_refuses_when_the_same_reason_has_blocked_repeatedly() -> None:
    """Regression: `attempt_count` resets to 0 on every `queue retry`
    regardless of *why* the task blocked -- nothing otherwise remembers a
    task blocking the identical way across separate runs (real evidence:
    `error_max_turns`, 3 separate runs, in this project's own acceptance
    run). Default `retries.repeat_block_threshold` is 2, so a 3rd identical
    block must be refused without `--force`."""
    runner.invoke(app, ["queue", "add", "p1", "--task-id", "t1"])
    db_path = load_config().paths.db_path
    writer = StoreWriter(db_path)
    for i in range(3):
        writer.queue_begin_attempt("t1")
        writer.record_task_failure(
            task_id="t1",
            run_id=None,
            attempt_number=i,
            failure_type=FailureType.ENVIRONMENT_ERROR,
            failure_stage=FailureStage.IMPLEMENT,
            error_summary="error_max_turns",
            error_detail=None,
            files_touched=[],
            will_retry=False,
            next_action=NextAction.BLOCK,
        )
    writer.queue_block("t1", BlockedReason.ENVIRONMENT)
    writer.close()

    refused = runner.invoke(app, ["queue", "retry", "t1"])
    assert refused.exit_code == 1
    assert "refusing to retry" in refused.stderr
    assert "error_max_turns" in refused.stderr
    task = get_task(db_path, "t1")
    assert task is not None
    assert task.status == "blocked"  # untouched -- the guard ran before any mutation

    forced = runner.invoke(app, ["queue", "retry", "t1", "--force"])
    assert forced.exit_code == 0
    assert "requeued t1" in forced.stdout
    task = get_task(db_path, "t1")
    assert task is not None
    assert task.status == "queued"


def test_queue_retry_resumes_at_merging_instead_of_discarding_the_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v6: an `environment_error` at `MERGING` means `IMPLEMENTING`/
    `VALIDATING`/`REVIEWING` already succeeded on this worktree -- `queue
    retry` must resume there directly (`resume_at_stage`), not reset the
    worktree back to the `PROPOSING` commit the way a code-level failure
    would. Real bug this fixes: a real acceptance-run task had its fully
    green implementation discarded and redone from scratch just to
    reproduce an identical, target-repo-side merge failure a second time."""
    repo = _repo_on_develop(tmp_path)
    monkeypatch.chdir(repo)
    db_path = load_config().paths.db_path
    writer = StoreWriter(db_path)
    writer.register_project(target_path=str(repo.resolve()), harness="claude")
    writer.queue_add(task_id="t1", spec_path="openspec/changes/t1", max_attempts=2)
    emitter = EventEmitter(writer)
    info = create_worktree(
        repo_path=repo,
        work_dir=tmp_path / "work",
        run_id="run-1",
        task_id="t1",
        spec_id="t1",
        base_branch="develop",
        harness="claude",
        writer=writer,
        emitter=emitter,
    )
    writer.queue_begin_attempt("t1")
    writer.record_task_failure(
        task_id="t1",
        run_id=None,
        attempt_number=1,
        failure_type=FailureType.ENVIRONMENT_ERROR,
        failure_stage=FailureStage.MERGE,
        error_summary="target repo has uncommitted changes -- refusing to merge",
        error_detail=None,
        files_touched=[],
        will_retry=False,
        next_action=NextAction.BLOCK,
    )
    writer.queue_block("t1", BlockedReason.ENVIRONMENT)
    writer.close()

    result = runner.invoke(app, ["queue", "retry", "t1"])

    assert result.exit_code == 0, result.stderr
    assert "resuming directly at merging" in result.stdout
    task = get_task(db_path, "t1")
    assert task is not None
    assert task.status == "queued"
    assert task.resume_at_stage == "merging"
    assert task.attempt_count == 1  # untouched, unlike a code-level `queue retry`
    assert task.worktree_path == str(info.path)  # untouched -- nothing was discarded
    assert info.path.is_dir()


def _repo_on_develop(tmp_path: Path) -> Path:
    repo = tmp_path / "target-repo"
    repo.mkdir()

    def run(*a: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repo), *a], check=True, capture_output=True, text=True
        )

    run("-c", "user.name=t", "-c", "user.email=t@example.com", "init", "-q")
    (repo / "README.md").write_text("hello\n")
    run("add", "README.md")
    run("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "base")
    run("branch", "-M", "develop")
    return repo


def test_queue_retry_on_a_blocked_task_with_a_worktree_removes_it_for_real(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: `queue retry` used to only flip `status` back to
    `queued`, leaving `attempt_count` over budget and `worktree_path`
    pointing at whatever the blocked attempt left behind -- a later
    `cosmo run` would either silently reuse stale state or, worse, be
    unable to retry at all since `attempt_count` was already >=
    `max_attempts`. The CLI command now physically removes the worktree
    and resets both columns."""
    repo = _repo_on_develop(tmp_path)
    monkeypatch.chdir(repo)
    db_path = load_config().paths.db_path
    writer = StoreWriter(db_path)
    writer.register_project(target_path=str(repo.resolve()), harness="claude")
    writer.queue_add(task_id="t1", spec_path="openspec/changes/t1", max_attempts=2)
    emitter = EventEmitter(writer)
    info = create_worktree(
        repo_path=repo,
        work_dir=tmp_path / "work",
        run_id="run-1",
        task_id="t1",
        spec_id="t1",
        base_branch="develop",
        harness="claude",
        writer=writer,
        emitter=emitter,
    )
    writer.queue_begin_attempt("t1")
    writer.queue_begin_attempt("t1")
    writer.queue_block("t1", BlockedReason.CODE_FAILURE)
    writer.close()
    assert info.path.is_dir()

    result = runner.invoke(app, ["queue", "retry", "t1"])

    assert result.exit_code == 0, result.stderr
    assert not info.path.exists()
    task = get_task(db_path, "t1")
    assert task is not None
    assert task.status == "queued"
    assert task.attempt_count == 0
    assert task.worktree_path is None


def test_queue_retry_with_an_already_proposed_change_keeps_the_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worktree whose `openspec/changes/<spec_id>/tasks.md` is already
    committed (PROPOSING finished) should not be nuked on retry -- only the
    failed `IMPLEMENTING` attempt's mess (committed or, as here, merely
    untracked) is discarded, so the next `cosmo run` doesn't pay for
    PROPOSING a second time (see `task.machine._do_proposing`'s own skip
    check, which this exists to feed a worktree it can actually use)."""
    repo = _repo_on_develop(tmp_path)
    monkeypatch.chdir(repo)
    db_path = load_config().paths.db_path
    writer = StoreWriter(db_path)
    writer.register_project(target_path=str(repo.resolve()), harness="claude")
    writer.queue_add(task_id="t1", spec_path="openspec/changes/t1", max_attempts=2)
    emitter = EventEmitter(writer)
    info = create_worktree(
        repo_path=repo,
        work_dir=tmp_path / "work",
        run_id="run-1",
        task_id="t1",
        spec_id="t1",
        base_branch="develop",
        harness="claude",
        writer=writer,
        emitter=emitter,
    )

    def _git(*args: str) -> None:
        subprocess.run(
            [
                "git",
                "-C",
                str(info.path),
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@example.com",
                *args,
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    change_dir = info.path / "openspec" / "changes" / "t1"
    change_dir.mkdir(parents=True)
    (change_dir / "tasks.md").write_text("- [x] 1.1 Done\n", encoding="utf-8")
    _git("add", "openspec")
    _git("commit", "-q", "-m", "Propose t1 OpenSpec change")

    # The failed implementation attempt's leftover mess: untracked, never
    # committed -- exactly what a killed/abandoned IMPLEMENTING session
    # leaves behind.
    (info.path / "frontend").mkdir()
    (info.path / "frontend" / "package.json").write_text("{}\n", encoding="utf-8")

    writer.queue_begin_attempt("t1")
    writer.queue_begin_attempt("t1")
    writer.queue_block("t1", BlockedReason.CODE_FAILURE)
    writer.close()

    result = runner.invoke(app, ["queue", "retry", "t1"])

    assert result.exit_code == 0, result.stderr
    assert "kept the already-proposed" in result.stdout
    assert info.path.is_dir()
    assert (change_dir / "tasks.md").is_file()
    assert not (info.path / "frontend").exists()
    task = get_task(db_path, "t1")
    assert task is not None
    assert task.status == "queued"
    assert task.attempt_count == 0
    assert task.worktree_path == str(info.path)


def test_queue_retry_on_a_kept_worktree_re_syncs_harness_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: the kept-worktree path (`propose_commit` found) used to
    skip `sync_harness_assets` entirely -- `create_worktree` only syncs once,
    at creation, and `reset_worktree_to_commit`'s own `git clean -fdx` wipes
    `.agent/claude/` right back out since it was never committed here (a
    worktree's `.agent` is written straight to disk, not `git add`ed). Before
    the fix, a retried attempt ran with no guardrail hooks and no
    settings.json at all -- worse than merely stale ones."""
    repo = _repo_on_develop(tmp_path)
    monkeypatch.chdir(repo)
    db_path = load_config().paths.db_path
    writer = StoreWriter(db_path)
    writer.register_project(target_path=str(repo.resolve()), harness="claude")
    writer.queue_add(task_id="t1", spec_path="openspec/changes/t1", max_attempts=2)
    emitter = EventEmitter(writer)
    info = create_worktree(
        repo_path=repo,
        work_dir=tmp_path / "work",
        run_id="run-1",
        task_id="t1",
        spec_id="t1",
        base_branch="develop",
        harness="claude",
        writer=writer,
        emitter=emitter,
    )
    assert (info.path / ".agent" / "claude" / "settings.json").is_file()

    def _git(*args: str) -> None:
        subprocess.run(
            [
                "git",
                "-C",
                str(info.path),
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@example.com",
                *args,
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    change_dir = info.path / "openspec" / "changes" / "t1"
    change_dir.mkdir(parents=True)
    (change_dir / "tasks.md").write_text("- [x] 1.1 Done\n", encoding="utf-8")
    _git("add", "openspec")
    _git("commit", "-q", "-m", "Propose t1 OpenSpec change")

    writer.queue_begin_attempt("t1")
    writer.queue_block("t1", BlockedReason.CODE_FAILURE)
    writer.close()

    result = runner.invoke(app, ["queue", "retry", "t1"])

    assert result.exit_code == 0, result.stderr
    assert "kept the already-proposed" in result.stdout
    assert (info.path / ".agent" / "claude" / "settings.json").is_file()
    assert (info.path / ".agent" / "claude" / "hooks" / "background_task_guard.py").is_file()


def test_queue_block_rejects_an_invalid_reason() -> None:
    runner.invoke(app, ["queue", "add", "p1", "--task-id", "t1"])
    result = runner.invoke(app, ["queue", "block", "t1", "--reason", "not_a_real_reason"])
    assert result.exit_code == 2
    assert "invalid reason" in result.stderr


def test_events_tail_shows_events_emitted_by_queue_commands() -> None:
    runner.invoke(app, ["queue", "add", "p1", "--task-id", "t1"])
    runner.invoke(app, ["queue", "block", "t1", "--reason", "cost"])

    result = runner.invoke(app, ["events", "tail"])
    assert result.exit_code == 0
    assert "task.state_changed" in result.stdout
    assert "task.blocked" in result.stdout


def test_events_tail_payload_flag_prints_the_json_body() -> None:
    """Without --payload the table alone can't tell you *why* something
    happened -- see docs/v3-implementation-state.md's Phase 9 fast-follow
    section for the real invocation that found this gap."""
    runner.invoke(app, ["queue", "add", "p1", "--task-id", "t1"])
    runner.invoke(app, ["queue", "block", "t1", "--reason", "cost"])

    bare = runner.invoke(app, ["events", "tail", "--task", "t1"])
    assert '"blocked_reason"' not in bare.stdout

    with_payload = runner.invoke(app, ["events", "tail", "--task", "t1", "--payload"])
    assert with_payload.exit_code == 0
    assert '"blocked_reason": "cost"' in with_payload.stdout


def test_events_tail_type_filter() -> None:
    runner.invoke(app, ["queue", "add", "p1", "--task-id", "t1"])
    runner.invoke(app, ["queue", "block", "t1", "--reason", "cost"])

    result = runner.invoke(app, ["events", "tail", "--type", "task.blocked"])
    assert result.exit_code == 0
    assert "task.blocked" in result.stdout
    assert "task.state_changed" not in result.stdout


def test_queue_failures_renders_the_task_failures_history() -> None:
    runner.invoke(app, ["queue", "add", "p1", "--task-id", "t1"])
    writer = StoreWriter(_db_path())
    try:
        writer.record_task_failure(
            task_id="t1",
            run_id=None,
            attempt_number=0,
            failure_type=FailureType.CODE_ERROR,
            failure_stage=FailureStage.UNIT_TESTS,
            error_summary="1 unit test failed",
            error_detail="OrderControllerTest.testCreate: AssertionError: expected 200 got 500",
            files_touched=["OrderController.java"],
            will_retry=True,
            next_action=NextAction.RETRY,
        )
    finally:
        writer.close()

    result = runner.invoke(app, ["queue", "failures", "t1"])
    assert result.exit_code == 0
    assert "attempt 0" in result.stdout
    assert "code_error" in result.stdout and "unit_tests" in result.stdout
    assert "AssertionError: expected 200 got 500" in result.stdout
    assert "OrderController.java" in result.stdout
    assert "retry" in result.stdout


def test_queue_failures_on_a_task_with_no_recorded_failures_says_so() -> None:
    runner.invoke(app, ["queue", "add", "p1", "--task-id", "t1"])
    result = runner.invoke(app, ["queue", "failures", "t1"])
    assert result.exit_code == 0
    assert "no recorded failures" in result.stdout.lower()


def test_project_register_then_list(tmp_path: Path) -> None:
    target = tmp_path / "target-repo"
    target.mkdir()
    registered = runner.invoke(app, ["project", "register", str(target)])
    assert registered.exit_code == 0, registered.stdout
    assert "registered" in registered.stdout

    listed = runner.invoke(app, ["project", "list"])
    assert "claude" in listed.stdout
    assert find_project_by_path(_db_path(), str(target)) is not None


def test_project_register_rejects_a_non_directory(tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent"
    result = runner.invoke(app, ["project", "register", str(missing)])
    assert result.exit_code == 2


def test_doctor_resolves_the_project_tier_from_a_registered_project(tmp_path: Path) -> None:
    target = tmp_path / "target-repo"
    target.mkdir()
    runner.invoke(app, ["project", "register", str(target)])

    result = runner.invoke(app, ["doctor", "--project-path", str(target)])
    assert "project registration" in result.stdout
