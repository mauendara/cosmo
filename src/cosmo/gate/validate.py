"""Ties `runner.run_validation_gate` to persisted state and events (spec
9.2, 9.3) -- mirrors `git.merge.merge_task` tying `attempt_merge_ladder` to
`StoreWriter`/`EventEmitter`.

Not wired to any CLI command yet, deliberately: `cosmo validate <worktree>`
(the CLI entry point the plan's exit criteria names) is a standalone
diagnostic, the same posture `cosmo harness probe` took in Phase 3 -- it
calls `runner.run_validation_gate` directly and never touches the store,
because a bare worktree path handed to `cosmo validate` need not correspond
to a queued task at all. `validate_task` here is the real seam for whichever
of Phase 7/8 becomes the state machine's actual `VALIDATING` handler, tested
now the same way Phase 5 tested `merge_task` well before Phase 7 existed to
call it.

`will_retry`/`next_action` below apply spec 6.2/6.3's literal rule
(`code_error` counts toward the attempt budget, `environment_error` doesn't)
but do not implement spec 6.5's circuit breaker -- Phase 8 owns deciding
when repeated `environment_error`s escalate to `ESCALATE_CIRCUIT_BREAKER`
instead of a plain retry. Until Phase 8 exists, an `environment_error` here
always reports `next_action=RETRY`.
"""

from __future__ import annotations

from pathlib import Path

from cosmo.config.model import CosmoConfig
from cosmo.events.emitter import EventEmitter
from cosmo.events.envelope import EventType
from cosmo.gate.runner import run_validation_gate
from cosmo.gate.types import GateResult, StageResult
from cosmo.store.enums import FailureType, NextAction, Severity
from cosmo.store.writer import StoreWriter


def _stage_payload(stage: StageResult | None) -> dict[str, object] | None:
    if stage is None:
        return None
    return {
        "passed": stage.passed,
        "duration_seconds": stage.duration_seconds,
        "passed_count": stage.counts.passed if stage.counts else None,
        "failed_count": stage.counts.failed if stage.counts else None,
        "skipped_count": stage.counts.skipped if stage.counts else None,
        "failing_tests": [ft.test_id for ft in stage.failing_tests],
    }


def validate_task(
    *,
    task_id: str,
    run_id: str | None,
    attempt_number: int,
    max_attempts: int,
    worktree_path: Path,
    base_branch: str,
    task_branch: str,
    allow_test_edits: bool,
    config: CosmoConfig,
    writer: StoreWriter,
    emitter: EventEmitter,
) -> GateResult:
    result = run_validation_gate(
        task_id=task_id,
        run_id=run_id,
        worktree_path=worktree_path,
        base_branch=base_branch,
        task_branch=task_branch,
        allow_test_edits=allow_test_edits,
        gate=config.gate,
        db_path=config.paths.db_path,
    )

    # Spec 9.2: unit and e2e reported separately, never one combined boolean.
    emitter.emit(
        event_type=EventType.TASK_VALIDATION_RESULT,
        severity=Severity.INFO if result.passed else Severity.WARNING,
        run_id=run_id,
        task_id=task_id,
        payload={
            "passed": result.passed,
            "duration_seconds": result.duration_seconds,
            "unit": _stage_payload(result.unit),
            "e2e": _stage_payload(result.e2e),
            "flaky_detected": result.flaky_detected,
            "quarantined_skipped": result.quarantined_skipped,
        },
    )

    if not result.passed:
        assert result.failure_type is not None
        assert result.failure_stage is not None

        if result.failure_type is FailureType.ENVIRONMENT_ERROR:
            will_retry = True
            next_action = NextAction.RETRY
        else:
            will_retry = attempt_number < max_attempts
            next_action = NextAction.RETRY if will_retry else NextAction.BLOCK

        writer.record_task_failure(
            task_id=task_id,
            run_id=run_id,
            attempt_number=attempt_number,
            failure_type=result.failure_type,
            failure_stage=result.failure_stage,
            error_summary=result.error_summary or "validation gate failed",
            error_detail=result.error_detail,
            files_touched=result.files_touched,
            will_retry=will_retry,
            next_action=next_action,
        )

    return result
