"""Enums backing the schema's CHECK constraints (spec 5).

Kept as real Python enums, not bare strings, so callers get a typo caught by
mypy instead of by a CHECK constraint at insert time. Each enum's `.value` set
matches its table's CHECK constraint exactly (see `store/migrations.py`) --
the two are two views of one contract, not independent sources of truth.
"""

from __future__ import annotations

import enum


class TaskStatus(enum.Enum):
    """Spec 3.2 task state machine, plus `REVIEWING`/`FINISHING` (v4 workflow
    changes, see `docs/v4-changes-to-workflow-plan.md`): `REVIEWING` sits
    between `VALIDATING` and `COMMITTING` (a fresh adversarial-review harness
    call, gated on `config.review.enabled`); `FINISHING` sits between
    `MERGING` and `DONE` (best-effort `openspec archive`, never blocking)."""

    QUEUED = "queued"
    PROPOSING = "proposing"
    PROPOSED = "proposed"
    IMPLEMENTING = "implementing"
    VALIDATING = "validating"
    REVIEWING = "reviewing"
    COMMITTING = "committing"
    MERGING = "merging"
    FINISHING = "finishing"
    DONE = "done"
    FAILED_RETRY = "failed_retry"
    BLOCKED = "blocked"


class BlockedReason(enum.Enum):
    """Spec 5. Without this enum every consumer ends up regex-parsing
    `last_error`, a free-text field that will drift."""

    CODE_FAILURE = "code_failure"
    COST = "cost"
    MERGE_CONFLICT = "merge_conflict"
    ENVIRONMENT = "environment"
    TIMEOUT = "timeout"
    FLAKY_UNRESOLVED = "flaky_unresolved"


class FailureType(enum.Enum):
    """Spec 6.2."""

    CODE_ERROR = "code_error"
    ENVIRONMENT_ERROR = "environment_error"
    TIMEOUT = "timeout"
    FLAKY = "flaky"


class FailureStage(enum.Enum):
    """Spec 9.3, plus `SECRETS` (Phase 6 deviation #12, see
    `docs/v3-implementation-state.md`'s cumulative deviations table): the
    spec's own enumerated list has no stage for the gate-side `gitleaks`
    backstop (spec 6.1) -- a secret in the diff is not a test-integrity
    violation, and folding it into `TEST_INTEGRITY` would make that value
    ambiguous for anyone querying `task_failures` later."""

    PROPOSE = "propose"
    IMPLEMENT = "implement"
    BUILD = "build"
    UNIT_TESTS = "unit_tests"
    E2E_TESTS = "e2e_tests"
    TEST_INTEGRITY = "test_integrity"
    SECRETS = "secrets"
    ADVERSARIAL_REVIEW = "adversarial_review"
    """v4 workflow changes: a fresh-session reviewer rejected the diff at
    `REVIEWING`, or the review harness call itself failed/timed out."""
    COMMIT = "commit"
    MERGE = "merge"


class NextAction(enum.Enum):
    """Spec 9.3 `task.failed` payload."""

    RETRY = "retry"
    BLOCK = "block"
    ESCALATE_CIRCUIT_BREAKER = "escalate_circuit_breaker"


class RunStatus(enum.Enum):
    """Spec 3.1."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"


class PauseReason(enum.Enum):
    """Spec 9.2 `run.paused`."""

    CIRCUIT_BREAKER = "circuit_breaker"
    QUOTA_EXHAUSTED_5H = "quota_exhausted_5h"
    QUOTA_EXHAUSTED_WEEKLY = "quota_exhausted_weekly"


class StopReason(enum.Enum):
    """Spec 3.1 plus the 7.1 weekly-cap-beyond-budget case and 9.5's
    pre-run disk check (Phase 9, migration 3)."""

    COMPLETED = "completed"
    MAX_TIME = "max_time"
    QUEUE_EMPTY = "queue_empty"
    COST_LIMIT_REACHED = "cost_limit_reached"
    MANUAL = "manual"
    QUOTA_EXHAUSTED_WEEKLY = "quota_exhausted_weekly"
    DISK_LOW = "disk_low"
    CRASHED = "crashed"
    """v5 improvements plan part 1: a `run_state` row still `running` at the
    next `cosmo run`'s startup reconciliation -- under Cosmo's strictly
    serial, single-process design (spec 5), only possible if the process
    that owned it died."""


class HeartbeatSource(enum.Enum):
    """Spec 4 / 9.2 `task.heartbeat`."""

    STREAM = "stream"
    FILE = "file"
    MTIME = "mtime"


class Severity(enum.Enum):
    """Spec 9.1 event envelope."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
