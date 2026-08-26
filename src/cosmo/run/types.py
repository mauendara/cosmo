"""Shared shapes for Phase 8's run loop (spec 3.1, 9.2)."""

from __future__ import annotations

from dataclasses import dataclass, field

from cosmo.store.enums import RunStatus, StopReason


@dataclass(slots=True)
class RunSummary:
    """Accumulated by `run.loop.run_queue` as it goes, then folded into the
    `run.summary` event payload (spec 9.2) at the end. Counts only what the
    loop directly observes as it drives each task -- `retried`/
    `flaky_detected`/`repeated_merge_conflict_tasks` are filled in from the
    event/failure history at summary time (`run.loop._build_summary_
    extras`), since those aren't naturally available as the loop's own
    per-task counters."""

    completed: int = 0
    blocked_by_reason: dict[str, int] = field(default_factory=dict)
    requeued: int = 0
    """Tasks a run guard (wall clock or quota) returned to `QUEUED` rather
    than blocking -- not a terminal outcome, so tracked separately from
    `blocked_by_reason`."""
    retried: int = 0
    flaky_detected: list[str] = field(default_factory=list)
    repeated_merge_conflict_tasks: list[str] = field(default_factory=list)
    """Spec 3.4: "repeated conflicts on the same files... surface this in
    run.summary" -- task_ids blocked with `merge_conflict` more than once
    across this task's full history (not just this run), a signal the
    DAG's `depends_on` edges are under-specified."""
    knowledge_files_near_cap: list[str] = field(default_factory=list)
    """Spec 11: `docs/**/*.md` files at or above 80% of
    `knowledge.max_file_lines`, checked once at run end against `repo_path`
    (compaction itself is never automated -- spec 11's own rule)."""
    total_duration_seconds: float = 0.0
    total_cost_usd: float = 0.0

    @property
    def blocked(self) -> int:
        return sum(self.blocked_by_reason.values())


@dataclass(frozen=True, slots=True)
class RunOutcome:
    run_id: str
    status: RunStatus
    stop_reason: StopReason | None
    summary: RunSummary
    execution_order: list[str]
    """The task_ids the DAG scheduler resolved, in the order they were (or,
    for `--dry-run`, would have been) attempted."""
