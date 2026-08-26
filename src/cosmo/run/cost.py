"""Spec 7.3's dollar-denominated cost ceilings. Inert for the v1
subscription-billed Claude adapter -- `CostConfig.max_cost_per_run_usd`/
`max_cost_per_task_usd` both default to `0.0` in `defaults.toml`, meaning
"no hard stop" (`CostConfig.run_limit_enabled`/`task_limit_enabled`).
Implemented in full anyway so a future per-token adapter needs no new
mechanism, only non-zero config.
"""

from __future__ import annotations

from dataclasses import dataclass

from cosmo.config.model import CostConfig


@dataclass(frozen=True, slots=True)
class CostVerdict:
    stop_run: bool
    """`max_cost_per_run_usd` reached -- the whole run should `STOPPED`
    (spec 7.3: "nothing to auto-recover from")."""
    warn: bool
    """`warn_at_fraction` of `max_cost_per_run_usd` reached, run not yet
    stopped -- spec 7.3's 80% warning event."""


def check_run_cost(total_run_cost_usd: float, config: CostConfig) -> CostVerdict:
    stop = config.run_limit_enabled and total_run_cost_usd >= config.max_cost_per_run_usd
    warn = (
        config.run_limit_enabled
        and not stop
        and total_run_cost_usd >= config.max_cost_per_run_usd * config.warn_at_fraction
    )
    return CostVerdict(stop_run=stop, warn=warn)


def task_cost_ceiling_reached(total_task_cost_usd: float, config: CostConfig) -> bool:
    """`True` -> this task should `BLOCKED` now (`blocked_reason=cost`);
    the queue continues with the next task (spec 7.3)."""
    return config.task_limit_enabled and total_task_cost_usd >= config.max_cost_per_task_usd
