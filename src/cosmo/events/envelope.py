"""The event envelope (spec 9.1) and the event types named in spec 9.2.

`schema_version` is carried on every row from day one specifically so this
table can migrate later without a backfill archaeology project -- and so the
payload shapes here can eventually map onto OTel GenAI span attributes
(spec 9.4) without a rewrite.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from cosmo.store.enums import Severity

EVENT_SCHEMA_VERSION = 1

__all__ = ["EVENT_SCHEMA_VERSION", "Event", "EventType", "Severity"]


class EventType(enum.Enum):
    """Spec 9.2."""

    RUN_STARTED = "run.started"
    RUN_PAUSED = "run.paused"
    RUN_RESUMED = "run.resumed"
    RUN_STOPPED = "run.stopped"
    RUN_SUMMARY = "run.summary"
    RUN_COST_WARNING = "run.cost_warning"
    """Phase 8 addition, not in spec 9.2's own enumerated list -- spec 7.3
    requires "a warning event at 80% of max_cost_per_run_usd" but never
    names one. See `docs/v3-implementation-state.md`'s cumulative deviation
    table."""
    AGENT_ASSETS_SYNCED = "agent_assets.synced"
    TASK_STATE_CHANGED = "task.state_changed"
    TASK_FAILED = "task.failed"
    TASK_BLOCKED = "task.blocked"
    TASK_COMPLETED = "task.completed"
    TASK_VALIDATION_RESULT = "task.validation_result"
    TASK_PROGRESS = "task.progress"
    TASK_HEARTBEAT = "task.heartbeat"
    TASK_GUARDRAIL_TRIPPED = "task.guardrail_tripped"
    TASK_FINISHING_FAILED = "task.finishing_failed"
    """v4 workflow changes, not in spec 9.2's own enumerated list (that
    predates `FINISHING`): `_do_finishing`'s best-effort `openspec archive`
    step failed. Always `severity=warning` -- FINISHING never blocks a task
    that already merged successfully, this is purely an observability
    signal for post-run review."""
    TASK_INTERRUPTED = "task.interrupted"
    """v5 improvements plan part 1: a task found mid-flight (any status but
    `queued`/`done`/`blocked`) by the startup reconciliation sweep --
    `run.recovery.reconcile_interrupted_tasks` -- because the process that
    was driving it crashed or was killed. Always `severity=warning`;
    emitted once per reconciled task, before it's requeued."""
    QUOTA_BYPASSED = "quota.bypassed"
    """v5 improvements plan part 7: a confirmed `five_hour` quota signal was
    *not* paused on because `quota.bypass_5h_with_credits` is set -- the
    operator has opted in to spending real usage-credit money past the
    included subscription allowance. Always `severity=warning`."""
    TASK_COST_REQUEUED = "task.cost_requeued"
    """v7: `run.recovery.requeue_cost_blocked_tasks` found a `blocked`/
    `cost` task no longer over the *current* `max_cost_per_task_usd` (a
    human raised the ceiling, or disabled it, between runs) and cleared the
    block. Always `severity=info` -- nothing failed here, unlike `task.
    interrupted`; emitted once per requeued task, before it's transitioned
    back to `queued`."""


@dataclass(frozen=True, slots=True)
class Event:
    event_id: str
    run_id: str | None
    task_id: str | None
    timestamp: str
    sequence: int
    event_type: str
    severity: Severity
    schema_version: int
    payload: dict[str, Any] = field(default_factory=dict)
