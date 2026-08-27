"""The spec 3.2 task state machine: `QUEUED -> PROPOSING -> PROPOSED ->
IMPLEMENTING -> VALIDATING -> REVIEWING -> COMMITTING -> MERGING ->
FINISHING -> DONE`, with `FAILED_RETRY`/`BLOCKED`. `REVIEWING`/`FINISHING`
are v4 workflow-changes additions (`docs/v4-changes-to-workflow-plan.md`),
layered onto the original spec 3.2 sequence -- see this module's own
`_do_reviewing`/`_do_finishing` sections below for their docstrings.

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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cosmo.bootstrap.openspec import OpenSpecInitError, archive_change
from cosmo.config.model import CosmoConfig
from cosmo.events.emitter import EventEmitter
from cosmo.events.envelope import EventType
from cosmo.events.helpers import emit_state_changed
from cosmo.gate.runner import run_validation_gate
from cosmo.gate.validate import GateRunner, validate_task
from cosmo.git.merge import MergeCommandError, merge_task
from cosmo.harness.base import HarnessAdapter, HarnessResult
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
from cosmo.task.review import read_review_verdict
from cosmo.task.timeouts import run_with_liveness_timeout, run_with_wall_clock_timeout
from cosmo.task.types import FailureClassification, RunGuardAction, TaskContext

OnHarnessResult = Callable[[HarnessResult], None]
CheckRunGuard = Callable[[], RunGuardAction | None]
OnActivity = Callable[[str], None]
"""Item 3's live-activity hook: one short human-readable line per notable
live event during a harness call (a tool call, session start) -- purely
display, threaded straight through to `HarnessAdapter.propose`/`implement`/
`review`'s own `on_activity` param, never consulted for any retry/
classification decision this module makes."""

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
    run_id: str | None = None,
    on_harness_result: OnHarnessResult | None = None,
    check_run_guard: CheckRunGuard | None = None,
    on_activity: OnActivity | None = None,
    resume_at: TaskStatus = TaskStatus.IMPLEMENTING,
) -> TaskStatus:
    """`run_id` defaults to `None`, preserving Phase 7's "no run tracking"
    posture for any caller that doesn't have one -- `cosmo run --task`
    (single-task CLI) still passes `None`. Phase 8's run loop is the first
    caller to pass a real value.

    `on_harness_result`/`check_run_guard` are Phase 8's cost/quota/run-wall-
    clock seam (see `task.types.RunGuardAction`'s docstring): purely
    additive hooks that never change this function's own retry/
    classification logic when unset (both default to `None`). `on_harness_
    result` observes every raw `HarnessResult` from `propose()`/
    `implement()` as it happens; `check_run_guard` is polled once before
    each new `PROPOSING`/`IMPLEMENTING` attempt starts.

    `resume_at` (v6, `store.writer.queue_resume_at`): `COMMITTING` or
    `MERGING` skip straight there -- `PROPOSING` and every stage before
    `resume_at` already succeeded in an earlier `cosmo run` process, on this
    same worktree, and are not redone. Safe specifically because
    `COMMITTING`/`MERGING` are the only two stages whose own
    `environment_error` never gets an in-run retry at all (`_do_committing`/
    `_do_merging` below always `will_retry=False`) -- unlike every earlier
    stage, there is no "the code needs to change" judgment bundled into that
    failure, so nothing before it needs to be redone either. `cli.main.
    queue_retry` is the only real caller of anything but the default."""
    task_id = ctx.task_id

    if resume_at is TaskStatus.IMPLEMENTING:
        proposed = _do_proposing(
            ctx=ctx,
            config=config,
            writer=writer,
            emitter=emitter,
            adapter=adapter,
            run_id=run_id,
            on_harness_result=on_harness_result,
            check_run_guard=check_run_guard,
            on_activity=on_activity,
        )
        if proposed is not TaskStatus.PROPOSED:
            # BLOCKED (an ordinary PROPOSING failure) or QUEUED (`check_run_
            # guard` fired REQUEUE before/during PROPOSING) -- either way this
            # task's run is over for now.
            return proposed
        emit_state_changed(
            emitter, writer.queue_transition(task_id, TaskStatus.PROPOSED, run_id=run_id)
        )

    task_row = get_task(config.paths.db_path, task_id)
    attempt_count = task_row.attempt_count if task_row is not None else 0
    validating_env_retries = 0

    if resume_at is not TaskStatus.MERGING:
        skip_to_committing = resume_at is TaskStatus.COMMITTING
        while True:
            if not skip_to_committing:
                if check_run_guard is not None:
                    guard_action = check_run_guard()
                    if guard_action is RunGuardAction.REQUEUE:
                        return _requeue(
                            writer=writer, emitter=emitter, task_id=task_id, run_id=run_id
                        )
                    if guard_action is RunGuardAction.BLOCK_COST:
                        return _block(
                            writer=writer,
                            emitter=emitter,
                            task_id=task_id,
                            run_id=run_id,
                            reason=BlockedReason.COST,
                            note="task cost ceiling reached (spec 7.3)",
                        )

                # -- IMPLEMENTING -----------------------------------------------
                emit_state_changed(
                    emitter,
                    writer.queue_transition(task_id, TaskStatus.IMPLEMENTING, run_id=run_id),
                )
                implemented = _do_implementing(
                    ctx=ctx,
                    config=config,
                    writer=writer,
                    emitter=emitter,
                    adapter=adapter,
                    run_id=run_id,
                    on_harness_result=on_harness_result,
                    on_activity=on_activity,
                )

                if not implemented.success:
                    assert implemented.classification is not None
                    if implemented.timed_out:
                        will_retry = attempt_count < ctx.max_attempts
                        attempt_count = writer.queue_begin_attempt(task_id)
                        _record_failure(
                            writer,
                            task_id,
                            run_id,
                            attempt_count,
                            implemented.classification,
                            will_retry,
                        )
                        if not will_retry:
                            return _block(
                                writer=writer,
                                emitter=emitter,
                                task_id=task_id,
                                run_id=run_id,
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
                                run_id=run_id,
                                reason=BlockedReason.ENVIRONMENT,
                                note=implemented.classification.error_summary,
                            )
                    emit_state_changed(
                        emitter,
                        writer.queue_transition(task_id, TaskStatus.FAILED_RETRY, run_id=run_id),
                    )
                    _retry_delay(config)
                    continue

                # -- VALIDATING ---------------------------------------------------
                emit_state_changed(
                    emitter,
                    writer.queue_transition(task_id, TaskStatus.VALIDATING, run_id=run_id),
                )
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

                if (
                    not gate_result.passed
                    and gate_result.failure_type is FailureType.ENVIRONMENT_ERROR
                ):
                    validating_env_retries += 1
                    if validating_env_retries > config.retries.max_attempts:
                        return _block(
                            writer=writer,
                            emitter=emitter,
                            task_id=task_id,
                            run_id=run_id,
                            reason=BlockedReason.ENVIRONMENT,
                            note=gate_result.error_summary,
                        )
                    emit_state_changed(
                        emitter,
                        writer.queue_transition(task_id, TaskStatus.FAILED_RETRY, run_id=run_id),
                    )
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
                            run_id=run_id,
                            reason=BlockedReason.CODE_FAILURE,
                            note=gate_result.error_summary,
                        )
                    emit_state_changed(
                        emitter,
                        writer.queue_transition(task_id, TaskStatus.FAILED_RETRY, run_id=run_id),
                    )
                    _retry_delay(config)
                    continue

                # -- REVIEWING (v4 workflow changes) -------------------------------
                if config.review.enabled:
                    review_step = _do_reviewing(
                        ctx=ctx,
                        config=config,
                        writer=writer,
                        emitter=emitter,
                        adapter=adapter,
                        run_id=run_id,
                        on_harness_result=on_harness_result,
                        on_activity=on_activity,
                        attempt_count=attempt_count,
                        will_retry=will_retry,
                        validating_env_retries=validating_env_retries,
                    )
                    validating_env_retries = review_step.validating_env_retries
                    if review_step.outcome is _ReviewOutcome.RETRY:
                        emit_state_changed(
                            emitter,
                            writer.queue_transition(
                                task_id, TaskStatus.FAILED_RETRY, run_id=run_id
                            ),
                        )
                        _retry_delay(config)
                        continue
                    if review_step.outcome is _ReviewOutcome.BLOCKED:
                        return TaskStatus.BLOCKED

            skip_to_committing = False

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
                emit_state_changed(
                    emitter,
                    writer.queue_transition(task_id, TaskStatus.FAILED_RETRY, run_id=run_id),
                )
                _retry_delay(config)
                continue
            if committing is _CommitOutcome.BLOCKED:
                return TaskStatus.BLOCKED
            break  # _CommitOutcome.DONE

    # -- MERGING ------------------------------------------------------------
    emit_state_changed(emitter, writer.queue_transition(task_id, TaskStatus.MERGING, run_id=run_id))
    merged_status = _do_merging(
        ctx=ctx,
        config=config,
        writer=writer,
        emitter=emitter,
        repo_path=repo_path,
        run_id=run_id,
        gate_runner=gate_runner,
    )
    if merged_status is TaskStatus.DONE:
        # -- FINISHING (v4 workflow changes) --------------------------------
        # `merge_task` (called by `_do_merging` above) already set the task
        # `done` and emitted `task.completed`/`task.state_changed` itself
        # (spec 3.2's own merge-success path, unchanged) -- FINISHING is a
        # deliberately best-effort step layered *after* that real
        # completion, not a precondition for it. The task_transitions trail
        # therefore genuinely reads `..., merging, done, finishing, done`:
        # honest about a task that fully completed at MERGING, with an
        # optional archive step recorded afterward rather than gating on it.
        emit_state_changed(
            emitter, writer.queue_transition(task_id, TaskStatus.FINISHING, run_id=run_id)
        )
        _do_finishing(ctx=ctx, repo_path=repo_path, config=config, emitter=emitter, run_id=run_id)
        emit_state_changed(
            emitter, writer.queue_transition(task_id, TaskStatus.DONE, run_id=run_id)
        )
    return merged_status


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
    on_harness_result: OnHarnessResult | None,
    check_run_guard: CheckRunGuard | None,
    on_activity: OnActivity | None,
) -> TaskStatus:
    task_id = ctx.task_id
    emit_state_changed(
        emitter, writer.queue_transition(task_id, TaskStatus.PROPOSING, run_id=run_id)
    )

    spec_id = Path(ctx.spec_path).stem
    tasks_md = ctx.worktree_path / "openspec" / "changes" / spec_id / "tasks.md"
    if tasks_md.is_file() and tasks_md.stat().st_size > 0:
        # A worktree reused within this same run (a requeue -- see
        # `run.loop._run_one_task`'s worktree-reuse branch, e.g. after a
        # quota/wall-clock guard sent this task back to `QUEUED` mid-run)
        # already has a complete OpenSpec change from an earlier PROPOSING
        # pass in this exact attempt. `tasks.md` is the last artifact
        # `propose()`'s own workflow produces (proposal -> design -> specs
        # -> tasks, per `skills/openspec-workflow/SKILL.md`), so its
        # presence means PROPOSING already finished here -- re-running it
        # would be a second real, billed harness call for work that's
        # already done. A genuinely fresh worktree (a real retry, or a new
        # run) never has this file -- it's a brand-new checkout of the
        # target repo's current `docs/`/spec content -- so this only ever
        # skips the reused-worktree case, never a real first attempt.
        if on_activity is not None:
            on_activity(f"proposing: skipped -- {spec_id} already proposed in this worktree")
        return TaskStatus.PROPOSED

    for local_attempt in range(1, _PROPOSING_MAX_LOCAL_ATTEMPTS + 1):
        if check_run_guard is not None:
            guard_action = check_run_guard()
            if guard_action is RunGuardAction.REQUEUE:
                return _requeue(writer=writer, emitter=emitter, task_id=task_id, run_id=run_id)
            if guard_action is RunGuardAction.BLOCK_COST:
                return _block(
                    writer=writer,
                    emitter=emitter,
                    task_id=task_id,
                    run_id=run_id,
                    reason=BlockedReason.COST,
                    note="task cost ceiling reached (spec 7.3)",
                )

        result = run_with_wall_clock_timeout(
            lambda: adapter.propose(
                Path(ctx.spec_path), {"task_id": task_id}, on_activity=on_activity
            ),
            wall_s=float(config.timeouts.proposing_wall),
            cancel=lambda: adapter.cancel(task_id),
            kill_grace_s=float(config.timeouts.kill_grace),
        )
        if result.value is not None and on_harness_result is not None:
            on_harness_result(result.value)
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
                run_id=run_id,
                reason=reason,
                note=classification.error_summary,
            )
        emit_state_changed(
            emitter, writer.queue_transition(task_id, TaskStatus.FAILED_RETRY, run_id=run_id)
        )
        _retry_delay(config)
        emit_state_changed(
            emitter, writer.queue_transition(task_id, TaskStatus.PROPOSING, run_id=run_id)
        )

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
    on_harness_result: OnHarnessResult | None,
    on_activity: OnActivity | None,
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

    # `ctx.spec_path` is not always the OpenSpec change directory itself --
    # true for the direct `queue add` front door (spec_path is literally
    # `openspec/changes/<name>`), but for a v4 spec-queued task it's the
    # raw `*-task.md` file PROPOSING read to create the change. The change
    # directory's actual name is `Path(spec_path).stem` either way -- same
    # derivation `_do_finishing` already uses for `openspec archive` -- so
    # locate `tasks.md` there rather than under `spec_path` directly.
    spec_id = Path(ctx.spec_path).stem
    tasks_md_path = ctx.worktree_path / "openspec" / "changes" / spec_id / "tasks.md"
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
            lambda: adapter.implement(
                task_id, Path(ctx.spec_path), retry_context, on_activity=on_activity
            ),
            timers=timers,
            wall_s=float(config.timeouts.implementing_wall),
            cancel=lambda: adapter.cancel(task_id),
            kill_grace_s=float(config.timeouts.kill_grace),
            on_tick=_on_tick,
        )
    finally:
        watcher.stop()
        writer.drain()

    if timeout_result.value is not None and on_harness_result is not None:
        on_harness_result(timeout_result.value)

    if timeout_result.value is not None and timeout_result.value.success:
        return _ImplementOutcome(success=True, timed_out=False, classification=None)

    classification = classify_harness_failure(
        timeout_result.value, stage=FailureStage.IMPLEMENT, timed_out=timeout_result.timed_out
    )
    return _ImplementOutcome(
        success=False, timed_out=timeout_result.timed_out, classification=classification
    )


# -- REVIEWING (v4 workflow changes) ---------------------------------------


class _ReviewOutcome:
    APPROVED = "approved"
    RETRY = "retry"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class _ReviewStepResult:
    outcome: str
    validating_env_retries: int
    """Handed back so `run_task` can keep threading it into a later
    `VALIDATING` cycle -- see the parameter's own docstring below."""


def _do_reviewing(
    *,
    ctx: TaskContext,
    config: CosmoConfig,
    writer: StoreWriter,
    emitter: EventEmitter,
    adapter: HarnessAdapter,
    run_id: str | None,
    on_harness_result: OnHarnessResult | None,
    on_activity: OnActivity | None,
    attempt_count: int,
    will_retry: bool,
    validating_env_retries: int,
) -> _ReviewStepResult:
    """v4 workflow changes: a fresh, session-less adversarial review, run
    once `VALIDATING`'s gate has confirmed `gate_result.passed` -- see
    `HarnessAdapter.review`'s docstring for why the call itself carries no
    memory of the implementation session.

    Two independent budgets, matching this module's own "environment_error
    never consumes the code-level retry budget" discipline (see the module
    docstring):

    - A **rejected review** is a genuine code-level judgment, exactly like a
      gate `code_error`/`test_integrity` verdict -- it reuses the
      `attempt_count`/`will_retry` judgment `run_task`'s own `VALIDATING`
      step already computed for this cycle (passed in rather than
      recomputed, the same "no new ceiling on top of the judgment already
      made" pattern `_do_committing`'s knowledge-cap check uses), and blocks
      with `BlockedReason.CODE_FAILURE` when that budget is spent.
    - A review call that itself never produced a usable verdict (crashed,
      timed out, or wrote no/malformed file) is an environment problem with
      the call, not a judgment about the code -- it shares `VALIDATING`'s
      own `validating_env_retries` counter (threaded in and back out via
      `_ReviewStepResult`, since both states are "post-implementation
      environment reliability" problems in the same sense), and blocks with
      `BlockedReason.ENVIRONMENT`/`TIMEOUT` instead, never touching
      `attempt_count` at all.
    """
    task_id = ctx.task_id
    emit_state_changed(
        emitter, writer.queue_transition(task_id, TaskStatus.REVIEWING, run_id=run_id)
    )

    timeout_result = run_with_wall_clock_timeout(
        lambda: adapter.review(
            task_id, Path(ctx.spec_path), ctx.base_branch, on_activity=on_activity
        ),
        wall_s=float(config.timeouts.reviewing_wall),
        cancel=lambda: adapter.cancel(task_id),
        kill_grace_s=float(config.timeouts.kill_grace),
    )
    result = timeout_result.value
    if result is not None and on_harness_result is not None:
        on_harness_result(result)

    verdict = None
    if result is not None and result.success:
        verdict = read_review_verdict(ctx.worktree_path)

    if verdict is not None and verdict.approved:
        return _ReviewStepResult(_ReviewOutcome.APPROVED, validating_env_retries)

    if verdict is not None and not verdict.approved:
        classification = FailureClassification(
            failure_type=FailureType.CODE_ERROR,
            failure_stage=FailureStage.ADVERSARIAL_REVIEW,
            error_summary=verdict.reason or "adversarial review rejected the diff",
            error_detail=None,
        )
        _record_failure(writer, task_id, run_id, attempt_count, classification, will_retry)
        if not will_retry:
            _block(
                writer=writer,
                emitter=emitter,
                task_id=task_id,
                run_id=run_id,
                reason=BlockedReason.CODE_FAILURE,
                note=classification.error_summary,
            )
            return _ReviewStepResult(_ReviewOutcome.BLOCKED, validating_env_retries)
        return _ReviewStepResult(_ReviewOutcome.RETRY, validating_env_retries)

    # No usable verdict at all -- an environment problem with the review
    # call, bounded the same way VALIDATING's own environment_error is.
    if result is not None and result.success:
        # The call itself completed, but wrote no (or a malformed) verdict
        # file -- a broken contract, not a crash. `classify_harness_failure`
        # assumes `result.success is False` (it exists for a *failed*
        # propose/implement call), so this case is built by hand.
        classification = FailureClassification(
            failure_type=FailureType.ENVIRONMENT_ERROR,
            failure_stage=FailureStage.ADVERSARIAL_REVIEW,
            error_summary="review call completed but produced no usable verdict",
            error_detail=None,
        )
    else:
        classification = classify_harness_failure(
            result, stage=FailureStage.ADVERSARIAL_REVIEW, timed_out=timeout_result.timed_out
        )

    validating_env_retries += 1
    blocking = validating_env_retries > config.retries.max_attempts
    _record_failure(writer, task_id, run_id, attempt_count, classification, will_retry=not blocking)
    if blocking:
        reason = (
            BlockedReason.TIMEOUT
            if classification.failure_type is FailureType.TIMEOUT
            else BlockedReason.ENVIRONMENT
        )
        _block(
            writer=writer,
            emitter=emitter,
            task_id=task_id,
            run_id=run_id,
            reason=reason,
            note=classification.error_summary,
        )
        return _ReviewStepResult(_ReviewOutcome.BLOCKED, validating_env_retries)
    return _ReviewStepResult(_ReviewOutcome.RETRY, validating_env_retries)


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
    emit_state_changed(
        emitter, writer.queue_transition(task_id, TaskStatus.COMMITTING, run_id=run_id)
    )

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
                run_id=run_id,
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
            run_id=run_id,
            reason=BlockedReason.ENVIRONMENT,
            note=str(exc),
        )
        return _CommitOutcome.BLOCKED

    return _CommitOutcome.DONE


