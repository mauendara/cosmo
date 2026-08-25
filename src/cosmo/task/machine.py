"""The spec 3.2 task state machine: `QUEUED -> PROPOSING -> PROPOSED ->
IMPLEMENTING -> VALIDATING -> COMMITTING -> MERGING -> DONE`, with
`FAILED_RETRY`/`BLOCKED`.

Design decisions recorded in `docs/v3-implementation-state.md`'s Phase 7
section (summarized at each relevant point below, not repeated in full):

- `attempt_count` (`task_queue.attempt_count`) is 0-indexed: it is *how
  many* code-level attempts have already been consumed, always peeked
  before a new one is judged and only incremented afterward if that attempt
  actually counted. `validate_task(attempt_number=attempt_count, ...)` is
  therefore called with the pre-increment value, and its own
  `will_retry = attempt_number < max_attempts` check and this module's
  identical re-derivation both give "the third code-level failure blocks"
  with the default `max_attempts=2` (spec 6.3's own wording) -- attempt 1
  fails at `attempt_count=0` (0<2, retry), attempt 2 fails at
  `attempt_count=1` (1<2, retry), attempt 3 fails at `attempt_count=2`
  (2<2 is false, BLOCKED). The increment (`queue_begin_attempt`) happens
  exactly once per attempt that is a genuine code-level judgment (a pass or
  a `code_error`/`test_integrity` verdict at `VALIDATING`, or a timeout at
  `IMPLEMENTING`) -- never for an `environment_error`, at either state,
  which is what makes spec 6.2's "does not count toward the task's retry
  limit" hold for real rather than just in the classifier's opinion.
- `PROPOSING` gets its own bounded "retry once locally, then BLOCKED"
  policy (spec 3.3) that never touches `attempt_count` at all -- there is
  no code yet at that point to attribute a `code_error` to.
- A `HarnessResult.success=False` from `propose()`/`implement()` that isn't
  a detected timeout is always `environment_error` (`task.classify`) --
  code-quality problems are only observable once the gate actually
  builds/tests the work.
- `environment_error`, wherever it originates (`IMPLEMENTING`'s own process
  failure or `VALIDATING`'s gate verdict), shares one bounded local retry
  counter (reusing `config.retries.max_attempts` as the bound) rather than
  retrying forever -- there is no circuit breaker yet (Phase 8) to stop an
  unbounded loop on a stuck environment.
- `VALIDATING`'s own wall/stall timeout (`config.timeouts.validating_wall`/
  `validating_stall`) is deliberately **not** wired to an external timer
  here: `gate.stage_timeout_seconds` (Phase 6) already bounds each of the
  gate's three stages and converts a stage timeout into
  `FailureType.ENVIRONMENT_ERROR` before `GateResult` ever reaches this
  module (`StageResult.timed_out`'s docstring) -- combined with this
  module's own "environment_error never increments attempt_count" rule,
  spec 3.3's "VALIDATING timeouts do not consume the code-level retry
  budget" already holds structurally. A second, outer wall-clock wrapper
  around `validate_task` was considered and rejected: `run_validation_gate`
  has no `cancel()` hook (unlike `HarnessAdapter`), so it could only abandon
  a background thread without stopping the Docker containers it started --
  a timeout that doesn't free what it claims to bound is worse than no
  timeout, so this is recorded as a deferred item instead of a fake one.
- `COMMITTING` never calls the harness -- `templates/harness/claude/
  CLAUDE.md` already instructs the agent to append knowledge notes and
  commit its own work as the last step of `IMPLEMENTING`. `COMMITTING` here
  only enforces the line cap on whatever `docs/**/*.md` files the task's
  own commits touched (`cosmo.knowledge`) and appends one Cosmo-authored,
  structured `decisions-log.md` entry. A knowledge-cap violation loops back
  to `IMPLEMENTING` (an informed retry, since it's a real, fixable defect in
  what the harness wrote) using the `attempt_count` already consumed by the
  `VALIDATING` pass that got it here -- it does not consume a second one on
  top, since it is not a new code judgment, just a deferred part of the
  same one.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from cosmo.config.model import CosmoConfig
from cosmo.events.emitter import EventEmitter
from cosmo.events.envelope import EventType
from cosmo.events.helpers import emit_state_changed
from cosmo.gate.runner import run_validation_gate
from cosmo.gate.validate import GateRunner, validate_task
from cosmo.git.merge import MergeCommandError, merge_task
from cosmo.harness.base import HarnessAdapter
from cosmo.knowledge.caps import docs_md_files, files_over_cap
from cosmo.knowledge.decisions_log import append_decision_entry
from cosmo.proc.timers import LivenessTimers
from cosmo.store.enums import (
    BlockedReason,
    FailureStage,
    FailureType,
    HeartbeatSource,
    NextAction,
    Severity,
    TaskStatus,
)
from cosmo.store.reader import get_task, list_task_failures
from cosmo.store.writer import StoreWriter
from cosmo.task.classify import classify_harness_failure
from cosmo.task.progress import ProgressWatcher, read_progress_from_file
from cosmo.task.retry import build_retry_context
from cosmo.task.timeouts import run_with_liveness_timeout, run_with_wall_clock_timeout
from cosmo.task.types import FailureClassification, TaskContext

_PROPOSING_MAX_LOCAL_ATTEMPTS = 2  # spec 3.3: "retry once, then BLOCKED"


class GitCommandError(RuntimeError):
    """A `git add`/`git commit` invocation in `COMMITTING` failed for a
    reason other than a knowledge-cap violation (which is `code_error`,
    handled separately) -- an environment problem."""


def run_task(
    *,
    ctx: TaskContext,
    config: CosmoConfig,
    writer: StoreWriter,
    emitter: EventEmitter,
    adapter: HarnessAdapter,
    repo_path: Path,
    gate_runner: GateRunner = run_validation_gate,
) -> TaskStatus:
    task_id = ctx.task_id
    run_id: str | None = None  # spec 3.2: no run-level tracking until Phase 8

    proposed = _do_proposing(
        ctx=ctx, config=config, writer=writer, emitter=emitter, adapter=adapter, run_id=run_id
    )
    if proposed is not TaskStatus.PROPOSED:
        return proposed
    emit_state_changed(emitter, writer.queue_transition(task_id, TaskStatus.PROPOSED))

    task_row = get_task(config.paths.db_path, task_id)
    attempt_count = task_row.attempt_count if task_row is not None else 0
    validating_env_retries = 0

    while True:
        # -- IMPLEMENTING -----------------------------------------------
        emit_state_changed(emitter, writer.queue_transition(task_id, TaskStatus.IMPLEMENTING))
        implemented = _do_implementing(
            ctx=ctx, config=config, writer=writer, emitter=emitter, adapter=adapter, run_id=run_id
        )

        if not implemented.success:
            assert implemented.classification is not None
            if implemented.timed_out:
                will_retry = attempt_count < ctx.max_attempts
                attempt_count = writer.queue_begin_attempt(task_id)
                _record_failure(
                    writer, task_id, run_id, attempt_count, implemented.classification, will_retry
                )
                if not will_retry:
                    return _block(
                        writer=writer,
                        emitter=emitter,
                        task_id=task_id,
                        reason=BlockedReason.TIMEOUT,
                        note=implemented.classification.error_summary,
                    )
            else:
                validating_env_retries += 1
                blocking = validating_env_retries > config.retries.max_attempts
                _record_failure(
                    writer,
                    task_id,
                    run_id,
                    attempt_count,
                    implemented.classification,
                    will_retry=not blocking,
                )
                if blocking:
                    return _block(
                        writer=writer,
                        emitter=emitter,
                        task_id=task_id,
                        reason=BlockedReason.ENVIRONMENT,
                        note=implemented.classification.error_summary,
                    )
            emit_state_changed(emitter, writer.queue_transition(task_id, TaskStatus.FAILED_RETRY))
            _retry_delay(config)
            continue

        # -- VALIDATING ---------------------------------------------------
        emit_state_changed(emitter, writer.queue_transition(task_id, TaskStatus.VALIDATING))
        gate_result = validate_task(
            task_id=task_id,
            run_id=run_id,
            attempt_number=attempt_count,
            max_attempts=ctx.max_attempts,
            worktree_path=ctx.worktree_path,
            base_branch=ctx.base_branch,
            task_branch=ctx.branch,
            allow_test_edits=ctx.allow_test_edits,
            config=config,
            writer=writer,
            emitter=emitter,
            gate_runner=gate_runner,
        )

        if not gate_result.passed and gate_result.failure_type is FailureType.ENVIRONMENT_ERROR:
            validating_env_retries += 1
            if validating_env_retries > config.retries.max_attempts:
                return _block(
                    writer=writer,
                    emitter=emitter,
                    task_id=task_id,
                    reason=BlockedReason.ENVIRONMENT,
                    note=gate_result.error_summary,
                )
            emit_state_changed(emitter, writer.queue_transition(task_id, TaskStatus.FAILED_RETRY))
            _retry_delay(config)
            continue

        # A genuine code-level judgment happened (pass, or code_error /
        # test_integrity) -- this consumes one attempt, pass or fail.
        will_retry = attempt_count < ctx.max_attempts
        attempt_count = writer.queue_begin_attempt(task_id)

        if not gate_result.passed:
            if not will_retry:
                return _block(
                    writer=writer,
                    emitter=emitter,
                    task_id=task_id,
                    reason=BlockedReason.CODE_FAILURE,
                    note=gate_result.error_summary,
                )
            emit_state_changed(emitter, writer.queue_transition(task_id, TaskStatus.FAILED_RETRY))
            _retry_delay(config)
            continue

        # -- COMMITTING -----------------------------------------------------
        committing = _do_committing(
            ctx=ctx,
            config=config,
            writer=writer,
            emitter=emitter,
            run_id=run_id,
            attempt_count=attempt_count,
        )
        if committing is _CommitOutcome.RETRY:
            emit_state_changed(emitter, writer.queue_transition(task_id, TaskStatus.FAILED_RETRY))
            _retry_delay(config)
            continue
        if committing is _CommitOutcome.BLOCKED:
            return TaskStatus.BLOCKED
        break  # _CommitOutcome.DONE

    # -- MERGING ------------------------------------------------------------
    emit_state_changed(emitter, writer.queue_transition(task_id, TaskStatus.MERGING))
    return _do_merging(
        ctx=ctx,
        config=config,
        writer=writer,
        emitter=emitter,
        repo_path=repo_path,
        run_id=run_id,
        gate_runner=gate_runner,
    )


def _record_failure(
    writer: StoreWriter,
    task_id: str,
    run_id: str | None,
    attempt_number: int,
    classification: FailureClassification,
    will_retry: bool,
) -> None:
    writer.record_task_failure(
        task_id=task_id,
        run_id=run_id,
        attempt_number=attempt_number,
        failure_type=classification.failure_type,
        failure_stage=classification.failure_stage,
        error_summary=classification.error_summary,
        error_detail=classification.error_detail,
        files_touched=[],
        will_retry=will_retry,
        next_action=NextAction.RETRY if will_retry else NextAction.BLOCK,
    )


# -- PROPOSING ----------------------------------------------------------------


def _do_proposing(
    *,
    ctx: TaskContext,
    config: CosmoConfig,
    writer: StoreWriter,
    emitter: EventEmitter,
    adapter: HarnessAdapter,
    run_id: str | None,
) -> TaskStatus:
    task_id = ctx.task_id
    emit_state_changed(emitter, writer.queue_transition(task_id, TaskStatus.PROPOSING))

    for local_attempt in range(1, _PROPOSING_MAX_LOCAL_ATTEMPTS + 1):
        result = run_with_wall_clock_timeout(
            lambda: adapter.propose(Path(ctx.spec_path), {"task_id": task_id}),
            wall_s=float(config.timeouts.proposing_wall),
            cancel=lambda: adapter.cancel(task_id),
            kill_grace_s=float(config.timeouts.kill_grace),
        )
        if result.value is not None and result.value.success:
            return TaskStatus.PROPOSED

        classification = classify_harness_failure(
            result.value, stage=FailureStage.PROPOSE, timed_out=result.timed_out
        )
        will_retry = local_attempt < _PROPOSING_MAX_LOCAL_ATTEMPTS
        _record_failure(writer, task_id, run_id, local_attempt, classification, will_retry)
        if not will_retry:
            reason = (
                BlockedReason.TIMEOUT
                if classification.failure_type is FailureType.TIMEOUT
                else BlockedReason.ENVIRONMENT
            )
            return _block(
                writer=writer,
                emitter=emitter,
                task_id=task_id,
                reason=reason,
                note=classification.error_summary,
            )
        emit_state_changed(emitter, writer.queue_transition(task_id, TaskStatus.FAILED_RETRY))
        _retry_delay(config)
        emit_state_changed(emitter, writer.queue_transition(task_id, TaskStatus.PROPOSING))

    raise AssertionError("unreachable: the loop above always returns or blocks")


# -- IMPLEMENTING ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ImplementOutcome:
    success: bool
    timed_out: bool
    classification: FailureClassification | None


def _do_implementing(
    *,
    ctx: TaskContext,
    config: CosmoConfig,
    writer: StoreWriter,
    emitter: EventEmitter,
    adapter: HarnessAdapter,
    run_id: str | None,
) -> _ImplementOutcome:
    """Runs one `implement()` attempt under the wall/stall timeout and
    returns its outcome -- deliberately no side effects on `attempt_count`
    or `task_failures` here; `run_task`'s main loop owns both, since only it
    knows whether this attempt is a genuine code-level judgment (see this
    module's docstring)."""
    task_id = ctx.task_id
    failures = [
        f
        for f in list_task_failures(config.paths.db_path, task_id)
        if f.failure_stage != FailureStage.PROPOSE.value
    ]
    retry_context = build_retry_context(failures)

    tasks_md_path = ctx.worktree_path / ctx.spec_path / "tasks.md"
    watch_path: Path | None
    if adapter.capabilities.reports_native_progress:

        def read_progress() -> tuple[int, int, str | None]:
            completed, total = adapter.get_progress(task_id)
            return completed, total, None

        watch_path = None
    else:

        def read_progress() -> tuple[int, int, str | None]:
            return read_progress_from_file(tasks_md_path)

        watch_path = tasks_md_path

    timers = LivenessTimers(
        wall_s=float(config.timeouts.implementing_wall),
        stall_s=float(config.timeouts.implementing_stall),
    )
    watcher = ProgressWatcher(
        task_id=task_id,
        run_id=run_id,
        state=TaskStatus.IMPLEMENTING.value,
        writer=writer,
        emitter=emitter,
        read_progress=read_progress,
        tasks_md_path=watch_path,
        timers=timers,
    )

    def _on_tick() -> None:
        writer.drain()
        watcher.check(HeartbeatSource.MTIME)

    watcher.start()
    try:
        timeout_result = run_with_liveness_timeout(
            lambda: adapter.implement(task_id, Path(ctx.spec_path), retry_context),
            timers=timers,
            wall_s=float(config.timeouts.implementing_wall),
            cancel=lambda: adapter.cancel(task_id),
            kill_grace_s=float(config.timeouts.kill_grace),
            on_tick=_on_tick,
        )
    finally:
        watcher.stop()
        writer.drain()

    if timeout_result.value is not None and timeout_result.value.success:
        return _ImplementOutcome(success=True, timed_out=False, classification=None)

    classification = classify_harness_failure(
        timeout_result.value, stage=FailureStage.IMPLEMENT, timed_out=timeout_result.timed_out
    )
    return _ImplementOutcome(
        success=False, timed_out=timeout_result.timed_out, classification=classification
    )


# -- COMMITTING -----------------------------------------------------------------


class _CommitOutcome:
    DONE = "done"
    RETRY = "retry"
    BLOCKED = "blocked"


def _do_committing(
    *,
    ctx: TaskContext,
    config: CosmoConfig,
    writer: StoreWriter,
    emitter: EventEmitter,
    run_id: str | None,
    attempt_count: int,
) -> str:
    task_id = ctx.task_id
    emit_state_changed(emitter, writer.queue_transition(task_id, TaskStatus.COMMITTING))

    touched = docs_md_files(ctx.worktree_path, ctx.base_branch, ctx.branch)
    over_cap = files_over_cap(ctx.worktree_path, touched, config.knowledge.max_file_lines)

    if over_cap:
        will_retry = attempt_count < ctx.max_attempts
        error_summary = (
            f"knowledge file(s) over the {config.knowledge.max_file_lines}-line cap: "
            f"{', '.join(over_cap)}"
        )
        writer.record_task_failure(
            task_id=task_id,
            run_id=run_id,
            attempt_number=attempt_count,
            failure_type=FailureType.CODE_ERROR,
            failure_stage=FailureStage.COMMIT,
            error_summary=error_summary,
            error_detail=None,
            files_touched=over_cap,
            will_retry=will_retry,
            next_action=NextAction.RETRY if will_retry else NextAction.BLOCK,
        )
        if not will_retry:
            _block(
                writer=writer,
                emitter=emitter,
                task_id=task_id,
                reason=BlockedReason.CODE_FAILURE,
                note=error_summary,
            )
            return _CommitOutcome.BLOCKED
        return _CommitOutcome.RETRY

    append_decision_entry(ctx.worktree_path, task_id=task_id, spec_path=ctx.spec_path)
    try:
        _git_commit_decisions_log(ctx.worktree_path, config)
    except GitCommandError as exc:
        writer.record_task_failure(
            task_id=task_id,
            run_id=run_id,
            attempt_number=attempt_count,
            failure_type=FailureType.ENVIRONMENT_ERROR,
            failure_stage=FailureStage.COMMIT,
            error_summary=str(exc),
            error_detail=None,
            files_touched=[],
            will_retry=False,
            next_action=NextAction.BLOCK,
        )
        _block(
            writer=writer,
            emitter=emitter,
            task_id=task_id,
            reason=BlockedReason.ENVIRONMENT,
            note=str(exc),
        )
        return _CommitOutcome.BLOCKED

    return _CommitOutcome.DONE


def _git_commit_decisions_log(worktree_path: Path, config: CosmoConfig) -> None:
    name, email = config.git.commit_author_name, config.git.commit_author_email
    try:
        subprocess.run(
            ["git", "-C", str(worktree_path), "add", "docs/decisions-log.md"],
            check=True,
            capture_output=True,
            text=True,
            timeout=60.0,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(worktree_path),
                "-c",
                f"user.name={name}",
                "-c",
                f"user.email={email}",
                "commit",
                "-m",
                "cosmo: record decision log entry",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60.0,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        raise GitCommandError(f"could not commit decisions-log.md: {exc}") from exc


# -- MERGING ----------------------------------------------------------------


def _do_merging(
    *,
    ctx: TaskContext,
    config: CosmoConfig,
    writer: StoreWriter,
    emitter: EventEmitter,
    repo_path: Path,
    run_id: str | None,
    gate_runner: GateRunner,
) -> TaskStatus:
    def gate_rerun() -> bool:
        return gate_runner(
            task_id=ctx.task_id,
            run_id=run_id,
            worktree_path=ctx.worktree_path,
            base_branch=ctx.base_branch,
            task_branch=ctx.branch,
            allow_test_edits=ctx.allow_test_edits,
            gate=config.gate,
            db_path=config.paths.db_path,
        ).passed

    author = (config.git.commit_author_name, config.git.commit_author_email)
    try:
        merge_result = merge_task(
            repo_path=repo_path,
            worktree_path=ctx.worktree_path,
            branch=ctx.branch,
            base_branch=ctx.base_branch,
            task_id=ctx.task_id,
            run_id=run_id,
            writer=writer,
            emitter=emitter,
            gate_rerun=gate_rerun,
            author=author,
        )
    except MergeCommandError as exc:
        writer.record_task_failure(
            task_id=ctx.task_id,
            run_id=run_id,
            attempt_number=0,
            failure_type=FailureType.ENVIRONMENT_ERROR,
            failure_stage=FailureStage.MERGE,
            error_summary=str(exc),
            error_detail=None,
            files_touched=[],
            will_retry=False,
            next_action=NextAction.BLOCK,
        )
        return _block(
            writer=writer,
            emitter=emitter,
            task_id=ctx.task_id,
            reason=BlockedReason.ENVIRONMENT,
            note=str(exc),
        )

    return TaskStatus.DONE if merge_result.outcome.merged else TaskStatus.BLOCKED


# -- shared helpers -----------------------------------------------------------


def _block(
    *,
    writer: StoreWriter,
    emitter: EventEmitter,
    task_id: str,
    reason: BlockedReason,
    note: str | None,
) -> TaskStatus:
    transition = writer.queue_block(task_id, reason, note=note)
    emitter.emit(
        event_type=EventType.TASK_BLOCKED,
        severity=Severity.WARNING,
        task_id=task_id,
        payload={"blocked_reason": reason.value, "note": note},
    )
    emit_state_changed(emitter, transition)
    return TaskStatus.BLOCKED


def _retry_delay(config: CosmoConfig) -> None:
    time.sleep(config.retries.delay_min)
