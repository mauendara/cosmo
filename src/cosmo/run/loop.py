"""The spec 3.1 run-level state machine and its orchestration of Phase 7's
per-task state machine: `IDLE -> RUNNING -> PAUSED -> STOPPED`, DAG
scheduling (`run.dag`), the circuit breaker (`run.breaker`), quota
detection/pause/resume (`run.quota`), and cost ceilings (`run.cost`).

`run_queue` calls `task.machine.run_task` once per DAG-eligible task, in
strictly serial order (spec 5) -- it never reimplements any of Phase 7's
per-task retry/classification logic. Its own influence over a running task
is limited to the two hooks `run_task` exposes for exactly this purpose
(`task.types.RunGuardAction`'s docstring): `on_harness_result` observes
every raw `HarnessResult` (cost accounting, quota-signal capture);
`check_run_guard` is polled before each new attempt and can ask a task to
stop early (`BLOCK_COST`) or hand control back to this loop (`REQUEUE`),
never anything more invasive.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cosmo.config.model import CosmoConfig
from cosmo.doctor import check_disk
from cosmo.events.emitter import EventEmitter
from cosmo.events.envelope import EventType
from cosmo.gate.runner import run_validation_gate
from cosmo.gate.validate import GateRunner
from cosmo.git.worktree import (
    WorktreeInfo,
    create_worktree,
    sweep_stale_worktrees,
)
from cosmo.harness.base import HarnessAdapter, HarnessResult
from cosmo.retention import apply_log_retention
from cosmo.run.breaker import CircuitBreaker
from cosmo.run.cost import check_run_cost, task_cost_ceiling_reached
from cosmo.run.dag import DagCycleError, resolve_execution_order
from cosmo.run.quota import HeuristicTracker, QuotaSignal, decide, observe_harness_result
from cosmo.run.recovery import (
    acquire_run_lock,
    reconcile_interrupted_tasks,
    requeue_cost_blocked_tasks,
)
from cosmo.run.types import RunOutcome, RunSummary
from cosmo.store.enums import BlockedReason, RunStatus, Severity, StopReason, TaskStatus
from cosmo.store.reader import (
    TaskRow,
    get_run_cost,
    get_task,
    get_task_cost,
    list_events,
    list_task_failures,
    list_tasks,
)
from cosmo.store.writer import StoreWriter
from cosmo.task.machine import run_task
from cosmo.task.types import RunGuardAction, TaskContext
from cosmo.watchdog import notify as watchdog_notify

_NEAR_CAP_FRACTION = 0.8  # spec 11's cap enforcement is exact; this is a softer heads-up.


def run_queue(
    *,
    config: CosmoConfig,
    writer: StoreWriter,
    emitter: EventEmitter,
    adapter: HarnessAdapter,
    repo_path: Path,
    base_branch: str,
    harness_name: str,
    gate_runner: GateRunner = run_validation_gate,
    monotonic: Callable[[], float] = time.monotonic,
    wall_clock_now: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
    on_activity: Callable[[str], None] | None = None,
    resume_run_id: str | None = None,
) -> RunOutcome:
    """Thin wrapper around `_run_queue_locked`: acquires the v5 improvements
    plan part 1 process lock (only one `run_queue` may run against a given
    `config.paths.data_dir` at a time) and guarantees its release on every
    exit path, including an exception -- a bare `try/finally` around the
    whole body rather than reindenting it, since `RunLock.release()` is
    idempotent and cheap (`unlink`) either way."""
    lock = acquire_run_lock(config.paths.data_dir)
    try:
        return _run_queue_locked(
            config=config,
            writer=writer,
            emitter=emitter,
            adapter=adapter,
            repo_path=repo_path,
            base_branch=base_branch,
            harness_name=harness_name,
            gate_runner=gate_runner,
            monotonic=monotonic,
            wall_clock_now=wall_clock_now,
            sleep=sleep,
            on_activity=on_activity,
            resume_run_id=resume_run_id,
        )
    finally:
        lock.release()


def _run_queue_locked(
    *,
    config: CosmoConfig,
    writer: StoreWriter,
    emitter: EventEmitter,
    adapter: HarnessAdapter,
    repo_path: Path,
    base_branch: str,
    harness_name: str,
    gate_runner: GateRunner,
    monotonic: Callable[[], float],
    wall_clock_now: Callable[[], datetime],
    sleep: Callable[[float], None],
    on_activity: Callable[[str], None] | None,
    resume_run_id: str | None,
) -> RunOutcome:
    """Drives the whole task queue to completion, a breaker trip, a quota
    stop, or a wall-clock/cost stop -- one `cosmo run` (no `--task`)
    invocation. `monotonic`/`wall_clock_now`/`sleep` are injectable
    (matching `task.timeouts`'s own testing posture) so a test exercising
    the 5-hour auto-resume path never actually sleeps for real.

    `on_activity`, if given, is threaded straight through to each task's
    `run_task(..., on_activity=...)` (item 3) -- purely a CLI/presentation
    concern (a line to print), so this module stays as ignorant of it as it
    is of `console`/Rich; `cli.main` is what actually builds a real one.

    `resume_run_id` (v5 improvements plan part 2): when given, reuses that
    `run_id` instead of minting a fresh one and skips `run_create` (the row
    already exists, `paused`) -- `RUN_RESUMED` is emitted instead of
    `RUN_STARTED`. Cost accounting picks back up correctly for free
    (`run_cost`/`task_cost` are already keyed by `run_id`, spec 8's own
    current-state schema); the wall-clock budget below is deliberately
    *not* carried over -- a resumed run gets a fresh `timeouts.run_wall`
    starting now (decision 2), not an accounting of time spent paused."""
    db_path = config.paths.db_path
    run_id = resume_run_id if resume_run_id is not None else uuid.uuid4().hex

    # Spec 9.5, best-effort and run-id-independent -- prunes old
    # `raw_log_path` files under `paths.log_dir` before this run even
    # starts. Placed ahead of `run_create` deliberately: a systemd-managed
    # loop (Phase 9) restarts `cosmo run` as a fresh process on every
    # cycle (see docs/handoff.md's Phase 8->9 note), so this is the closest
    # thing to a periodic sweep without a separate cron/timer.
    apply_log_retention(config)

    # Spec 3.2's own "a startup sweep prunes worktrees belonging to
    # completed runs" -- built in Phase 5 (`git.worktree.sweep_
    # stale_worktrees`) but never actually called from anywhere until now
    # (flagged as a real gap in both Phase 8's and Phase 9's own state-doc
    # sections). Nothing is "running" at process start by definition, so
    # every worktree currently on disk belongs to a run that already ended
    # -- a `DONE` task's own worktree is normally removed inline by `git.
    # merge.merge_task` already, so this mainly recovers a task that
    # crashed mid-attempt (never reached a terminal `remove_worktree` call
    # at all) or one left behind by a killed/restarted process (the same
    # systemd-restart-as-fresh-run scenario the log retention comment
    # above describes). A `BLOCKED` task's worktree is retained for
    # inspection (spec 3.2), same as always.
    sweep_stale_worktrees(repo_path=repo_path, work_dir=config.paths.work_dir, db_path=db_path)

    if resume_run_id is not None:
        writer.run_transition(run_id, RunStatus.RUNNING)
        emitter.emit(
            event_type=EventType.RUN_RESUMED, severity=Severity.INFO, run_id=run_id, payload={}
        )
    else:
        writer.run_create(
            run_id=run_id,
            harness=harness_name,
            permission_mode=config.harness.permission_mode,
            max_turns=config.harness.max_turns,
            base_branch=base_branch,
        )
        writer.run_transition(run_id, RunStatus.RUNNING)
        emitter.emit(
            event_type=EventType.RUN_STARTED,
            severity=Severity.INFO,
            run_id=run_id,
            payload={
                "harness": harness_name,
                "permission_mode": config.harness.permission_mode,
                "max_turns": config.harness.max_turns,
                "base_branch": base_branch,
                "run_wall_seconds": config.timeouts.run_wall,
                "max_cost_per_run_usd": config.cost.max_cost_per_run_usd,
            },
        )

    # v5 improvements plan part 1: the worktree sweep's own sibling for
    # `task_queue`/`run_state` rows -- a task interrupted mid-flight by a
    # crashed/killed process is not "restarted from scratch" (spec 3.2's
    # own promise) without this; it's lost forever, since `run.dag.
    # resolve_execution_order` only ever considers `queued` tasks. Runs
    # unconditionally, on the way into a fresh run and a resumed one alike
    # (a resume also defends against the process having been killed while
    # paused-and-sleeping). Deliberately placed *after* the run row above
    # exists (`run_create`/the resume branch's `run_transition`), not
    # before: the task-level rows this writes are attributed to `run_id`,
    # and `task_failures`/`task_transitions` both hold a real foreign key
    # to `run_state(run_id)`.
    reconcile_interrupted_tasks(db_path=db_path, writer=writer, emitter=emitter, run_id=run_id)
    # v7: a second, independent startup reconciliation -- re-evaluates
    # blocked/cost tasks against *this* invocation's config, since that
    # block can only ever legitimately clear between runs (see the
    # function's own docstring). Placement mirrors reconcile_interrupted_
    # tasks above: unconditional, fresh-and-resumed-run-alike.
    requeue_cost_blocked_tasks(
        db_path=db_path, writer=writer, emitter=emitter, config=config, run_id=run_id
    )

    breaker = CircuitBreaker(config.circuit_breaker)
    heuristic = HeuristicTracker(config.quota)
    summary = RunSummary()
    started_monotonic = monotonic()
    deadline_monotonic = started_monotonic + config.timeouts.run_wall
    executed_order: list[str] = []
    cost_warned = False

    final_status = RunStatus.RUNNING
    stop_reason: StopReason | None = None
    # A startup-time abort (disk_low, a DAG cycle) carries richer detail and
    # a higher severity than the generic post-loop RUN_STOPPED emission
    # every non-PAUSED final_status gets below -- captured here instead of
    # emitting a second, separate RUN_STOPPED event for the same stop (a
    # real, pre-existing duplicate-event gap found by hand and fixed as
    # part of this session's own v5 work, not part of the plan itself).
    stop_severity = Severity.INFO
    stop_extra_payload: dict[str, object] = {}
    disk_checked = False

    while True:
        watchdog_notify(watchdog=True)

        if not disk_checked:
            # Spec 9.5: "a full disk fails every subsequent task in a way
            # that reads as a code error" -- checked once, here rather than
            # before `run_create` above, so the abort itself is a real,
            # queryable `run.stopped`/`stop_reason=disk_low` row (spec 3.1's
            # own "abort the run" framing), the same posture the DAG-cycle
            # case below already takes for its own startup-time abort.
            disk_checked = True
            disk_check = check_disk(config)
            if disk_check.blocking:
                stop_severity = Severity.CRITICAL
                stop_extra_payload = {"detail": disk_check.detail}
                final_status, stop_reason = RunStatus.STOPPED, StopReason.DISK_LOW
                break
            watchdog_notify(ready=True)

        if monotonic() >= deadline_monotonic:
            final_status, stop_reason = RunStatus.STOPPED, StopReason.MAX_TIME
            break

        current_tasks = list_tasks(db_path)
        try:
            order = resolve_execution_order(current_tasks)
        except DagCycleError as exc:
            stop_severity = Severity.CRITICAL
            stop_extra_payload = {"error": str(exc)}
            final_status, stop_reason = RunStatus.STOPPED, StopReason.MANUAL
            break
        if not order:
            # Every `queued` task at this point (if any) has an unmet
            # `depends_on` edge -- `resolve_execution_order` would have
            # returned it otherwise (its own Kahn's-algorithm pass already
            # accounts for every dependency chain resolvable from the
            # current `done` set). Surfaced separately from `blocked_by_
            # reason` since these tasks are still `queued`, not `blocked` --
            # nothing failed, the DAG is just stuck.
            summary.stalled_queued_tasks = sorted(
                t.task_id for t in current_tasks if t.status == "queued"
            )
            # v7: distinguishes "genuinely nothing left to do" from "nothing
            # is schedulable because every remaining task is BLOCKED" -- both
            # used to collapse into QUEUE_EMPTY, which cli.main then rendered
            # green/exit-0 either way (see docs/v7-complete-queue-done-fixes-
            # plan.md; the dominant cost in the Phase 10 acceptance run's own
            # timing data was exactly this gap going unnoticed for hours).
            final_status = RunStatus.STOPPED
            stop_reason = (
                StopReason.BLOCKED_REMAINING
                if summary.blocked_by_reason
                else StopReason.QUEUE_EMPTY
            )
            break

        task_id = order[0]
        executed_order.append(task_id)
        task_row = get_task(db_path, task_id)
        assert task_row is not None  # just read from resolve_execution_order's own input

        result = _run_one_task(
            task=task_row,
            run_id=run_id,
            config=config,
            writer=writer,
            emitter=emitter,
            adapter=adapter,
            repo_path=repo_path,
            base_branch=base_branch,
            harness_name=harness_name,
            gate_runner=gate_runner,
            deadline_monotonic=deadline_monotonic,
            monotonic=monotonic,
            on_activity=on_activity,
        )

        run_total_cost = get_run_cost(db_path, run_id)
        cost_verdict = check_run_cost(run_total_cost, config.cost)
        if cost_verdict.warn and not cost_warned:
            cost_warned = True
            emitter.emit(
                event_type=EventType.RUN_COST_WARNING,
                severity=Severity.WARNING,
                run_id=run_id,
                payload={
                    "total_cost_usd": run_total_cost,
                    "limit_usd": config.cost.max_cost_per_run_usd,
                },
            )
        if cost_verdict.stop_run:
            final_status, stop_reason = RunStatus.STOPPED, StopReason.COST_LIMIT_REACHED
            break

        if result.quota_signal is not None:
            # A confirmed signal (primary/secondary, `_run_one_task`'s own
            # `observe_harness_result` call) -- always resolved before the
            # breaker or the heuristic ever get a say, since a real signal
            # is strictly more trustworthy than either.
            stop = _handle_quota_pause_or_stop(
                result.quota_signal,
                config=config,
                deadline_monotonic=deadline_monotonic,
                monotonic=monotonic,
                wall_clock_now=wall_clock_now,
                writer=writer,
                emitter=emitter,
                run_id=run_id,
                sleep=sleep,
            )
            if stop is not None:
                final_status, stop_reason = RunStatus.STOPPED, stop
                break
            continue

        if result.status is TaskStatus.DONE:
            summary.completed += 1
            breaker.record_done()
            continue

        if result.status is TaskStatus.QUEUED:
            # A pure run-wall-clock requeue (a confirmed quota signal was
            # already handled above) -- deliberately not fed to the
            # heuristic tracker at all: it belongs to BLOCKED outcomes only
            # (see the comment below), and a requeue is not a task outcome
            # in the first place.
            summary.requeued += 1
            continue

        if result.status is TaskStatus.BLOCKED:
            blocked_row = get_task(db_path, task_id)
            assert blocked_row is not None and blocked_row.blocked_reason is not None
            reason = blocked_row.blocked_reason
            summary.blocked_by_reason[reason] = summary.blocked_by_reason.get(reason, 0) + 1
            env_weight = _environment_error_weight(db_path, run_id, task_id, config)
            pause_reason = breaker.record_blocked(
                BlockedReason(reason), environment_error_weight=env_weight
            )
            if pause_reason is not None:
                writer.run_transition(run_id, RunStatus.PAUSED, pause_reason=pause_reason)
                watchdog_notify(watchdog=True, status=f"paused: {pause_reason.value}")
                emitter.emit(
                    event_type=EventType.RUN_PAUSED,
                    severity=Severity.WARNING,
                    run_id=run_id,
                    payload={"reason": pause_reason.value, "triggering_task": task_id},
                )
                final_status = RunStatus.PAUSED
                break

            # The breaker did not trip on this block -- only now does the
            # tertiary wall-clock heuristic get a look, and only at this
            # BLOCKED outcome (never DONE/QUEUED). Checked last and only
            # here deliberately: a real reproduction of this phase's own
            # manual verification (see docs/v3-implementation-state.md's
            # Phase 8 section) found that feeding the heuristic every
            # outcome let it fire on the *same* evidence an ordinary
            # environment_error breaker trip already explains, masking a
            # real trip behind a needless quota pause.
            if result.last_result is not None:
                heuristic_signal = heuristic.observe(result.last_result)
                if heuristic_signal is not None:
                    stop = _handle_quota_pause_or_stop(
                        heuristic_signal,
                        config=config,
                        deadline_monotonic=deadline_monotonic,
                        monotonic=monotonic,
                        wall_clock_now=wall_clock_now,
                        writer=writer,
                        emitter=emitter,
                        run_id=run_id,
                        sleep=sleep,
                    )
                    if stop is not None:
                        final_status, stop_reason = RunStatus.STOPPED, stop
                        break
            continue

        raise AssertionError(f"unreachable: run_task returned {result.status!r}")

    summary.total_duration_seconds = monotonic() - started_monotonic
    summary.total_cost_usd = get_run_cost(db_path, run_id)
    _fill_summary_extras(
        summary, db_path=db_path, run_id=run_id, repo_path=repo_path, config=config
    )

    if final_status is not RunStatus.PAUSED:
        # PAUSED already made its own run_transition/RUN_PAUSED emission
        # above -- a breaker trip requires manual intervention (spec 6.5),
        # so this loop simply ends rather than transitioning further.
        writer.run_transition(run_id, final_status, stop_reason=stop_reason)
        watchdog_notify(
            watchdog=True, status=f"stopped: {stop_reason.value if stop_reason else 'unknown'}"
        )
        emitter.emit(
            event_type=EventType.RUN_STOPPED,
            severity=stop_severity,
            run_id=run_id,
            payload={"reason": stop_reason.value if stop_reason else None, **stop_extra_payload},
        )

    emitter.emit(
        event_type=EventType.RUN_SUMMARY,
        severity=Severity.INFO,
        run_id=run_id,
        payload={
            "completed": summary.completed,
            "blocked": summary.blocked,
            "blocked_by_reason": summary.blocked_by_reason,
            "requeued": summary.requeued,
            "retried": summary.retried,
            "flaky_detected": summary.flaky_detected,
            "repeated_merge_conflict_tasks": summary.repeated_merge_conflict_tasks,
            "knowledge_files_near_cap": summary.knowledge_files_near_cap,
            "stalled_queued_tasks": summary.stalled_queued_tasks,
            "total_duration_seconds": summary.total_duration_seconds,
            "total_cost_usd": summary.total_cost_usd,
        },
    )

    return RunOutcome(
        run_id=run_id,
        status=final_status,
        stop_reason=stop_reason,
        summary=summary,
        execution_order=executed_order,
    )


def _handle_quota_pause_or_stop(
    signal: QuotaSignal,
    *,
    config: CosmoConfig,
    deadline_monotonic: float,
    monotonic: Callable[[], float],
    wall_clock_now: Callable[[], datetime],
    writer: StoreWriter,
    emitter: EventEmitter,
    run_id: str,
    sleep: Callable[[float], None],
) -> StopReason | None:
    """Applies `quota.decide`'s verdict: `None` means either the run paused,
    slept out the window, and resumed, or (v5 improvements plan part 7) a
    confirmed `five_hour` signal was deliberately bypassed -- either way the
    caller should `continue` its loop. A `StopReason` means the run should
    stop with that reason instead."""
    decision = decide(
        signal,
        config=config.quota,
        run_wall_remaining_seconds=deadline_monotonic - monotonic(),
        now=wall_clock_now(),
    )
    if decision.bypassed:
        emitter.emit(
            event_type=EventType.QUOTA_BYPASSED,
            severity=Severity.WARNING,
            run_id=run_id,
            payload={
                "resets_at": signal.resets_at,
                "run_cost_so_far_usd": get_run_cost(config.paths.db_path, run_id),
            },
        )
        return None
    if decision.status is RunStatus.STOPPED:
        return decision.stop_reason

    writer.run_transition(run_id, RunStatus.PAUSED, pause_reason=decision.pause_reason)
    watchdog_notify(
        watchdog=True,
        status=f"paused: {decision.pause_reason.value if decision.pause_reason else 'quota'}",
    )
    emitter.emit(
        event_type=EventType.RUN_PAUSED,
        severity=Severity.WARNING,
        run_id=run_id,
        payload={
            "reason": decision.pause_reason.value if decision.pause_reason else None,
            "resume_delay_seconds": decision.resume_delay_seconds,
            "confirmed": signal.confirmed,
        },
    )
    sleep(decision.resume_delay_seconds)
    writer.run_transition(run_id, RunStatus.RUNNING)
    watchdog_notify(watchdog=True, status="running")
    emitter.emit(
        event_type=EventType.RUN_RESUMED, severity=Severity.INFO, run_id=run_id, payload={}
    )
    return None


@dataclass(frozen=True, slots=True)
class _TaskRunResult:
    status: TaskStatus
    quota_signal: QuotaSignal | None
    """A *confirmed* signal only (primary/secondary) -- the tertiary
    heuristic is deliberately not consulted here; see `run_queue`'s own
    comment on why it must run after the breaker, not inside this
    function."""
    last_result: HarnessResult | None
    """The last raw `HarnessResult` observed for this task, exposed so
    `run_queue` can feed it to the heuristic tracker itself, at the one
    point that's actually safe to (see above)."""


def _run_one_task(
    *,
    task: TaskRow,
    run_id: str,
    config: CosmoConfig,
    writer: StoreWriter,
    emitter: EventEmitter,
    adapter: HarnessAdapter,
    repo_path: Path,
    base_branch: str,
    harness_name: str,
    gate_runner: GateRunner,
    deadline_monotonic: float,
    monotonic: Callable[[], float],
    on_activity: Callable[[str], None] | None = None,
) -> _TaskRunResult:
    """One `task.machine.run_task` call, wired to the run loop's cost/quota
    observation via its two hooks. `box` is the closure state
    `on_harness_result`/`check_run_guard` share -- both run on the same
    thread `run_task` calls them from (its own retry loop, never a
    background thread), so no lock is needed."""
    task_id = task.task_id
    spec_id = Path(task.spec_path).stem

    branch = f"task/{spec_id}"
    if task.worktree_path is not None and Path(task.worktree_path).is_dir():
        # This worktree is still mid-lifecycle, not abandoned: either a run
        # guard (wall clock or quota) requeued this task earlier in *this*
        # run (`queue_transition` back to `QUEUED` deliberately leaves
        # `worktree_path` alone), or a previous `cosmo run` process paused
        # or was killed and a later one is now picking the task back up
        # under a *different* run_id. Either way it's safe to reuse
        # regardless of which run_id originally created it:
        # `cli.main.queue_retry` is the only place that ever clears
        # `worktree_path`, and it does so by physically removing the
        # worktree first -- so a `QUEUED` task whose `worktree_path` is
        # still set is unambiguous evidence nothing here was ever abandoned
        # by a human asking to start over. Found by hand: an earlier
        # version of this function scoped reuse to the *current* run_id
        # only, wiping -- and re-proposing from scratch -- a task's already
        # -complete PROPOSING work purely because a quota pause outlived
        # the process that triggered it (see this phase's own state-doc
        # section).
        info = WorktreeInfo(task_id=task_id, branch=branch, path=Path(task.worktree_path))
    else:
        info = create_worktree(
            repo_path=repo_path,
            work_dir=config.paths.work_dir,
            run_id=run_id,
            task_id=task_id,
            spec_id=spec_id,
            base_branch=base_branch,
            harness=harness_name,
            writer=writer,
            emitter=emitter,
        )
    adapter.cwd = info.path

    ctx = TaskContext(
        task_id=task_id,
        spec_path=task.spec_path,
        worktree_path=info.path,
        branch=info.branch,
        base_branch=base_branch,
        allow_test_edits=task.allow_test_edits,
        max_attempts=task.max_attempts,
    )

    box: dict[str, object] = {"quota_signal": None, "last_result": None}

    def on_harness_result(result: HarnessResult) -> None:
        box["last_result"] = result
        if result.total_cost_usd:
            writer.run_cost_add(run_id, result.total_cost_usd)
            writer.task_cost_add(task_id, result.total_cost_usd)
        signal = observe_harness_result(result, config.quota)
        if signal is not None:
            box["quota_signal"] = signal

    def check_run_guard() -> RunGuardAction | None:
        if task_cost_ceiling_reached(get_task_cost(config.paths.db_path, task_id), config.cost):
            return RunGuardAction.BLOCK_COST
        if monotonic() >= deadline_monotonic:
            return RunGuardAction.REQUEUE
        if box["quota_signal"] is not None:
            return RunGuardAction.REQUEUE
        return None

    resume_at = (
        TaskStatus(task.resume_at_stage)
        if task.resume_at_stage is not None
        else TaskStatus.IMPLEMENTING
    )
    status = run_task(
        ctx=ctx,
        config=config,
        writer=writer,
        emitter=emitter,
        adapter=adapter,
        repo_path=repo_path,
        gate_runner=gate_runner,
        run_id=run_id,
        on_harness_result=on_harness_result,
        check_run_guard=check_run_guard,
        on_activity=on_activity,
        resume_at=resume_at,
    )

    confirmed = box["quota_signal"]
    assert confirmed is None or isinstance(confirmed, QuotaSignal)
    last_result = box["last_result"]
    assert last_result is None or isinstance(last_result, HarnessResult)

    return _TaskRunResult(status=status, quota_signal=confirmed, last_result=last_result)


def _environment_error_weight(db_path: Path, run_id: str, task_id: str, config: CosmoConfig) -> int:
    """Spec 6.5: `0` if this task had no `environment_error` during this
    run; otherwise `1`, or `config.reap_failure_weight` instead if a
    process-reap failure occurred for it (`proc.reap.cancel_and_reap`'s own
    `circuit_breaker_weight` payload)."""
    failures = list_task_failures(db_path, task_id, run_id=run_id)
    if not any(f.failure_type == "environment_error" for f in failures):
        return 0
    reap_events = list_events(
        db_path,
        run_id=run_id,
        task_id=task_id,
        event_type=EventType.TASK_FAILED.value,
        limit=10_000,
    )
    weights: list[int] = []
    for e in reap_events:
        weight = e.payload.get("circuit_breaker_weight")
        if isinstance(weight, int):
            weights.append(weight)
    return max(weights) if weights else 1


def _fill_summary_extras(
    summary: RunSummary, *, db_path: Path, run_id: str, repo_path: Path, config: CosmoConfig
) -> None:
    state_changed = list_events(
        db_path, run_id=run_id, event_type=EventType.TASK_STATE_CHANGED.value, limit=10_000
    )
    summary.retried = sum(1 for e in state_changed if e.payload.get("to_state") == "failed_retry")

    validation_results = list_events(
        db_path, run_id=run_id, event_type=EventType.TASK_VALIDATION_RESULT.value, limit=10_000
    )
    flaky: set[str] = set()
    for e in validation_results:
        detected = e.payload.get("flaky_detected")
        if isinstance(detected, list):
            flaky.update(str(x) for x in detected)
    summary.flaky_detected = sorted(flaky)

    # Spec 3.4: "repeated conflicts on the same files... surface this in
    # run.summary" -- checked against each task's *full* history (not just
    # this run), since a human re-queuing a blocked task across separate
    # `cosmo run` invocations is exactly the scenario worth flagging.
    blocked_this_run = list_events(
        db_path, run_id=run_id, event_type=EventType.TASK_BLOCKED.value, limit=10_000
    )
    merge_conflict_task_ids = {
        e.task_id
        for e in blocked_this_run
        if e.payload.get("blocked_reason") == BlockedReason.MERGE_CONFLICT.value and e.task_id
    }
    repeated: list[str] = []
    for task_id in sorted(merge_conflict_task_ids):
        history = list_events(
            db_path, task_id=task_id, event_type=EventType.TASK_BLOCKED.value, limit=10_000
        )
        count = sum(
            1
            for e in history
            if e.payload.get("blocked_reason") == BlockedReason.MERGE_CONFLICT.value
        )
        if count > 1:
            repeated.append(task_id)
    summary.repeated_merge_conflict_tasks = repeated

    summary.knowledge_files_near_cap = _knowledge_files_near_cap(
        repo_path, config.knowledge.max_file_lines
    )


def _knowledge_files_near_cap(repo_path: Path, max_lines: int) -> list[str]:
    docs_dir = repo_path / "docs"
    if not docs_dir.is_dir():
        return []
    threshold = max_lines * _NEAR_CAP_FRACTION
    near_cap: list[str] = []
    for path in sorted(docs_dir.rglob("*.md")):
        if not path.is_file():
            continue
        line_count = sum(1 for _ in path.open("r", encoding="utf-8", errors="replace"))
        if line_count >= threshold:
            near_cap.append(str(path.relative_to(repo_path)))
    return near_cap
