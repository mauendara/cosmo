"""`run.cost` (spec 7.3, plan Phase 8 exit criteria): run-level ceiling ->
stop, task-level ceiling -> block that one task, an 80% warning short of
the run ceiling, and both mechanisms inert (never trip) at their
`defaults.toml` value of 0.0."""

from __future__ import annotations

from cosmo.config.model import CostConfig
from cosmo.run.cost import check_run_cost, task_cost_ceiling_reached


def _config(
    *, max_cost_per_run_usd: float = 10.0, max_cost_per_task_usd: float = 5.0
) -> CostConfig:
    return CostConfig(
        max_cost_per_run_usd=max_cost_per_run_usd,
        max_cost_per_task_usd=max_cost_per_task_usd,
        warn_at_fraction=0.8,
    )


def test_run_cost_at_the_ceiling_stops() -> None:
    verdict = check_run_cost(10.0, _config())

    assert verdict.stop_run is True


def test_run_cost_below_the_warn_fraction_is_quiet() -> None:
    verdict = check_run_cost(1.0, _config())

    assert verdict.stop_run is False
    assert verdict.warn is False


def test_run_cost_at_the_warn_fraction_warns_but_does_not_stop() -> None:
    verdict = check_run_cost(8.0, _config())

    assert verdict.stop_run is False
    assert verdict.warn is True


def test_run_cost_ceiling_disabled_at_zero_never_stops_or_warns() -> None:
    verdict = check_run_cost(1_000_000.0, _config(max_cost_per_run_usd=0.0))

    assert verdict.stop_run is False
    assert verdict.warn is False


def test_task_cost_at_the_ceiling_blocks() -> None:
    assert task_cost_ceiling_reached(5.0, _config()) is True


def test_task_cost_below_the_ceiling_does_not_block() -> None:
    assert task_cost_ceiling_reached(4.99, _config()) is False


def test_task_cost_ceiling_disabled_at_zero_never_blocks() -> None:
    assert task_cost_ceiling_reached(1_000_000.0, _config(max_cost_per_task_usd=0.0)) is False
