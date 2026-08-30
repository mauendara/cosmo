"""`run.breaker.CircuitBreaker` (spec 6.5, plan Phase 8 exit criteria):
trips on N distinct tasks BLOCKED consecutively or repeated
`environment_error` across distinct tasks; `merge_conflict`/
`flaky_unresolved` are excluded from the tally entirely; a reap failure
counts double."""

from __future__ import annotations

from cosmo.config.model import CircuitBreakerConfig
from cosmo.run.breaker import CircuitBreaker
from cosmo.store.enums import BlockedReason, PauseReason


def _config(
    *, consecutive_blocked_threshold: int = 3, environment_error_threshold: int = 3
) -> CircuitBreakerConfig:
    return CircuitBreakerConfig(
        consecutive_blocked_threshold=consecutive_blocked_threshold,
        environment_error_threshold=environment_error_threshold,
        reap_failure_weight=2,
    )


def test_trips_on_n_consecutive_distinct_blocked_tasks() -> None:
    breaker = CircuitBreaker(_config(consecutive_blocked_threshold=3))

    assert breaker.record_blocked(BlockedReason.CODE_FAILURE) is None
    assert breaker.record_blocked(BlockedReason.TIMEOUT) is None
    result = breaker.record_blocked(BlockedReason.CODE_FAILURE)

    assert result is PauseReason.CIRCUIT_BREAKER


def test_a_done_task_resets_the_consecutive_streak() -> None:
    breaker = CircuitBreaker(_config(consecutive_blocked_threshold=3))

    breaker.record_blocked(BlockedReason.CODE_FAILURE)
    breaker.record_blocked(BlockedReason.CODE_FAILURE)
    breaker.record_done()
    breaker.record_blocked(BlockedReason.CODE_FAILURE)
    result = breaker.record_blocked(BlockedReason.CODE_FAILURE)

    assert result is None  # only 2 consecutive since the reset, not 4


def test_merge_conflict_blocks_never_trip_the_breaker() -> None:
    breaker = CircuitBreaker(_config(consecutive_blocked_threshold=3))

    for _ in range(10):
        result = breaker.record_blocked(BlockedReason.MERGE_CONFLICT)

    assert result is None


def test_flaky_unresolved_blocks_never_trip_the_breaker() -> None:
    breaker = CircuitBreaker(_config(consecutive_blocked_threshold=3))

    for _ in range(10):
        result = breaker.record_blocked(BlockedReason.FLAKY_UNRESOLVED)

    assert result is None


def test_merge_conflict_blocks_do_not_reset_a_real_streak_either() -> None:
    # Excluded entirely -- neither adds to nor breaks the tally. Threshold
    # 2: two CODE_FAILUREs trip it regardless of the MERGE_CONFLICT
    # sandwiched between them.
    breaker = CircuitBreaker(_config(consecutive_blocked_threshold=2))

    breaker.record_blocked(BlockedReason.CODE_FAILURE)
    breaker.record_blocked(BlockedReason.MERGE_CONFLICT)
    breaker.record_blocked(BlockedReason.MERGE_CONFLICT)
    result = breaker.record_blocked(BlockedReason.CODE_FAILURE)

    assert result is PauseReason.CIRCUIT_BREAKER


def test_trips_on_repeated_environment_error_across_distinct_tasks() -> None:
    breaker = CircuitBreaker(
        _config(consecutive_blocked_threshold=100, environment_error_threshold=3)
    )

    assert breaker.record_blocked(BlockedReason.ENVIRONMENT, environment_error_weight=1) is None
    assert breaker.record_blocked(BlockedReason.ENVIRONMENT, environment_error_weight=1) is None
    result = breaker.record_blocked(BlockedReason.ENVIRONMENT, environment_error_weight=1)

    assert result is PauseReason.CIRCUIT_BREAKER


def test_a_reap_failure_counts_double() -> None:
    breaker = CircuitBreaker(
        _config(consecutive_blocked_threshold=100, environment_error_threshold=3)
    )

    assert breaker.record_blocked(BlockedReason.ENVIRONMENT, environment_error_weight=2) is None
    # One more distinct-task environment_error (weight 1) tips 2+1=3 >= 3,
    # not needing a third full-weight occurrence the way two ordinary
    # environment_errors alone would.
    result = breaker.record_blocked(BlockedReason.ENVIRONMENT, environment_error_weight=1)

    assert result is PauseReason.CIRCUIT_BREAKER