def _git_commit_decisions_log(worktree_path: Path, config: CosmoConfig) -> None:
    # unified_identity: no `-c` override, inherit the repo's own local git
    # config -- same posture as `_do_merging`'s `author` below.
    identity_flags: list[str] = []
    if not config.git.unified_identity:
        identity_flags = [
            "-c",
            f"user.name={config.git.commit_author_name}",
            "-c",
            f"user.email={config.git.commit_author_email}",
        ]
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
                *identity_flags,
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

    author = (
        None
        if config.git.unified_identity
        else (config.git.commit_author_name, config.git.commit_author_email)
    )
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
            run_id=run_id,
            reason=BlockedReason.ENVIRONMENT,
            note=str(exc),
        )

    return TaskStatus.DONE if merge_result.outcome.merged else TaskStatus.BLOCKED


# -- FINISHING (v4 workflow changes) -----------------------------------------


def _do_finishing(
    *,
    ctx: TaskContext,
    repo_path: Path,
    config: CosmoConfig,
    emitter: EventEmitter,
    run_id: str | None,
) -> None:
    """v4 workflow changes: `openspec archive <spec_id>` only (v1 scope,
    deliberately). Runs against `repo_path` -- Cosmo's own dedicated
    `base_branch` checkout, which by this point already holds the
    just-merged commit(s) (`git.merge`'s own "repo_path is always on
    base_branch" invariant) -- never `ctx.worktree_path`, which `merge_task`
    has already removed by the time this runs.

    `spec_id` is derived the same way `run.loop._run_one_task` derives it
    for branch naming (`Path(spec_path).stem`) -- a v4-flow task's own
    `PROPOSING` step is expected to name its `openspec new change` the same
    way, so the two stay in sync without either one hardcoding the other's
    convention (see `docs/v4-changes-to-workflow-plan.md`'s state-doc
    write-up for why this wasn't spelled out in the original plan).

    Deliberately best-effort and non-blocking, per the plan's own decision:
    the task already merged successfully by the time this runs, so a
    failure here must never retroactively fail it -- only a warning event.

    Found live (deviation 68 follow-up): `openspec archive` mutates
    `repo_path`'s working tree directly (moves the change's files under
    `openspec/changes/archive/`, rewrites `openspec/specs/`) but never
    commits the result on its own -- every earlier version of this function
    left `repo_path` sitting dirty after every single completed task, which
    then made the *next* task's `MERGING` step (`git.merge._assert_ready`)
    refuse to merge at all ("has uncommitted changes -- refusing to merge"),
    confirmed live against a real two-task run. Committing the archive's own
    output here, the same way `_git_commit_decisions_log` commits
    `docs/decisions-log.md` in the worktree, is what actually closes that
    gap -- a warning event alone was never going to un-dirty `repo_path`.
    """
    task_id = ctx.task_id
    spec_id = Path(ctx.spec_path).stem
    try:
        archive_change(repo_path, spec_id)
    except OpenSpecInitError as exc:
        emitter.emit(
            event_type=EventType.TASK_FINISHING_FAILED,
            severity=Severity.WARNING,
            run_id=run_id,
            task_id=task_id,
            payload={"spec_id": spec_id, "error": str(exc)},
        )
        return

    try:
        _git_commit_archive(repo_path, spec_id, config)
    except GitCommandError as exc:
        emitter.emit(
            event_type=EventType.TASK_FINISHING_FAILED,
            severity=Severity.WARNING,
            run_id=run_id,
            task_id=task_id,
            payload={"spec_id": spec_id, "error": str(exc)},
        )


