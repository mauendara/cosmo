"""`FakeHarnessAdapter`: scriptable outcomes for every later phase's test
(plan Phase 3).

Real `claude -p` is never invoked from a unit test -- the same "fake the
external process, test the mechanics" stance Phase 2 took with `docker`
(`tests/fixtures/fake_docker.sh`) applies here, promoted to a first-class
adapter under `cosmo.harness` (rather than a test fixture) precisely so every
later phase's state-machine tests can target it directly instead of
reimplementing a double of their own.
"""

from __future__ import annotations

import enum
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from cosmo.checks import CheckResult, ok
from cosmo.config import CosmoConfig
from cosmo.harness.base import HarnessAdapter, HarnessCapabilities, HarnessResult


class FakeOutcome(enum.Enum):
    SUCCESS = "success"
    CODE_FAILURE = "code_failure"
    ENVIRONMENT_FAILURE = "environment_failure"
    HANG = "hang"
    RATE_LIMIT = "rate_limit"
    COST_OVERRUN = "cost_overrun"


@dataclass(slots=True)
class ScriptedCall:
    """One scripted response. Failure-kind nuance (which `FailureType`/
    `FailureStage` this corresponds to) is deliberately not modeled here --
    that classification belongs to Phase 6/7, which have direct access to
    the script anyway. This only fills in spec 2.2's uniform result object."""

    outcome: FakeOutcome
    output_summary: str = ""
    files_changed: list[str] = field(default_factory=list)
    duration_seconds: float = 0.1
    total_cost_usd: float | None = None
    exit_code: int | None = None
    session_id: str | None = "fake-session"
    tool_call_count: int = 0
    # Only meaningful for FakeOutcome.RATE_LIMIT (Phase 8): lets a test
    # script exactly which spec 7.1 window and reset ETA the scripted call
    # reports, the fake-adapter equivalent of `extract_quota_signal`'s
    # output for the real Claude adapter.
    quota_window: str | None = None
    quota_resets_at: str | None = None


class FakeHarnessAdapter(HarnessAdapter):
    name: ClassVar[str] = "fake"

    capabilities: ClassVar[HarnessCapabilities] = HarnessCapabilities(
        reports_native_progress=True,
        supports_retry_context=True,
        has_internal_timeout=False,
        reports_native_cost=True,
        supports_gating=False,
        supports_structured_stream=False,
    )

    def __init__(
        self,
        config: CosmoConfig,
        *,
        cwd: Path | None = None,
        script: ScriptedCall | Sequence[ScriptedCall] | None = None,
    ) -> None:
        super().__init__(config, cwd=cwd)
        if script is None:
            script = ScriptedCall(outcome=FakeOutcome.SUCCESS)
        if isinstance(script, ScriptedCall):
            self._script: list[ScriptedCall] = [script]
        else:
            self._script = list(script)
        self._call_index = 0
        self._progress: dict[str, tuple[int, int]] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        # Audit trail a test can assert against: (method, task_id, retry_context).
        self.calls: list[tuple[str, str, str | None]] = []

    def preflight(self) -> list[CheckResult]:
        return [ok("fake harness", "always ready")]

    def probe(self, prompt: str) -> HarnessResult:
        return self._run("probe", "probe", None)

    def propose(self, spec_path: Path, context: dict[str, Any]) -> HarnessResult:
        task_id = str(context.get("task_id", spec_path.stem))
        return self._run("propose", task_id, None)

    def implement(
        self,
        task_id: str,
        spec_path: Path,
        retry_context: str | None = None,
    ) -> HarnessResult:
        return self._run("implement", task_id, retry_context)

    def review(self, task_id: str, spec_path: Path, base_branch: str) -> HarnessResult:
        # The verdict itself is a file `task.review.read_review_verdict`
        # reads back from the worktree (`HarnessAdapter.review`'s own
        # docstring) -- a script here only needs to control whether this
        # call *completed* (`FakeOutcome`'s usual environment-health
        # meaning), same as `propose`/`implement`. A test wanting a specific
        # verdict writes `task.review.review_result_path(worktree)` directly.
        return self._run("review", task_id, None)

    def get_progress(self, task_id: str) -> tuple[int, int]:
        return self._progress.get(task_id, (0, 0))

    def set_progress(self, task_id: str, completed: int, total: int) -> None:
        """Test helper -- not part of the adapter interface."""
        self._progress[task_id] = (completed, total)

    def cancel(self, task_id: str) -> None:
        event = self._cancel_events.get(task_id)
        if event is not None:
            event.set()

    def _next_script(self) -> ScriptedCall:
        call = self._script[min(self._call_index, len(self._script) - 1)]
        self._call_index += 1
        return call

    def _run(self, method: str, task_id: str, retry_context: str | None) -> HarnessResult:
        self.calls.append((method, task_id, retry_context))
        call = self._next_script()

        if call.outcome is FakeOutcome.HANG:
            # Simulates a stuck harness: blocks until `cancel(task_id)` is
            # called, exactly like a real `claude -p` that only responds to
            # SIGTERM/SIGKILL on its process group (spec 2.4) -- not a
            # bare-fixed sleep, which would race whatever the test is timing.
            event = self._cancel_events.setdefault(task_id, threading.Event())
            event.wait()
            return HarnessResult(
                success=False,
                output_summary="cancelled while hung",
                raw_log_path=None,
                files_changed=[],
                duration_seconds=call.duration_seconds,
                total_cost_usd=None,
                exit_code=None,
                session_id=call.session_id,
            )

        # COST_OVERRUN is a normal, successful call that happens to report a
        # high total_cost_usd (spec 7.3: it's the cumulative run/task total
        # that becomes a problem, not any single call failing). RATE_LIMIT
        # is a genuinely failed call, matching this module's own quota
        # design: a rate-limit signal is only actionable when the call it
        # rode in on did not succeed (see `cosmo.run.quota`).
        success = call.outcome in (FakeOutcome.SUCCESS, FakeOutcome.COST_OVERRUN)
        exit_code = call.exit_code if call.exit_code is not None else (0 if success else 1)
        quota_window = call.quota_window
        if call.outcome is FakeOutcome.RATE_LIMIT and quota_window is None:
            quota_window = "five_hour"
        return HarnessResult(
            success=success,
            output_summary=call.output_summary or call.outcome.value,
            raw_log_path=None,
            files_changed=list(call.files_changed),
            duration_seconds=call.duration_seconds,
            total_cost_usd=call.total_cost_usd,
            exit_code=exit_code,
            session_id=call.session_id,
            quota_window=quota_window,
            quota_resets_at=call.quota_resets_at,
            tool_call_count=call.tool_call_count,
        )
