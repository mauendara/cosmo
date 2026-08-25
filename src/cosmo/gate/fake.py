"""`FakeGate`: scriptable gate outcomes for Phase 7/8's tests (plan Phase 6
build item 10) -- the same shape `FakeHarnessAdapter` (`cosmo.harness.fake`)
gives Phase 3+ callers, so a task-state-machine test never needs a real
Docker daemon to exercise `VALIDATING`.

Also the natural candidate for `cosmo.git.merge.GateRerun`
(`Callable[[], bool]`): `FakeGate.as_gate_rerun(task_id)` returns exactly
that shape by closing over a task_id and unwrapping `GateResult.passed`. The
real `run_validation_gate` does *not* conform to `GateRerun` directly -- its
natural signature takes `worktree_path`/`base_branch`/`task_branch`/etc. and
returns a full `GateResult`, which is far more than the ladder's merge-retry
seam needs. Recorded as a Phase 6 spec-deviation-shaped note rather than
reshaping either signature to fit the other: whichever of Phase 7/8 becomes
the ladder's real caller wraps `run_validation_gate` in a closure the same
way `FakeGate.as_gate_rerun` does here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from cosmo.gate.types import DiffGateResult, GateResult
from cosmo.store.enums import FailureStage, FailureType


@dataclass(slots=True)
class ScriptedGateResult:
    """One scripted `GateResult`, built from a short description rather than
    every field -- most tests only care about pass/fail plus which stage/type
    a failure attributes to."""

    passed: bool
    failure_type: FailureType | None = None
    failure_stage: FailureStage | None = None
    error_summary: str | None = None
    error_detail: str | None = None
    flaky_detected: list[str] = field(default_factory=list)
    quarantined_skipped: list[str] = field(default_factory=list)
    duration_seconds: float = 0.1


class FakeGate:
    def __init__(self, script: ScriptedGateResult | list[ScriptedGateResult] | None = None) -> None:
        if script is None:
            script = ScriptedGateResult(passed=True)
        self._script: list[ScriptedGateResult] = (
            [script] if isinstance(script, ScriptedGateResult) else list(script)
        )
        self._call_index = 0
        # Audit trail a test can assert against.
        self.calls: list[str] = []

    def validate(self, task_id: str) -> GateResult:
        self.calls.append(task_id)
        scripted = self._script[min(self._call_index, len(self._script) - 1)]
        self._call_index += 1
        return GateResult(
            task_id=task_id,
            run_id=None,
            passed=scripted.passed,
            duration_seconds=scripted.duration_seconds,
            diff_gate=DiffGateResult(passed=True),
            build=None,
            unit=None,
            e2e=None,
            flaky_detected=list(scripted.flaky_detected),
            quarantined_skipped=list(scripted.quarantined_skipped),
            failure_type=scripted.failure_type,
            failure_stage=scripted.failure_stage,
            error_summary=scripted.error_summary,
            error_detail=scripted.error_detail,
        )

    def as_gate_rerun(self, task_id: str) -> Callable[[], bool]:
        """Satisfies `cosmo.git.merge.GateRerun` for a specific task."""
        return lambda: self.validate(task_id).passed
