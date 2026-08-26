"""Small shared shapes for the task state machine (Phase 7, spec 3.2)."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path

from cosmo.store.enums import FailureStage, FailureType


@dataclass(frozen=True, slots=True)
class TaskContext:
    """Everything `task.machine.run_task` needs about one task that isn't
    already in `config`/`writer`/`emitter`. `worktree_path`/`branch` are
    supplied by the caller (`cosmo run`), already created via
    `git.worktree.create_worktree` -- the state machine itself never touches
    real git worktree creation, only `git.merge.merge_task`'s ladder inside
    `MERGING` (see `docs/v3-implementation-state.md`'s Phase 7 decision on
    why worktree creation stays outside this package)."""

    task_id: str
    spec_path: str
    worktree_path: Path
    branch: str
    base_branch: str
    allow_test_edits: bool
    max_attempts: int


class RunGuardAction(enum.Enum):
    """What `task.machine.run_task`'s optional `check_run_guard` hook
    (Phase 8) can ask it to do instead of starting the next `PROPOSING`/
    `IMPLEMENTING` attempt. Lives here, not in `cosmo.run`, so `cosmo.task`
    never has to import the run package that calls it -- `cosmo.run`
    depends on `cosmo.task`, never the other way (same direction Phase 7
    already established for `cosmo.gate`/`cosmo.git`)."""

    BLOCK_COST = "block_cost"
    """Spec 7.3: this task's accumulated cost hit `max_cost_per_task_usd`.
    `run_task` blocks it (`blocked_reason=cost`) and returns -- the run
    loop's queue continues with the next task."""

    REQUEUE = "requeue"
    """The run itself can no longer make progress on this task right now --
    spec 3.3's run-level wall clock expired, or spec 7.1/7.2's quota
    detection confirmed the harness is rate-limited. Not this task's fault,
    so `run_task` returns it to `QUEUED` (`attempt_count` untouched) instead
    of `BLOCKED`, and the run loop decides what happens to the *run*
    (`STOPPED`/`max_time`, or `PAUSED` pending quota reset)."""


@dataclass(frozen=True, slots=True)
class FailureClassification:
    """What `task.classify.classify_harness_failure` decides for a
    `PROPOSING`/`IMPLEMENTING` failure -- the equivalent of `GateResult`'s
    `failure_type`/`failure_stage`/`error_summary`/`error_detail` fields for
    the two states the gate never sees."""

    failure_type: FailureType
    failure_stage: FailureStage
    error_summary: str
    error_detail: str | None
