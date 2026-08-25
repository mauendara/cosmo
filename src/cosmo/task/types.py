"""Small shared shapes for the task state machine (Phase 7, spec 3.2)."""

from __future__ import annotations

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
