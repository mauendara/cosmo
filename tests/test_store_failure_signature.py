"""`store.failure_signature` (v5 improvements plan part 5, Class 1):
deterministic substring matching, no model call."""

from __future__ import annotations

from cosmo.store.failure_signature import classify_failure_signature, detect_repeat_block
from cosmo.store.reader import TaskFailureRow


def test_none_error_detail_is_unmatched() -> None:
    assert classify_failure_signature(None) is None


def test_empty_error_detail_is_unmatched() -> None:
    assert classify_failure_signature("") is None


def test_missing_lockfile_shape() -> None:
    detail = "npm ERR! The `npm ci` command can only install with an existing package-lock.json"
    assert classify_failure_signature(detail) == "missing_lockfile"


def test_node_engine_mismatch_shape() -> None:
    detail = 'npm WARN EBADENGINE Unsupported engine {"package": "foo@2.0.0"}'
    assert classify_failure_signature(detail) == "node_engine_mismatch"


def test_enoent_node_modules_shape() -> None:
    detail = "Error: ENOENT: no such file or directory, open 'node_modules/.bin/vite'"
    assert classify_failure_signature(detail) == "enoent_node_modules"


def test_unrelated_error_stays_unmatched() -> None:
    assert classify_failure_signature("AssertionError: expected 1 but was 2") is None


def test_secrets_stray_backup_artifact_shape() -> None:
    detail = (
        "/work/scaffold-app/frontend/node_modules_old/@babel/helpers/lib/"
        "applyDecs2305.js.map:1 [generic-api-key]"
    )
    assert classify_failure_signature(detail) == "secrets_stray_backup_artifact"


def test_generic_api_key_alone_without_a_backup_dir_stays_unmatched() -> None:
    # A real secret could legitimately live at a path with neither "_old/"
    # nor be a false positive -- the pairing is deliberate, not incidental.
    detail = "/work/scaffold-app/frontend/src/config.ts:12 [generic-api-key]"
    assert classify_failure_signature(detail) is None


def _failure(
    *,
    id: int,
    next_action: str = "block",
    failure_stage: str = "implement",
    error_summary: str = "implement timed out",
    failure_signature: str | None = None,
    timestamp: str = "t0",
) -> TaskFailureRow:
    return TaskFailureRow(
        id=id,
        task_id="scaffold-app",
        run_id=None,
        attempt_number=0,
        failure_type="timeout",
        failure_stage=failure_stage,
        error_summary=error_summary,
        error_detail=None,
        files_touched=[],
        will_retry=(next_action == "retry"),
        next_action=next_action,
        timestamp=timestamp,
        failure_signature=failure_signature,
    )


def test_detect_repeat_block_with_no_failures_is_none() -> None:
    assert detect_repeat_block([], threshold=2) is None


def test_detect_repeat_block_with_no_terminal_blocks_is_none() -> None:
    failures = [_failure(id=1, next_action="retry"), _failure(id=2, next_action="retry")]
    assert detect_repeat_block(failures, threshold=2) is None


def test_detect_repeat_block_at_or_under_threshold_is_none() -> None:
    # Two identical terminal blocks, threshold 2: still within normal
    # retry-budget noise, not yet a repeat-block report.
    failures = [_failure(id=1), _failure(id=2)]
    assert detect_repeat_block(failures, threshold=2) is None


def test_detect_repeat_block_over_threshold_fires() -> None:
    failures = [_failure(id=1), _failure(id=2), _failure(id=3)]
    result = detect_repeat_block(failures, threshold=2)
    assert result is not None
    assert len(result.occurrences) == 3
    assert result.class_key == "implement:implement timed out"
    assert result.is_deterministic is False


def test_detect_repeat_block_prefers_the_real_signature_over_the_fallback() -> None:
    failures = [
        _failure(id=1, failure_stage="build", failure_signature="missing_lockfile"),
        _failure(id=2, failure_stage="build", failure_signature="missing_lockfile"),
        _failure(id=3, failure_stage="build", failure_signature="missing_lockfile"),
    ]
    result = detect_repeat_block(failures, threshold=2)
    assert result is not None
    assert result.class_key == "missing_lockfile"
    assert result.is_deterministic is True


def test_detect_repeat_block_ignores_non_terminal_retry_rows() -> None:
    failures = [
        _failure(id=1, next_action="retry"),
        _failure(id=2),
        _failure(id=3, next_action="retry"),
        _failure(id=4),
        _failure(id=5),
    ]
    result = detect_repeat_block(failures, threshold=2)
    assert result is not None
    assert len(result.occurrences) == 3  # only the 3 next_action="block" rows


def test_detect_repeat_block_does_not_mix_different_reasons() -> None:
    failures = [
        _failure(id=1, error_summary="frontend build failed", failure_stage="build"),
        _failure(id=2, error_summary="implement timed out", failure_stage="implement"),
        _failure(id=3, error_summary="1 e2e test(s) failed", failure_stage="e2e_tests"),
    ]
    # 3 blocks total, but each a different class key -- none recurs past
    # threshold on its own.
    assert detect_repeat_block(failures, threshold=2) is None


def test_playwright_image_version_mismatch_shape() -> None:
    detail = (
        "Looks like Playwright Test or Playwright was just updated to 1.49.0.\n"
        "Please update docker image as well.\n"
        " -  current: mcr.microsoft.com/playwright:v1.50.0-noble\n"
        " - required: mcr.microsoft.com/playwright:v1.49.0-noble\n"
    )
    assert classify_failure_signature(detail) == "playwright_image_version_mismatch"
