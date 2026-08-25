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
from cosmo.proc.orphans import SweepResult, sweep
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
