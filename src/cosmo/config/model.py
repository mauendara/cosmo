"""Typed configuration model.

Every tunable the spec argues about lives here, in one validated place. The spec
states outright that several values are estimates to be retuned against real data
(section 3.3, Open Item 2); scattered constants make that a hunt, one model makes
it an edit. Invalid values fail at startup rather than at 3am mid-run.

This module is harness-agnostic. `HarnessConfig.name` is a plain string resolved
from configuration -- core code must never branch on its value.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HarnessConfig(_Strict):
    name: str = Field(min_length=1)
    permission_mode: str = Field(min_length=1)
    max_turns: int = Field(gt=0)


class TimeoutConfig(_Strict):
    proposing_wall: int = Field(gt=0)
    implementing_wall: int = Field(gt=0)
    implementing_stall: int = Field(gt=0)
    validating_wall: int = Field(gt=0)
    validating_stall: int = Field(gt=0)
    committing_wall: int = Field(gt=0)
    merging_wall: int = Field(gt=0)
    run_wall: int = Field(gt=0)
    kill_grace: int = Field(gt=0)

    @model_validator(mode="after")
    def _stall_below_wall(self) -> TimeoutConfig:
        # A stall timer that outlives its wall clock can never fire, silently
        # disabling the only protection against a hung harness (spec 3.3).
        for stall, wall, state in (
            (self.implementing_stall, self.implementing_wall, "implementing"),
            (self.validating_stall, self.validating_wall, "validating"),
        ):
            if stall >= wall:
                raise ValueError(
                    f"timeouts.{state}_stall ({stall}s) must be less than "
                    f"timeouts.{state}_wall ({wall}s), or it can never fire"
                )
        return self


class RetryConfig(_Strict):
    max_attempts: int = Field(gt=0)
    delay_min: int = Field(ge=0)
    delay_max: int = Field(ge=0)

    @model_validator(mode="after")
    def _delay_ordered(self) -> RetryConfig:
        if self.delay_min > self.delay_max:
            raise ValueError(
                f"retries.delay_min ({self.delay_min}s) exceeds "
                f"retries.delay_max ({self.delay_max}s)"
            )
        return self


class CircuitBreakerConfig(_Strict):
    consecutive_blocked_threshold: int = Field(gt=0)
    environment_error_threshold: int = Field(gt=0)
    reap_failure_weight: int = Field(gt=0)


class CostConfig(_Strict):
    """Spec 7.3. A ceiling of 0.0 means "no hard stop" -- the posture for a
    subscription-billed harness, where section 7.1 usage windows govern instead."""

    max_cost_per_run_usd: float = Field(ge=0.0)
    max_cost_per_task_usd: float = Field(ge=0.0)
    warn_at_fraction: float = Field(gt=0.0, le=1.0)

    @property
    def run_limit_enabled(self) -> bool:
        return self.max_cost_per_run_usd > 0.0

    @property
    def task_limit_enabled(self) -> bool:
        return self.max_cost_per_task_usd > 0.0


class GateConfig(_Strict):
    playwright_image: str = Field(min_length=1)
    playwright_npm_version: str = Field(min_length=1)
    shm_size: str = Field(min_length=1)
    ipc_host: bool

    @model_validator(mode="after")
    def _no_floating_tags(self) -> GateConfig:
        # Spec 1.1: a silent upstream update turns a green suite red overnight,
        # surfacing as a phantom regression the agent will try to "fix".
        if self.playwright_image.endswith(":latest") or ":" not in self.playwright_image:
            raise ValueError(
                f"gate.playwright_image must be pinned to an explicit tag, "
                f"got {self.playwright_image!r}"
            )
        return self


class KnowledgeConfig(_Strict):
    max_file_lines: int = Field(gt=0)


class DiskConfig(_Strict):
    min_free_gb: float = Field(gt=0.0)


class GitConfig(_Strict):
    base_branch: str = Field(min_length=1)


class PathsConfig(_Strict):
    """Where Cosmo keeps its own state.

    Defaults follow the XDG layout so a developer box needs no root. A droplet
    overrides these to /var/cosmo via its config file (spec 3.2 writes
    /var/cosmo/work); same code, different config per host.
    """

    data_dir: Path
    work_dir: Path
    log_dir: Path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "cosmo.db"


class CosmoConfig(_Strict):
    harness: HarnessConfig
    timeouts: TimeoutConfig
    retries: RetryConfig
    circuit_breaker: CircuitBreakerConfig
    cost: CostConfig
    gate: GateConfig
    knowledge: KnowledgeConfig
    disk: DiskConfig
    git: GitConfig
    paths: PathsConfig
