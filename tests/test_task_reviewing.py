"""`task.machine._do_reviewing`/`_do_finishing` (v4 workflow changes, see
`docs/v4-changes-to-workflow-plan.md`): the new `REVIEWING`/`FINISHING`
states inserted into the spec 3.2 task machine. Mirrors `test_task_machine.
py`'s own fixture style -- this is that file's natural sibling for the two
new states, kept separate so `test_task_machine.py` stays focused on the
original spec 3.2 sequence with review disabled (its own `_fast_config`
comment explains why).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

from cosmo.config import CosmoConfig, load_config
from cosmo.events import EventEmitter
from cosmo.gate.fake import FakeGate, ScriptedGateResult
from cosmo.gate.types import GateResult
from cosmo.git.worktree import create_worktree
from cosmo.harness.base import HarnessResult
from cosmo.harness.fake import FakeHarnessAdapter, FakeOutcome, ScriptedCall
from cosmo.store import StoreWriter
from cosmo.store.enums import FailureStage, TaskStatus
from cosmo.store.reader import get_task, list_events, list_task_failures
from cosmo.task.machine import run_task
from cosmo.task.review import review_result_path
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
    review = cfg.review.model_copy(update={"enabled": True})
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
    tmp_path: Path, task_id: str = "add-foo", max_attempts: int = 2
) -> tuple[CosmoConfig, Path, StoreWriter, EventEmitter, TaskContext]:
    cfg = _fast_config(tmp_path)
    repo = _repo_on_develop(tmp_path)
    writer = StoreWriter(cfg.paths.db_path)
    writer.queue_add(
        task_id=task_id, spec_path="openspec/changes/add-foo", max_attempts=max_attempts
    )
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
        max_attempts=max_attempts,
    )
    return cfg, repo, writer, emitter, ctx


def _gate_runner(gate: FakeGate) -> Callable[..., GateResult]:
    def _run(*, task_id: str, **_kwargs: object) -> GateResult:
        return gate.validate(task_id)

    return _run


def _write_verdict(worktree_path: Path, *, approved: bool, reason: str | None = None) -> None:
    path = review_result_path(worktree_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body: dict[str, str] = {"verdict": "approved" if approved else "rejected"}
    if reason is not None:
        body["reason"] = reason
    path.write_text(json.dumps(body), encoding="utf-8")


def test_approved_review_reaches_done_through_reviewing(tmp_path: Path) -> None:
    cfg, repo, writer, emitter, ctx = _setup(tmp_path)
    _write_verdict(ctx.worktree_path, approved=True)
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
            "reviewing",
            "committing",
            "merging",
            "done",
            "finishing",
            "done",
        ]
        # Exactly one adapter.review() call -- a fresh, separate invocation,
        # not folded into the implement() call.
        assert [c[0] for c in adapter.calls].count("review") == 1
    finally:
        writer.close()


def test_rejected_review_retries_then_a_second_approval_reaches_done(tmp_path: Path) -> None:
    cfg, repo, writer, emitter, ctx = _setup(tmp_path)
    adapter = FakeHarnessAdapter(
        cfg, cwd=ctx.worktree_path, script=ScriptedCall(FakeOutcome.SUCCESS)
    )
    gate = FakeGate(ScriptedGateResult(passed=True))

    # First reviewing pass: rejected (no verdict file written yet).
    _write_verdict(ctx.worktree_path, approved=False, reason="missing error handling")

    call_count = {"n": 0}
    real_review = adapter.review

    def scripted_review(task_id: str, spec_path: Path, base_branch: str) -> HarnessResult:
        call_count["n"] += 1
        if call_count["n"] == 2:
            _write_verdict(ctx.worktree_path, approved=True)
        return real_review(task_id, spec_path, base_branch)

    adapter.review = scripted_review  # type: ignore[method-assign]

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
        assert call_count["n"] == 2

        failures = list_task_failures(cfg.paths.db_path, ctx.task_id)
        rejected = [f for f in failures if f.failure_stage == FailureStage.ADVERSARIAL_REVIEW.value]
        assert len(rejected) == 1
        assert rejected[0].error_summary == "missing error handling"
        assert rejected[0].will_retry is True

        task = get_task(cfg.paths.db_path, ctx.task_id)
        assert task is not None
        # A rejected review retries through IMPLEMENTING again (same
        # `continue`-to-the-top-of-the-loop shape COMMITTING's own
        # knowledge-cap retry uses) -- the *second* cycle's own VALIDATING
        # pass is what actually spends the second code-level attempt, not
        # the rejection itself.
        assert task.attempt_count == 2
    finally:
        writer.close()


def test_review_rejected_at_final_attempt_blocks_with_code_failure(tmp_path: Path) -> None:
    cfg, repo, writer, emitter, ctx = _setup(tmp_path, max_attempts=1)
    _write_verdict(ctx.worktree_path, approved=False, reason="no tests for the new path")
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

        assert status is TaskStatus.BLOCKED
        task = get_task(cfg.paths.db_path, ctx.task_id)
        assert task is not None
        assert task.status == "blocked"
        assert task.blocked_reason == "code_failure"
    finally:
        writer.close()


def test_review_call_with_no_verdict_file_is_an_environment_retry_not_a_rejection(
    tmp_path: Path,
) -> None:
    """A review call that completed (`HarnessResult.success=True`) but wrote
    no verdict file at all is a broken contract, not a code judgment --
    `classify_harness_failure`'s own assert would fire if this were
    misrouted through it (the bug this test was written to catch)."""
    cfg, repo, writer, emitter, ctx = _setup(tmp_path)
    # No verdict file written at all.
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

        assert status is TaskStatus.BLOCKED
        task = get_task(cfg.paths.db_path, ctx.task_id)
        assert task is not None
        assert task.blocked_reason == "environment"

        failures = list_task_failures(cfg.paths.db_path, ctx.task_id)
        review_failures = [
            f for f in failures if f.failure_stage == FailureStage.ADVERSARIAL_REVIEW.value
        ]
        assert all(f.failure_type == "environment_error" for f in review_failures)
    finally:
        writer.close()


def test_finishing_never_blocks_a_task_when_archive_fails(tmp_path: Path) -> None:
    """`repo` here has no `openspec/` at all -- `openspec archive` fails,
    and the task must still reach `done` (the plan's own "best-effort,
    never blocking" decision)."""
    cfg, repo, writer, emitter, ctx = _setup(tmp_path)
    _write_verdict(ctx.worktree_path, approved=True)
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
        event_types = [
            e.event_type
            for e in reversed(list_events(cfg.paths.db_path, task_id=ctx.task_id, limit=200))
        ]
        assert "task.finishing_failed" in event_types
    finally:
        writer.close()
