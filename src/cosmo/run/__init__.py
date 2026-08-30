"""Phase 8: the run-level state machine (spec 3.1), DAG scheduling over the
task queue (spec 5), the global circuit breaker (spec 6.5), quota
detection/pause/resume (spec 7.1/7.2), and dollar-cost ceilings (spec 7.3).

`run.loop.run_queue` is the orchestrator: it calls `task.machine.run_task`
once per DAG-eligible task, in strictly serial order, and never reimplements
any of Phase 7's per-task retry/classification logic.
"""

from __future__ import annotations

from cosmo.run.breaker import CircuitBreaker
from cosmo.run.cost import CostVerdict, check_run_cost, task_cost_ceiling_reached
from cosmo.run.dag import DagCycleError, find_cycle, resolve_execution_order
from cosmo.run.loop import run_queue
from cosmo.run.quota import (
    HeuristicTracker,
    QuotaDecision,
    QuotaSignal,
    decide,
    observe_harness_result,
)
from cosmo.run.types import RunOutcome, RunSummary

__all__ = [
    "CircuitBreaker",
    "CostVerdict",
    "check_run_cost",
    "task_cost_ceiling_reached",
    "DagCycleError",
    "find_cycle",
    "resolve_execution_order",
    "run_queue",
    "HeuristicTracker",
    "QuotaDecision",
    "QuotaSignal",
    "decide",
    "observe_harness_result",
    "RunOutcome",
    "RunSummary",
]
