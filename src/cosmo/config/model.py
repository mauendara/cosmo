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

    # Spec 1's fixed target stack (Java+Spring backend, Vite+React frontend,
    # monorepo) -- build/unit-test images for the two sides of every repo
    # Cosmo operates on. Only the playwright image gets spec 1.1's explicit
    # "never latest" validator below; these two are pinned in defaults.toml
    # by the same discipline but the spec doesn't name them, so a bad
    # override here is a config mistake, not a guardrail violation.
    backend_image: str = Field(min_length=1)
    backend_dir: str = Field(min_length=1)
    frontend_image: str = Field(min_length=1)
    frontend_dir: str = Field(min_length=1)

    # Spec 1.2: one docker-run budget per serial stage (build, unit, e2e).
    # Not the same as timeouts.validating_wall (that's the whole-task clock
    # Phase 7 owns) -- this is what keeps a single hung container from
    # blocking `cosmo validate` forever when nothing else is watching it.
    stage_timeout_seconds: int = Field(gt=0)

    # Spec 6.1 layer 2 (diff gate / test-integrity detection).
    diff_gate_test_path_patterns: list[str] = Field(min_length=1)
    diff_gate_skip_annotations: list[str] = Field(min_length=1)
    diff_gate_loc_drop_threshold: int = Field(gt=0)

    # Spec 6.4 (flaky handling).
    flaky_rerun_limit: int = Field(gt=0)
    flaky_quarantine_candidate_threshold: int = Field(gt=0)
    # None means "use the file shipped in Cosmo's own package" (gate/data/),
    # the same "computed unless overridden" posture PathsConfig takes with
    # XDG paths -- tests point these at a tmp_path copy instead.
    quarantine_file: Path | None = None
    quarantine_candidates_file: Path | None = None

    # Spec 9.3: error_detail must be model-consumable, not archival.
    error_detail_max_chars: int = Field(gt=0)

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


class ProgressConfig(_Strict):
    """Spec 4: `watchdog`/inotify on the change's `tasks.md`, polling
    fallback at 5-10s. `poll_interval_seconds` is that fallback interval --
    also the only interval used at all when the harness adapter reports
    native progress instead of a file to watch (`HarnessCapabilities.
    reports_native_progress`), since there is nothing to inotify-watch in
    that case."""

    poll_interval_seconds: int = Field(gt=0)


class QuotaConfig(_Strict):
    """Spec 7.1/7.2. Detection order: a harness's primary structured
    rate-limit signal (`HarnessResult.quota_window`, e.g. the Claude
    adapter's `harness.claude.stream.extract_quota_signal`), the terminal
    result's error subtype (secondary), then a wall-clock heuristic (last
    resort, must never be reported as confirmed).

    `result_error_subtypes` has no real captured value behind it yet -- no
    real `claude -p` run in this project has ever actually exhausted a quota
    window (see `docs/v3-implementation-state.md`'s Phase 8 section). It is
    configurable specifically so it can be corrected the day a real one is
    captured, the same posture the spec's own timeout defaults take pending
    real p95 data (Open Item 2)."""

    result_error_subtypes: list[str] = Field(min_length=1)
    heuristic_consecutive_threshold: int = Field(gt=0)
    heuristic_max_duration_seconds: float = Field(gt=0.0)
    default_5h_resume_delay_seconds: int = Field(gt=0)


class DiskConfig(_Strict):
    min_free_gb: float = Field(gt=0.0)


class LogRetentionConfig(_Strict):
    """Spec 9.5: per-task `raw_log_path` rotation, keyed off the task's
    terminal status -- a `DONE` task's harness logs are worth less, for
    less time, than a `BLOCKED` one's (still under investigation)."""

    done_days: int = Field(gt=0)
    blocked_days: int = Field(gt=0)


class GitConfig(_Strict):
    base_branch: str = Field(min_length=1)
    # Identity for commits Cosmo creates itself (merge commits, rebase
    # replays) -- spec 3.4's merge ladder needs one regardless of whether
    # this host has a global git identity configured (it may not; Phase 5
    # found this by hand). Passed as `-c user.name=...` per invocation, never
    # written to global git config.
    commit_author_name: str = Field(min_length=1)
    commit_author_email: str = Field(min_length=1)


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
    progress: ProgressConfig
    quota: QuotaConfig
    disk: DiskConfig
    log_retention: LogRetentionConfig
    git: GitConfig
    paths: PathsConfig
