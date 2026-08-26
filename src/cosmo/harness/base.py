"""The adapter interface every harness must implement (spec 2.2)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from cosmo.checks import CheckResult
from cosmo.config import CosmoConfig


@dataclass(frozen=True, slots=True)
class HarnessCapabilities:
    """Spec 2.2. Each flag names a fallback Cosmo must take when it is false."""

    reports_native_progress: bool
    """False -> fall back to file-watching tasks.md."""

    supports_retry_context: bool
    """False -> compose a synthetic retry prompt instead."""

    has_internal_timeout: bool
    """False -> Cosmo imposes an external timeout."""

    reports_native_cost: bool
    """False -> estimate from token counts, or disable the cost hard stop."""

    supports_gating: bool
    """False -> post-hoc diff inspection only (spec 6.1 layer 3), strictly weaker."""

    supports_structured_stream: bool
    """False -> fall back to file-mtime liveness; the stall timeout is the only guard."""


@dataclass(frozen=True, slots=True)
class HarnessResult:
    """The uniform result object every adapter method returns (spec 2.2).

    `quota_window`/`quota_resets_at`/`tool_call_count` are Phase 8 additions,
    appended with defaults so every existing keyword construction stays
    valid. Harness-agnostic by design (spec 2's own discipline: core code
    never branches on which harness produced a result) -- an adapter with no
    rate-limit-shaped wire signal at all (`FakeHarnessAdapter`, any future
    non-Claude adapter) simply never sets `quota_window`, and Phase 8's
    quota detection degrades to its secondary/tertiary signals for it, the
    same as spec 7.2 already prescribes for a harness with no primary
    signal."""

    success: bool
    output_summary: str
    raw_log_path: Path | None
    files_changed: list[str]
    duration_seconds: float
    total_cost_usd: float | None
    exit_code: int | None
    session_id: str | None
    quota_window: str | None = None
    """Spec 7.1's window this result's rate-limit signal (if any) names --
    `"five_hour"` or `"weekly"` -- from the harness's primary structured
    quota signal, when it has one (spec 4/7.2).
    `None` means no such signal was observed on this call."""
    quota_resets_at: str | None = None
    """UTC ISO 8601 timestamp the named window resets at, when the wire
    signal carries one. `None` if unknown even though `quota_window` is set
    (e.g. the `system/api_retry` shape, spec 4's own capture note)."""
    tool_call_count: int = 0
    """Spec 7.2's wall-clock heuristic needs "no tool calls executed";
    0 for any adapter with no stream to count from."""


class HarnessAdapter(ABC):
    """Base adapter.

    `name` and `capabilities` are class-level declarations so the registry can
    report them without instantiating or running anything.
    """

    name: ClassVar[str]
    capabilities: ClassVar[HarnessCapabilities]

    def __init__(self, config: CosmoConfig, *, cwd: Path | None = None) -> None:
        self.config = config
        # Phase 5 introduces real worktree lifecycle; until then, every
        # subprocess-based adapter still needs *some* working directory to
        # launch its child in, and `cancel()`'s orphan sweep (spec 2.4 step 4)
        # needs *some* path to check for surviving holders. A constructor
        # argument is the deliberately minimal stand-in -- see Phase 3 state
        # doc. Harness-agnostic (every adapter needs a cwd), so it lives here
        # rather than being invented per-adapter.
        self.cwd = cwd if cwd is not None else Path.cwd()

    @abstractmethod
    def preflight(self) -> list[CheckResult]:
        """Environmental preconditions specific to this harness.

        Extends the spec's 2.2 interface. It exists so `cosmo doctor` can report
        harness-specific conditions without Cosmo's core knowing what they are.
        Must be cheap and side-effect free: no subprocess work beyond a PATH
        lookup, no network calls.
        """

    @abstractmethod
    def probe(self, prompt: str) -> HarnessResult:
        """Run a single raw prompt through the harness and return the uniform
        result. A second extension to spec 2.2 (see `preflight()` above):
        `cosmo harness probe` (plan Phase 3 exit criterion) needs a
        harness-agnostic smoke-test entry point that doesn't presuppose an
        OpenSpec change on disk the way `propose`/`implement` do.
        """

    @abstractmethod
    def propose(self, spec_path: Path, context: dict[str, Any]) -> HarnessResult: ...

    @abstractmethod
    def implement(
        self,
        task_id: str,
        spec_path: Path,
        retry_context: str | None = None,
    ) -> HarnessResult: ...

    @abstractmethod
    def review(self, task_id: str, spec_path: Path, base_branch: str) -> HarnessResult:
        """v4 workflow changes (`docs/v4-changes-to-workflow-plan.md`): a
        genuinely fresh, separate call -- no session resumption, no
        `retry_context` -- so the review is real rather than the same
        session grading its own work. Given only `spec_path` (the change's
        own spec/tasks.md) and `base_branch` (to diff the worktree's current
        `HEAD` against, e.g. `git diff {base_branch}...HEAD`); the reviewer
        has no other memory of how the diff came to exist.

        Verdict delivery is deliberately not a field on `HarnessResult`:
        spec 4's "prose parsing is prohibited as a signal" discipline
        (`harness.claude.stream`'s own docstring) rules out inspecting the
        session's free-text final message, and `HarnessResult` otherwise
        carries no harness-agnostic slot for it. Instead the reviewer writes
        a small structured file to the worktree (`task.review`'s own
        contract) -- the same "watch a file the harness writes, not the
        stream" shape `HarnessCapabilities.reports_native_progress=False`
        already uses for `tasks.md` -- and `task.machine._do_reviewing`
        reads it back after this call returns, harness-agnostically.
        """

    @abstractmethod
    def get_progress(self, task_id: str) -> tuple[int, int]:
        """Completed and total subtasks -- never a precomputed percent.

        Spec 4: the total is not constant and progress can legitimately move
        backwards, so numerator and denominator are stored separately.
        """

    @abstractmethod
    def cancel(self, task_id: str) -> None:
        """Terminate the run AND its entire process group (spec 2.4)."""


# Deviation from spec 2.2, recorded deliberately.
#
# The spec lists `validate(task_id)` among the adapter's interface methods, while
# also stating that validation "bypasses the LLM harness entirely (direct Docker
# invocation)". Those two statements conflict: a method that never touches the
# harness does not belong on the harness adapter. Validation is therefore owned by
# `cosmo.gate` (Phase 6), not by this interface. Folding this into a future spec
# revision is tracked in docs/v3-implementation-plan.md.
