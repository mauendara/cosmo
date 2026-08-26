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
from cosmo.git.worktree import WorktreeInfo, create_worktree, remove_worktree
from cosmo.harness.base import HarnessAdapter, HarnessResult
from cosmo.retention import apply_log_retention
from cosmo.run.breaker import CircuitBreaker
from cosmo.run.cost import check_run_cost, task_cost_ceiling_reached
from cosmo.run.dag import DagCycleError, resolve_execution_order
from cosmo.run.quota import HeuristicTracker, QuotaSignal, decide, observe_harness_result
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
) -> RunOutcome:
    """Drives the whole task queue to completion, a breaker trip, a quota
    stop, or a wall-clock/cost stop -- one `cosmo run` (no `--task`)
    invocation. `monotonic`/`wall_clock_now`/`sleep` are injectable
    (matching `task.timeouts`'s own testing posture) so a test exercising
    the 5-hour auto-resume path never actually sleeps for real."""
    db_path = config.paths.db_path
    run_id = uuid.uuid4().hex

    # Spec 9.5, best-effort and run-id-independent -- prunes old
    # `raw_log_path` files under `paths.log_dir` before this run even
    # starts. Placed ahead of `run_create` deliberately: a systemd-managed
    # loop (Phase 9) restarts `cosmo run` as a fresh process on every
    # cycle (see docs/handoff.md's Phase 8->9 note), so this is the closest
    # thing to a periodic sweep without a separate cron/timer.
    apply_log_retention(config)

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

    breaker = CircuitBreaker(config.circuit_breaker)
    heuristic = HeuristicTracker(config.quota)
    summary = RunSummary()
    started_monotonic = monotonic()
    deadline_monotonic = started_monotonic + config.timeouts.run_wall
    executed_order: list[str] = []
    cost_warned = False

    final_status = RunStatus.RUNNING
    stop_reason: StopReason | None = None
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
                emitter.emit(
                    event_type=EventType.RUN_STOPPED,
                    severity=Severity.CRITICAL,
                    run_id=run_id,
                    payload={"reason": StopReason.DISK_LOW.value, "detail": disk_check.detail},
                )
                final_status, stop_reason = RunStatus.STOPPED, StopReason.DISK_LOW
                break
            watchdog_notify(ready=True)

        if monotonic() >= deadline_monotonic:
            final_status, stop_reason = RunStatus.STOPPED, StopReason.MAX_TIME
            break

        try:
            order = resolve_execution_order(list_tasks(db_path))
        except DagCycleError as exc:
            emitter.emit(
                event_type=EventType.RUN_STOPPED,
                severity=Severity.CRITICAL,
                run_id=run_id,
                payload={"reason": StopReason.MANUAL.value, "error": str(exc)},
            )
            final_status, stop_reason = RunStatus.STOPPED, StopReason.MANUAL
            break
        if not order:
            final_status, stop_reason = RunStatus.STOPPED, StopReason.QUEUE_EMPTY
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
            severity=Severity.INFO,
            run_id=run_id,
            payload={"reason": stop_reason.value if stop_reason else None},
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
    """Applies `quota.decide`'s verdict: `None` means the run paused, slept
    out the window, and resumed -- the caller should `continue` its loop.
    A `StopReason` means the run should stop with that reason instead."""
    decision = decide(
        signal,
        config=config.quota,
        run_wall_remaining_seconds=deadline_monotonic - monotonic(),
        now=wall_clock_now(),
    )
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
) -> _TaskRunResult:
    """One `task.machine.run_task` call, wired to the run loop's cost/quota
    observation via its two hooks. `box` is the closure state
    `on_harness_result`/`check_run_guard` share -- both run on the same
    thread `run_task` calls them from (its own retry loop, never a
    background thread), so no lock is needed."""
    task_id = task.task_id
    spec_id = Path(task.spec_path).stem

    branch = f"task/{spec_id}"
    current_run_worktree = config.paths.work_dir / run_id / task_id
    if task.worktree_path is not None and Path(task.worktree_path) == current_run_worktree:
        # A run guard (wall clock or quota) already requeued this task
        # earlier in *this* run -- `queue_transition` back to `QUEUED`
        # deliberately leaves `worktree_path` alone (unlike `queue_
        # complete`/`queue_block`, spec 3.2's terminal-state cleanup), so
        # the worktree `create_worktree` made on the first attempt is still
        # there. Reuse it: a second `git worktree add` at the same path
        # would fail outright (branch and directory both already exist).
        info = WorktreeInfo(task_id=task_id, branch=branch, path=Path(task.worktree_path))
    else:
        if task.worktree_path is not None:
            # A task retried (`cosmo queue retry`) after being `BLOCKED`
            # in a *previous* `cosmo run` invocation: it still carries
            # that old run's `worktree_path`, a different run_id and a
            # different path -- reusing it would be wrong. Spec 3.2
            # retains a BLOCKED task's worktree *and branch* for
            # inspection, but `git worktree add` below needs `branch`
            # (task-scoped, not run-scoped) to not already exist -- a
            # retry means starting over, not resurrecting the abandoned
            # attempt, so both are removed first. Found by hand: an
            # earlier version of this function reused the stale path
            # unconditionally and either pointed at a removed directory
            # or, once that was fixed, still collided on the branch name
            # `git worktree add` tried to create fresh (see this phase's
            # own state-doc section).
            remove_worktree(
                repo_path=repo_path, worktree_path=Path(task.worktree_path), branch=branch
            )
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
