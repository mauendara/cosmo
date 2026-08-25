"""Failure classification for `PROPOSING`/`IMPLEMENTING` (spec 6.2).

`VALIDATING` already gets a real classification from `gate.validate_task` --
`GateResult.failure_type`/`failure_stage`, built from real build/test/e2e
output (Phase 6). Nothing here re-derives that.

Neither adapter's `HarnessResult` carries a structured error type, only
`success: bool` and `exit_code: int | None` -- confirmed against both
`FakeHarnessAdapter` (whose `ScriptedCall` docstring says outcome-kind
nuance is deliberately not modeled there, exactly because this
classification belongs to Phase 7) and the real Claude adapter (`success =
exit_code == 0`, "spec 2.3: zero vs non-zero exit only, never a specific
value"). A `HarnessResult.success=False` that isn't a state-machine-detected
timeout is therefore always classified `environment_error`, never
`code_error`: code-quality problems are only observable once the gate
actually builds/tests the work at `VALIDATING` -- a harness process that
didn't even complete has no gate-testable artifact to blame on the code.
Recorded as a Phase 7 decision in `docs/v3-implementation-state.md`.
"""

from __future__ import annotations

from cosmo.harness.base import HarnessResult
from cosmo.store.enums import FailureStage, FailureType
from cosmo.task.types import FailureClassification


def classify_harness_failure(
    result: HarnessResult | None,
    *,
    stage: FailureStage,
    timed_out: bool,
) -> FailureClassification:
    """`result` is `None` when the background thread never got a chance to
    produce one at all (e.g. the process hung past `kill_grace` with no
    return) -- still classified the same as any other timeout."""
    if timed_out:
        return FailureClassification(
            failure_type=FailureType.TIMEOUT,
            failure_stage=stage,
            error_summary=f"{stage.value} timed out",
            error_detail=None,
        )

    assert result is not None and not result.success
    return FailureClassification(
        failure_type=FailureType.ENVIRONMENT_ERROR,
        failure_stage=stage,
        error_summary=result.output_summary or f"{stage.value} failed (exit {result.exit_code})",
        error_detail=None,
    )