def _git_commit_archive(repo_path: Path, spec_id: str, config: CosmoConfig) -> None:
    """Commits whatever `archive_change` just changed in `repo_path` --
    scoped to `openspec/` specifically, mirroring `_git_commit_decisions_log`'s
    own scoped `git add`. A no-op, not an error, when `openspec archive`
    happened to change nothing (`--skip-specs` is never passed, but a change
    with no spec deltas is still possible)."""
    identity_flags: list[str] = []
    if not config.git.unified_identity:
        identity_flags = [
            "-c",
            f"user.name={config.git.commit_author_name}",
            "-c",
            f"user.email={config.git.commit_author_email}",
        ]
    try:
        subprocess.run(
            ["git", "-C", str(repo_path), "add", "-A", "--", "openspec"],
            check=True,
            capture_output=True,
            text=True,
            timeout=60.0,
        )
        staged = subprocess.run(
            ["git", "-C", str(repo_path), "diff", "--cached", "--quiet"],
            capture_output=True,
            text=True,
            timeout=60.0,
        )
        if staged.returncode == 0:
            return
        subprocess.run(
            [
                "git",
                "-C",
                str(repo_path),
                *identity_flags,
                "commit",
                "-m",
                f"cosmo: archive {spec_id}",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60.0,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        raise GitCommandError(f"could not commit openspec archive for {spec_id!r}: {exc}") from exc


# -- shared helpers -----------------------------------------------------------


def _block(
    *,
    writer: StoreWriter,
    emitter: EventEmitter,
    task_id: str,
    run_id: str | None = None,
    reason: BlockedReason,
    note: str | None,
) -> TaskStatus:
    transition = writer.queue_block(task_id, reason, run_id=run_id, note=note)
    emitter.emit(
        event_type=EventType.TASK_BLOCKED,
        severity=Severity.WARNING,
        run_id=run_id,
        task_id=task_id,
        payload={"blocked_reason": reason.value, "note": note},
    )
    emit_state_changed(emitter, transition)
    return TaskStatus.BLOCKED


def _requeue(
    *, writer: StoreWriter, emitter: EventEmitter, task_id: str, run_id: str | None
) -> TaskStatus:
    """Spec 3.3's run-level wall clock ("in-flight task returns to QUEUED")
    and spec 7.1/7.2's quota pause both resolve here (`task.types.
    RunGuardAction.REQUEUE`'s docstring): neither is this task's fault, so
    `attempt_count` is left untouched -- unlike `_block`, this is not a
    terminal outcome for the task, only for this attempt at running it."""
    transition = writer.queue_transition(task_id, TaskStatus.QUEUED, run_id=run_id)
    emit_state_changed(emitter, transition)
    return TaskStatus.QUEUED


def _retry_delay(config: CosmoConfig) -> None:
    time.sleep(config.retries.delay_min)
