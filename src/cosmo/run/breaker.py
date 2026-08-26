"""Spec 6.5's global circuit breaker: trips the run to `PAUSED` when N
*distinct* tasks land `BLOCKED` consecutively, or when repeated
`environment_error`s accumulate across distinct tasks. `merge_conflict` and
`flaky_unresolved` blocks are excluded from the tally entirely -- spec 3.4's
own framing: they signal queue contention over shared files, not a broken
environment, so they neither add to nor reset the consecutive-blocked
streak. A process-reap failure counts double (`proc.reap`'s own
`circuit_breaker_weight` payload, spec 2.4 step 6): "a leaked process pool
poisons every subsequent task, so the breaker should trip fast."

Deliberately in-memory and evaluated once per task's *terminal* outcome
(`DONE`/`BLOCKED`) -- spec 6.5 is phrased in terms of task outcomes ("N
distinct tasks... land in BLOCKED"), so per-task granularity is
spec-faithful, not a shortcut taken for convenience. A tripped breaker's
`PAUSED` state is what a restart needs to see (the persisted `run_state`
row, spec 3.1) -- resuming requires manual intervention regardless, so
losing this object's in-memory tally on a restart costs nothing real.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cosmo.config.model import CircuitBreakerConfig
from cosmo.store.enums import BlockedReason, PauseReason

_EXCLUDED_FROM_TALLY = frozenset({BlockedReason.MERGE_CONFLICT, BlockedReason.FLAKY_UNRESOLVED})


@dataclass(slots=True)
class CircuitBreaker:
    config: CircuitBreakerConfig
    _consecutive_blocked: int = field(default=0, init=False)
    _environment_error_weight: int = field(default=0, init=False)

    def record_done(self) -> None:
        """A task reaching `DONE` breaks any streak of consecutive blocks
        -- "consecutive" is only meaningful relative to intervening
        successes."""
        self._consecutive_blocked = 0

    def record_blocked(
        self, reason: BlockedReason, *, environment_error_weight: int = 0
    ) -> PauseReason | None:
        """Call once per task's terminal `BLOCKED` outcome.
        `environment_error_weight` is the run loop's own precomputed tally
        for *this* task: `0` if it never hit an `environment_error` during
        this run, `1` if it did, or `config.reap_failure_weight` instead of
        `1` if a process-reap failure occurred for it. Returns the
        `PauseReason` if this observation trips the breaker, else `None`.
        """
        if reason not in _EXCLUDED_FROM_TALLY:
            self._consecutive_blocked += 1
        self._environment_error_weight += environment_error_weight

        if (
            self._consecutive_blocked >= self.config.consecutive_blocked_threshold
            or self._environment_error_weight >= self.config.environment_error_threshold
        ):
            return PauseReason.CIRCUIT_BREAKER
        return None
