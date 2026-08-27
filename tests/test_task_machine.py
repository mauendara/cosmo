"""`task.machine.run_task` against `FakeHarnessAdapter` + `FakeGate` (plan
Phase 7 exit criterion): one task driven through every state with a
complete event trail, plus the four scenarios the plan's exit criteria name
explicitly: retry exhaustion -> BLOCKED with the right `blocked_reason`, an
`environment_error` at `IMPLEMENTING` not consuming an attempt, and a
`VALIDATING` environment_error (Phase 6's own stand-in for "the gate
timed out" -- see `StageResult.timed_out`'s docstring) not consuming one
either.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from cosmo.config import CosmoConfig, load_config
from cosmo.events import EventEmitter
from cosmo.gate.fake import FakeGate, ScriptedGateResult
from cosmo.gate.types import GateResult
from cosmo.git.worktree import create_worktree
from cosmo.harness.fake import FakeHarnessAdapter, FakeOutcome, ScriptedCall
from cosmo.store import StoreWriter
from cosmo.store.enums import FailureStage, FailureType, TaskStatus
from cosmo.store.reader import get_task, list_events
from cosmo.task.machine import _git_commit_decisions_log, run_task
from cosmo.task.types import TaskContext

NO_USER_CONFIG = Path("/nonexistent/config.toml")


def _fast_config(tmp_path: Path) -> CosmoConfig:
    cfg = load_config(config_path=NO_USER_CONFIG)
    paths = cfg.paths.model_copy(
        update={
            "data_dir": tmp_path / "data",
            "work_dir": tmp_path / "work",
            "log_dir": tmp_path / "logs",
        }
    )
    retries = cfg.retries.model_copy(update={"delay_min": 0, "delay_max": 0})
    # v4 workflow changes: REVIEWING is real by default (config.review.enabled
    # defaults true) and would otherwise run for every test in this file via
    # FakeHarnessAdapter's reused SUCCESS script, which writes no verdict file
    # -- disabled here so these tests keep exercising exactly what they did
    # before REVIEWING existed. `test_task_reviewing.py` covers REVIEWING itself.
    review = cfg.review.model_copy(update={"enabled": False})
    return cfg.model_copy(update={"paths": paths, "retries": retries, "review": review})


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


def _setup(
    tmp_path: Path, task_id: str = "add-foo"
) -> tuple[CosmoConfig, Path, StoreWriter, EventEmitter, TaskContext]:
    cfg = _fast_config(tmp_path)
    repo = _repo_on_develop(tmp_path)
    writer = StoreWriter(cfg.paths.db_path)
    writer.queue_add(task_id=task_id, spec_path="openspec/changes/add-foo", max_attempts=2)
    emitter = EventEmitter(writer)
    info = create_worktree(
        repo_path=repo,
        work_dir=cfg.paths.work_dir,
        run_id="run-1",
        task_id=task_id,
        spec_id=task_id,
        base_branch="develop",
        harness="claude",
        writer=writer,
        emitter=emitter,
    )
    ctx = TaskContext(
        task_id=task_id,
        spec_path="openspec/changes/add-foo",
        worktree_path=info.path,
        branch=info.branch,
        base_branch="develop",
        allow_test_edits=False,
        max_attempts=2,
    )
    return cfg, repo, writer, emitter, ctx


def _gate_runner(gate: FakeGate) -> Callable[..., GateResult]:
    def _run(*, task_id: str, **_kwargs: object) -> GateResult:
        return gate.validate(task_id)

    return _run


def test_happy_path_reaches_done_with_a_complete_event_trail(tmp_path: Path) -> None:
    cfg, repo, writer, emitter, ctx = _setup(tmp_path)
    adapter = FakeHarnessAdapter(
        cfg, cwd=ctx.worktree_path, script=ScriptedCall(FakeOutcome.SUCCESS)
    )
    gate = FakeGate(ScriptedGateResult(passed=True))

    try:
        status = run_task(
            ctx=ctx,
            config=cfg,
            writer=writer,
            emitter=emitter,
            adapter=adapter,
            repo_path=repo,
            gate_runner=_gate_runner(gate),
        )

        assert status is TaskStatus.DONE
        task = get_task(cfg.paths.db_path, ctx.task_id)
        assert task is not None
        assert task.status == "done"

        # `queued` itself predates `run_task` (written by `queue_add`, which
        # the CLI -- not this test -- is responsible for pairing with its
        # own `task.state_changed`); everything from `proposing` on is what
        # `run_task` itself drives.
        transitions = [
            e.payload["to_state"]
            for e in reversed(list_events(cfg.paths.db_path, task_id=ctx.task_id, limit=200))
            if e.event_type == "task.state_changed"
        ]
        # REVIEWING is disabled by `_fast_config` (see its own comment) so it
        # never appears here; FINISHING always runs on a merged task
        # regardless -- `openspec` has nothing to archive in this fixture
        # repo (no `openspec/` at all), so it fails best-effort and still
        # reaches `done` a second time (see `_do_finishing`'s own docstring
        # for why the trail legitimately reads `..., done, finishing, done`).
        assert transitions == [
            "proposing",
            "proposed",
            "implementing",
            "validating",
            "committing",
            "merging",
            "done",
            "finishing",
            "done",
        ]

        event_types = {
            e.event_type for e in list_events(cfg.paths.db_path, task_id=ctx.task_id, limit=200)
        }
        assert "task.validation_result" in event_types
        assert "task.completed" in event_types
    finally:
        writer.close()


def test_resume_at_merging_skips_straight_there_calling_neither_harness_nor_gate(
    tmp_path: Path,
) -> None:
    """v6: a task whose most recent block was an `environment_error` at
    `MERGING` has already been proposed, implemented, validated, and
    (when enabled) reviewed -- none of that needs redoing, only the merge
    itself. `resume_at=TaskStatus.MERGING` must reach `DONE` without ever
    calling `propose`/`implement`/`review` or re-running the gate -- the
    real bug this fixes: `queue retry` used to discard a fully green
    implementation just to reproduce the identical merge failure a second
    time."""
    cfg, repo, writer, emitter, ctx = _setup(tmp_path)
    # Stand in for "IMPLEMENTING/VALIDATING/REVIEWING/COMMITTING already
    # succeeded in an earlier `cosmo run` process" -- a real commit on the
    # task branch, mergeable into `develop`, with no fake-adapter call
    # involved in producing it.
    (ctx.worktree_path / "feature.txt").write_text("done\n", encoding="utf-8")
    _git(ctx.worktree_path, "add", "feature.txt")
    _git(
        ctx.worktree_path,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "commit",
        "-q",
        "-m",
        "Implement add-foo",
    )
    adapter = FakeHarnessAdapter(
        cfg, cwd=ctx.worktree_path, script=ScriptedCall(FakeOutcome.SUCCESS)
    )
    gate = FakeGate(ScriptedGateResult(passed=True))

    try:
        status = run_task(
            ctx=ctx,
            config=cfg,
            writer=writer,
            emitter=emitter,
            adapter=adapter,
            repo_path=repo,
            gate_runner=_gate_runner(gate),
            resume_at=TaskStatus.MERGING,
        )

        assert status is TaskStatus.DONE
        assert adapter.calls == []
        assert gate.calls == []
        task = get_task(cfg.paths.db_path, ctx.task_id)
        assert task is not None
        assert task.status == "done"
        transitions = [
            e.payload["to_state"]
            for e in reversed(list_events(cfg.paths.db_path, task_id=ctx.task_id, limit=200))
            if e.event_type == "task.state_changed"
        ]
        assert transitions == ["merging", "done", "finishing", "done"]
    finally:
        writer.close()


def test_resume_at_committing_skips_straight_there_calling_neither_harness_nor_gate(
    tmp_path: Path,
) -> None:
    """Same shape as the `MERGING` case above, for a task whose most recent
    block was an `environment_error` at `COMMITTING` itself (a `git commit`
    failure, e.g. a lock file) -- `IMPLEMENTING`/`VALIDATING`/`REVIEWING`
    already succeeded, only `COMMITTING`+`MERGING` need retrying."""
    cfg, repo, writer, emitter, ctx = _setup(tmp_path)
    (ctx.worktree_path / "feature.txt").write_text("done\n", encoding="utf-8")
    _git(ctx.worktree_path, "add", "feature.txt")
    _git(
        ctx.worktree_path,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "commit",
        "-q",
        "-m",
        "Implement add-foo",
    )
    adapter = FakeHarnessAdapter(
        cfg, cwd=ctx.worktree_path, script=ScriptedCall(FakeOutcome.SUCCESS)
    )
    gate = FakeGate(ScriptedGateResult(passed=True))

    try:
        status = run_task(
            ctx=ctx,
            config=cfg,
            writer=writer,
            emitter=emitter,
            adapter=adapter,
            repo_path=repo,
            gate_runner=_gate_runner(gate),
            resume_at=TaskStatus.COMMITTING,
        )

        assert status is TaskStatus.DONE
        assert adapter.calls == []
        assert gate.calls == []
        transitions = [
            e.payload["to_state"]
            for e in reversed(list_events(cfg.paths.db_path, task_id=ctx.task_id, limit=200))
            if e.event_type == "task.state_changed"
        ]
        assert transitions == ["committing", "merging", "done", "finishing", "done"]
    finally:
        writer.close()


def test_proposing_is_skipped_when_the_worktree_already_has_a_complete_change(
    tmp_path: Path,
) -> None:
    """Found by hand against a real overnight run: a task requeued mid-run
    (a quota/wall-clock guard) reuses its worktree (`run.loop._run_one_task`'s
    own worktree-reuse branch) but `run_task` still called `_do_proposing`
    unconditionally -- a second real, billed harness call to re-author a
    change that was already fully proposed and hadn't changed. A worktree
    whose `openspec/changes/<spec_id>/tasks.md` already exists (simulating
    that reuse) must skip straight to `PROPOSED` without ever calling
    `adapter.propose`."""
    cfg, repo, writer, emitter, ctx = _setup(tmp_path)
    spec_id = Path(ctx.spec_path).stem
    change_dir = ctx.worktree_path / "openspec" / "changes" / spec_id
    change_dir.mkdir(parents=True)
    (change_dir / "tasks.md").write_text("- [x] 1.1 Already done\n", encoding="utf-8")

    adapter = FakeHarnessAdapter(
        cfg, cwd=ctx.worktree_path, script=ScriptedCall(FakeOutcome.SUCCESS)
    )
    gate = FakeGate(ScriptedGateResult(passed=True))

    try:
        status = run_task(
            ctx=ctx,
            config=cfg,
            writer=writer,
            emitter=emitter,
            adapter=adapter,
            repo_path=repo,
            gate_runner=_gate_runner(gate),
        )

        assert status is TaskStatus.DONE
        assert "propose" not in {call[0] for call in adapter.calls}

        transitions = [
            e.payload["to_state"]
            for e in reversed(list_events(cfg.paths.db_path, task_id=ctx.task_id, limit=200))
            if e.event_type == "task.state_changed"
        ]
        assert transitions[:2] == ["proposing", "proposed"]
    finally:
        writer.close()


def test_retry_exhaustion_blocks_with_code_failure(tmp_path: Path) -> None:
    cfg, repo, writer, emitter, ctx = _setup(tmp_path)
    adapter = FakeHarnessAdapter(
        cfg, cwd=ctx.worktree_path, script=ScriptedCall(FakeOutcome.SUCCESS)
    )
    gate = FakeGate(
        ScriptedGateResult(
            passed=False,
            failure_type=FailureType.CODE_ERROR,
            failure_stage=FailureStage.UNIT_TESTS,
            error_summary="unit test failed",
        )
    )

    try:
        status = run_task(
            ctx=ctx,
            config=cfg,
            writer=writer,
            emitter=emitter,
            adapter=adapter,
            repo_path=repo,
            gate_runner=_gate_runner(gate),
        )

        assert status is TaskStatus.BLOCKED
        task = get_task(cfg.paths.db_path, ctx.task_id)
        assert task is not None
        assert task.status == "blocked"
        assert task.blocked_reason == "code_failure"
        # spec 6.3: "third code-level failure -> BLOCKED" with the default
        # max_attempts=2 -- three judgments were consumed to get there.
        assert task.attempt_count == 3
    finally:
        writer.close()


def test_implementing_environment_error_does_not_consume_an_attempt(tmp_path: Path) -> None:
    cfg, repo, writer, emitter, ctx = _setup(tmp_path)
    adapter = FakeHarnessAdapter(
        cfg,
        cwd=ctx.worktree_path,
        script=[
            ScriptedCall(FakeOutcome.SUCCESS),  # propose
            ScriptedCall(FakeOutcome.ENVIRONMENT_FAILURE),  # implement attempt 1
            ScriptedCall(FakeOutcome.SUCCESS),  # implement attempt 2 (retry)
        ],
    )
    gate = FakeGate(ScriptedGateResult(passed=True))

    try:
        status = run_task(
            ctx=ctx,
            config=cfg,
            writer=writer,
            emitter=emitter,
            adapter=adapter,
            repo_path=repo,
            gate_runner=_gate_runner(gate),
        )

        assert status is TaskStatus.DONE
        task = get_task(cfg.paths.db_path, ctx.task_id)
        assert task is not None
        # Only the one successful cycle counted -- the environment_error
        # retry left attempt_count untouched.
        assert task.attempt_count == 1
    finally:
        writer.close()


def test_validating_environment_error_does_not_consume_an_attempt(tmp_path: Path) -> None:
    cfg, repo, writer, emitter, ctx = _setup(tmp_path)
    adapter = FakeHarnessAdapter(
        cfg, cwd=ctx.worktree_path, script=ScriptedCall(FakeOutcome.SUCCESS)
    )
    gate = FakeGate(
        [
            ScriptedGateResult(
                passed=False,
                failure_type=FailureType.ENVIRONMENT_ERROR,
                failure_stage=FailureStage.E2E_TESTS,
                error_summary="docker unresponsive",
            ),
            ScriptedGateResult(passed=True),
        ]
    )

    try:
        status = run_task(
            ctx=ctx,
            config=cfg,
            writer=writer,
            emitter=emitter,
            adapter=adapter,
            repo_path=repo,
            gate_runner=_gate_runner(gate),
        )

        assert status is TaskStatus.DONE
        task = get_task(cfg.paths.db_path, ctx.task_id)
        assert task is not None
        assert task.attempt_count == 1
    finally:
        writer.close()


def _repo_with_local_identity(tmp_path: Path) -> Path:
    repo = tmp_path / "decisions-log-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "user.name", "Local Dev"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "user.email", "local@example.com"],
        check=True,
        capture_output=True,
    )
    (repo / "docs").mkdir()
    (repo / "docs" / "decisions-log.md").write_text("# Decisions\n")
    return repo


def _decisions_log_commit_author(repo: Path) -> str:
    log = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%an <%ae>"],
        capture_output=True,
        text=True,
        check=True,
    )
    return log.stdout.strip()


def test_git_commit_decisions_log_uses_cosmo_identity_by_default(tmp_path: Path) -> None:
    repo = _repo_with_local_identity(tmp_path)
    cfg = _fast_config(tmp_path)

    _git_commit_decisions_log(repo, cfg)

    assert (
        _decisions_log_commit_author(repo)
        == f"{cfg.git.commit_author_name} <{cfg.git.commit_author_email}>"
    )


def test_git_commit_decisions_log_uses_local_identity_when_unified(tmp_path: Path) -> None:
    repo = _repo_with_local_identity(tmp_path)
    cfg = _fast_config(tmp_path)
    cfg = cfg.model_copy(update={"git": cfg.git.model_copy(update={"unified_identity": True})})

    _git_commit_decisions_log(repo, cfg)

    assert _decisions_log_commit_author(repo) == "Local Dev <local@example.com>"
