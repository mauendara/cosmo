"""Startup crash recovery (v5 improvements plan part 1): a task interrupted
mid-flight by a killed/crashed `cosmo run` process is not silently lost
forever, and a stale process lock stops two `cosmo run` invocations from
racing the same queue.

Called from `run.loop.run_queue` immediately alongside the existing
`git.worktree.sweep_stale_worktrees` call -- same "nothing is running at
startup, by definition" reasoning that call already documents in its own
docstring, extended from worktree directories to `task_queue`/`run_state`
rows.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass
from pathlib import Path

from cosmo.events.emitter import EventEmitter
from cosmo.events.envelope import EventType
from cosmo.events.helpers import emit_state_changed
from cosmo.store.enums import FailureStage, FailureType, NextAction, RunStatus, Severity, StopReason
from cosmo.store.enums import TaskStatus as TS
from cosmo.store.reader import list_running_runs, list_tasks
from cosmo.store.writer import StoreWriter

_INTERRUPTIBLE_STATUSES = frozenset(
    s.value for s in TS if s not in (TS.QUEUED, TS.DONE, TS.BLOCKED)
)

# Coarse best-fit `FailureStage` per interrupted status -- there is no
# 1:1 mapping for every status (VALIDATING covers three real gate stages;
# FINISHING/FAILED_RETRY have no stage of their own at all), so this picks
# the closest one rather than adding new enum values for a purely
# informational "what was it doing" attribution.
_STAGE_BY_STATUS: dict[str, FailureStage] = {
    TS.PROPOSING.value: FailureStage.PROPOSE,
    TS.PROPOSED.value: FailureStage.PROPOSE,
    TS.IMPLEMENTING.value: FailureStage.IMPLEMENT,
    TS.VALIDATING.value: FailureStage.BUILD,
    TS.REVIEWING.value: FailureStage.ADVERSARIAL_REVIEW,
    TS.COMMITTING.value: FailureStage.COMMIT,
    TS.MERGING.value: FailureStage.MERGE,
    TS.FINISHING.value: FailureStage.MERGE,
    TS.FAILED_RETRY.value: FailureStage.IMPLEMENT,
}


@dataclass(frozen=True, slots=True)
class ReconcileOutcome:
    requeued_task_ids: list[str]
    crashed_run_ids: list[str]


def reconcile_interrupted_tasks(
    *, db_path: Path, writer: StoreWriter, emitter: EventEmitter, run_id: str | None
) -> ReconcileOutcome:
    """Requeues every task not in `queued`/`done`/`blocked` (crash-orphaned
    by a prior process) and marks every `run_state` row still `running` as
    `stopped`/`crashed`. `run_id` is the *new* run about to start (or
    resume) -- the task-level failure/transition/event rows this writes are
    attributed to it, not to whichever run originally owned the task,
    matching this being a startup fact discovered by the new run, not a
    historical correction of the old one. `None` for `cosmo run --task`'s
    single-task CLI path (found live: that path never called this at all,
    so a killed `--task` invocation left its task stuck forever -- outside
    `queued`, so invisible to `run.dag.resolve_execution_order`, and the
    next `cosmo run --task <same-id>` refused outright with "not queued"),
    preserving Phase 7's own "no run tracking" posture (`task_failures.
    run_id`/`task_transitions.run_id` are nullable for exactly this reason
    -- there is no `run_state` row for this caller's own work to attribute
    anything to).

    Idempotent and cheap to call unconditionally: a healthy startup finds
    nothing to reconcile (every task is `queued`/`done`/`blocked`, every run
    is already `paused`/`stopped`)."""
    requeued: list[str] = []
    for task in list_tasks(db_path):
        if task.status not in _INTERRUPTIBLE_STATUSES:
            continue
        event = emitter.emit(
            event_type=EventType.TASK_INTERRUPTED,
            severity=Severity.WARNING,
            run_id=run_id,
            task_id=task.task_id,
            payload={"previous_status": task.status},
        )
        writer.record_task_failure(
            task_id=task.task_id,
            run_id=run_id,
            attempt_number=task.attempt_count,
            failure_type=FailureType.ENVIRONMENT_ERROR,
            failure_stage=_STAGE_BY_STATUS.get(task.status, FailureStage.IMPLEMENT),
            error_summary=f"process crashed or was killed while {task.status}",
            error_detail=None,
            files_touched=[],
            will_retry=True,
            next_action=NextAction.RETRY,
            event_id=event.event_id,
        )
        # Spec 3.3's own "in-flight task returns to queued" behavior for a
        # clean max_time stop, now also applied to an unclean crash -- never
        # `queue_retry` (that resets attempt_count, a genuine fresh start);
        # this must not consume the code-level retry budget (the circuit
        # breaker's environment-error tally is the thing that bounds it).
        transition = writer.queue_transition(task.task_id, TS.QUEUED, run_id=run_id)
        writer.queue_clear_worktree_path(task.task_id)
        emit_state_changed(emitter, transition)
        requeued.append(task.task_id)

    crashed: list[str] = []
    for run in list_running_runs(db_path):
        if run.run_id == run_id:
            # `run.loop.run_queue` calls this *after* transitioning the
            # new/resumed run's own row to `running` (a real foreign-key
            # constraint on `task_failures`/`task_transitions` forces that
            # ordering -- see deviation 52) -- so that row is always,
            # legitimately, `running` right here. Without this guard every
            # fresh `cosmo run` would immediately mark its own brand-new run
            # `stopped`/`crashed` a few lines after starting it (found by a
            # real test failure, not by inspection).
            continue
        writer.run_transition(run.run_id, RunStatus.STOPPED, stop_reason=StopReason.CRASHED)
        emitter.emit(
            event_type=EventType.RUN_STOPPED,
            severity=Severity.WARNING,
            run_id=run.run_id,
            payload={"reason": StopReason.CRASHED.value},
        )
        crashed.append(run.run_id)

    return ReconcileOutcome(requeued_task_ids=requeued, crashed_run_ids=crashed)


class RunLockHeldError(RuntimeError):
    """Another live `cosmo run` process already holds the lock file."""


@dataclass(slots=True)
class RunLock:
    path: Path

    def release(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just owned by someone else
    return True


def acquire_run_lock(data_dir: Path) -> RunLock:
    """One `cosmo run`/`cosmo run resume` at a time per `data_dir` (v5
    improvements plan part 1's own "decided" note): a live lock refuses to
    start with a clear error; a stale one (its PID no longer alive) is
    reclaimed automatically, the same "stale is not sacred" posture
    `git.worktree.sweep_stale_worktrees` already applies to worktrees."""
    data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = data_dir / "cosmo-run.lock"
    if lock_path.is_file():
        try:
            held_pid = int(lock_path.read_text().strip())
        except (ValueError, OSError):
            held_pid = None
        if held_pid is not None and _pid_alive(held_pid):
            raise RunLockHeldError(
                f"another cosmo run (pid {held_pid}) already holds {lock_path} -- "
                "wait for it to finish, or remove the lock file if you've confirmed it's dead"
            )
        lock_path.unlink(missing_ok=True)
    lock_path.write_text(str(os.getpid()))
    return RunLock(path=lock_path)
