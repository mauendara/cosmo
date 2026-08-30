"""Result shapes for the validation gate (spec 9.2, 9.3).

Unit and e2e are always reported separately, never folded into one combined
boolean (spec 9.2) -- `GateResult` keeps `build`/`unit`/`e2e` as three
independent `StageResult | None` fields rather than a single pass/fail, and
`flaky_detected`/`quarantined_skipped` are top-level lists, not buried in a
per-stage detail blob, because `task.validation_result`'s payload (spec 9.2)
needs them at that level too.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from cosmo.store.enums import FailureStage, FailureType


@dataclass(frozen=True, slots=True)
class TestCounts:
    passed: int
    failed: int
    skipped: int

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.skipped


@dataclass(frozen=True, slots=True)
class FailingTest:
    """One failing test, kept structured so `error_detail` construction
    (spec 9.3) doesn't have to re-parse free text later."""

    test_id: str
    assertion: str | None
    stack_excerpt: str | None


@dataclass(frozen=True, slots=True)
class StageResult:
    stage: FailureStage  # BUILD | UNIT_TESTS | E2E_TESTS
    passed: bool
    duration_seconds: float
    counts: TestCounts | None
    failing_tests: list[FailingTest] = field(default_factory=list)
    error_summary: str | None = None
    error_detail: str | None = None
    log_path: Path | None = None
    # e2e only: Playwright trace/screenshot paths, never embedded binary
    # content (spec 9.3).
    artifact_paths: list[Path] = field(default_factory=list)
    # A stage that hit `gate.stage_timeout_seconds` is Docker/environment
    # trouble (spec 6.2's "Docker unresponsive" example), not a code defect
    # -- `run_validation_gate` uses this to pick `FailureType.ENVIRONMENT_ERROR`
    # over `CODE_ERROR` so a hung container never burns a task's retry budget.
    timed_out: bool = False


@dataclass(frozen=True, slots=True)
class DiffGateViolation:
    kind: str
    detail: str
    file: str | None


@dataclass(frozen=True, slots=True)
class DiffGateResult:
    passed: bool
    violations: list[DiffGateViolation] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class GateResult:
    task_id: str
    run_id: str | None
    passed: bool
    duration_seconds: float
    diff_gate: DiffGateResult
    build: StageResult | None
    unit: StageResult | None
    e2e: StageResult | None
    flaky_detected: list[str] = field(default_factory=list)
    quarantined_skipped: list[str] = field(default_factory=list)
    # Populated only when `passed` is False -- the spec 9.3 payload shape.
    failure_type: FailureType | None = None
    failure_stage: FailureStage | None = None
    error_summary: str | None = None
    error_detail: str | None = None
    files_touched: list[str] = field(default_factory=list)
