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
from cosmo.task.machine import run_task
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
    return cfg.model_copy(update={"paths": paths, "retries": retries})


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
        assert transitions == [
            "proposing",
            "proposed",
            "implementing",
            "validating",
            "committing",
            "merging",
            "done",
        ]

        event_types = {
            e.event_type for e in list_events(cfg.paths.db_path, task_id=ctx.task_id, limit=200)
        }
        assert "task.validation_result" in event_types
        assert "task.completed" in event_types
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
