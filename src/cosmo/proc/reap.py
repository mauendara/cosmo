"""Ties `cancel()` + the orphan sweep into one operation and emits the
reap-failure event spec 2.4 step 6 requires (plan Phase 2 build item 5).

Goes through the caller's `EventEmitter` -- and therefore the single
`StoreWriter` the main loop owns (spec 8) -- rather than opening any path of
its own; Phase 1 built that machinery specifically so later phases don't grow
a second one.

The circuit breaker itself is Phase 8's job. This module only emits the event
with the right `failure_type` and carries `config.circuit_breaker
.reap_failure_weight` in the payload so the breaker (once it exists) can
double-weight it, per spec 6.5's "a leaked process pool poisons every
subsequent task."
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cosmo.config import CosmoConfig
from cosmo.events import EventEmitter, EventType, Severity
from cosmo.proc.managed import ManagedProcess
from cosmo.proc.orphans import SweepResult, find_worktree_holders, sweep
from cosmo.store.enums import FailureType


@dataclass(frozen=True, slots=True)
class ReapOutcome:
    killpg_clean: bool
    sweep: SweepResult

    @property
    def fully_reaped(self) -> bool:
        return self.killpg_clean and self.sweep.clean


def cancel_and_reap(
    process: ManagedProcess,
    *,
    run_id: str,
    task_id: str,
    worktree_path: Path,
    config: CosmoConfig,
    emitter: EventEmitter,
    docker_bin: str = "docker",
) -> ReapOutcome:
    killpg_clean = process.cancel(grace_s=config.timeouts.kill_grace)
    sweep_result = sweep(run_id, task_id, worktree_path, docker_bin=docker_bin)
    outcome = ReapOutcome(killpg_clean=killpg_clean, sweep=sweep_result)

    if not outcome.fully_reaped:
        if not killpg_clean:
            detail = "process group survived SIGKILL"
        else:
            detail = "a process escaped the group and still holds the worktree"
        emitter.emit(
            event_type=EventType.TASK_FAILED,
            severity=Severity.CRITICAL,
            run_id=run_id,
            task_id=task_id,
            payload={
                "failure_type": FailureType.ENVIRONMENT_ERROR.value,
                "error_detail": f"process reap failed: {detail}",
                "circuit_breaker_weight": config.circuit_breaker.reap_failure_weight,
                "containers_removed": sweep_result.removed_containers,
                "worktree_holder_pids": sweep_result.worktree_holder_pids,
            },
        )
    return outcome


def sweep_orphans_after_completion(
    *, run_id: str | None, task_id: str, worktree_path: Path, emitter: EventEmitter
) -> list[int]:
    """G3 (docs/v15-fixes-after-wa-chat-run.md): the ordinary-completion
    counterpart to `cancel_and_reap`'s own worktree-holder backstop.
    `cancel_and_reap` only ever runs from a forced cancellation (an
    operator cancel, the cost guard tripping) -- an ordinary
    success/failure/timeout ending never called it, which is how 12 stray
    `vite preview`/`http-server` processes (backgrounded via a harness
    session's own `Bash` calls, escaping the process group) accumulated
    silently across several already-`done` tasks and drove real load
    average to ~17-18.

    Deliberately just the worktree-holder detection half, not the full
    `sweep()` (which also does a `docker ps`/`docker rm -f` container
    sweep keyed on `run_id`/`task_id` labels) -- there is no live process
    handle to `killpg` here since the harness call already exited on its
    own, so this can only ever detect and report, never kill, exactly like
    `cancel_and_reap`'s own `worktree_holder_pids` case. Call this
    unconditionally once a harness call returns, regardless of outcome."""
    holders = find_worktree_holders(worktree_path)
    if holders:
        emitter.emit(
            event_type=EventType.TASK_ORPHAN_DETECTED,
            severity=Severity.WARNING,
            run_id=run_id,
            task_id=task_id,
            payload={"worktree_holder_pids": holders},
        )
    return holders
