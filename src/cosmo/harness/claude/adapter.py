"""Claude Code CLI adapter (spec 2.3).

This module (with `stream.py` beside it) is the ONLY place in Cosmo that may
name Claude-specific binaries, environment variables, or flags -- enforced by
`tests/test_harness_boundary.py`.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, ClassVar

from cosmo.checks import CheckResult, check_executable, fail, ok, warn
from cosmo.config import CosmoConfig
from cosmo.events import EventEmitter
from cosmo.harness.base import HarnessAdapter, HarnessCapabilities, HarnessResult
from cosmo.harness.claude.stream import ClassifiedEvent, StreamReader
from cosmo.proc import ManagedProcess, cancel_and_reap

BINARY = "claude"

# Spec 2.3: setting this silently switches billing from the Pro/Max subscription
# to per-token API rates. Unattended overnight runs make that an expensive
# surprise, so it is a hard failure rather than a warning.
BILLING_ENV_VAR = "ANTHROPIC_API_KEY"

# Spec 2.3: never used. The droplet holds SSH keys and real credentials.
FORBIDDEN_PERMISSION_MODES = frozenset({"bypassPermissions"})

SUPPORTED_PERMISSION_MODES = frozenset({"dontAsk", "auto"})

# Spec 9.4: enable Claude Code's native OTel export, but keep content logging
# off explicitly rather than trusting the CLI's own default -- prompts and
# file contents in a telemetry backend are a data-exfiltration path for a
# private codebase. `OTEL_LOG_USER_PROMPTS` is what gates that content.
TELEMETRY_ENV = {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_LOG_USER_PROMPTS": "0",
}

# Consumed by the test-path guard hook (templates/harness/claude/hooks/
# test_path_guard.py) to read `task_queue.allow_test_edits` for the running
# task -- a hook is a separate OS process from Cosmo's own, so it has no
# other way to ask Cosmo's state (spec 2.5 / plan Phase 4 handoff). Not
# Claude-CLI flags themselves, but this adapter is where the child's
# environment is assembled, so this is where they're set.
TASK_ID_ENV_VAR = "COSMO_TASK_ID"
DB_PATH_ENV_VAR = "COSMO_DB_PATH"


class ClaudeCodeAdapter(HarnessAdapter):
    name: ClassVar[str] = "claude"

    capabilities: ClassVar[HarnessCapabilities] = HarnessCapabilities(
        reports_native_progress=False,  # progress comes from watching tasks.md (spec 4)
        supports_retry_context=True,
        has_internal_timeout=False,  # Cosmo imposes the wall clock (spec 3.3)
        reports_native_cost=True,  # total_cost_usd on the terminal result object
        supports_gating=True,  # PreToolUse hooks (spec 2.5)
        supports_structured_stream=True,  # --output-format stream-json (spec 4)
    )

    def __init__(
        self,
        config: CosmoConfig,
        *,
        cwd: Path | None = None,
        binary: str = BINARY,
        run_id: str | None = None,
        emitter: EventEmitter | None = None,
    ) -> None:
        super().__init__(config, cwd=cwd)
        self._binary = binary
        # `run_id`/`emitter` are optional: Phase 8's run loop is what will
        # normally supply them. Without them `cancel()` still kills the
        # process (spec 2.4 steps 1-3) but skips the orphan sweep + event
        # emission `cancel_and_reap` adds -- see `cancel()` below and the
        # Phase 3 state doc. Not wiring worktree/run lifecycle early is
        # deliberate (handoff: "don't invent worktree lifecycle early").
        self._run_id = run_id
        self._emitter = emitter
        self._lock = threading.Lock()
        self._running: dict[str, ManagedProcess] = {}

    def preflight(self) -> list[CheckResult]:
        results = [check_executable("claude cli", self._binary, "running the harness")]

        if os.environ.get(BILLING_ENV_VAR):
            results.append(
                fail(
                    "subscription billing",
                    f"{BILLING_ENV_VAR} is set. Spec 2.3: this silently switches "
                    f"billing from the Pro/Max subscription to per-token API rates. "
                    f"Unset it before running unattended.",
                )
            )
        else:
            results.append(
                ok("subscription billing", f"{BILLING_ENV_VAR} is unset (subscription billing)")
            )

        mode = self.config.harness.permission_mode
        if mode in FORBIDDEN_PERMISSION_MODES:
            results.append(
                fail(
                    "permission mode",
                    f"{mode!r} is never permitted (spec 2.3) -- the host holds real credentials",
                )
            )
        elif mode not in SUPPORTED_PERMISSION_MODES:
            results.append(
                warn(
                    "permission mode",
                    f"{mode!r} is not a mode this adapter knows; "
                    f"expected one of {sorted(SUPPORTED_PERMISSION_MODES)}",
                )
            )
        else:
            results.append(ok("permission mode", mode))

        return results

    def probe(self, prompt: str) -> HarnessResult:
        return self._invoke(task_id="probe", prompt=prompt)

    def propose(self, spec_path: Path, context: dict[str, Any]) -> HarnessResult:
        # The exact OpenSpec-facing prompt -- and how much of it leans on the
        # harness-facing CLAUDE.md operating policy vs. being spelled out here
        # -- is deliberately left thin. That policy doc is Phase 4's job
        # (§10.3); Phase 3's scope (§2.1-2.3, §4, §7.2) is invocation and
        # stream parsing, not prompt engineering. Revisit once Phase 4 exists.
        task_id = str(context.get("task_id", spec_path.stem))
        prompt = (
            f"Run OpenSpec's propose workflow for the change at {spec_path}. "
            f"Follow this repository's operating policy for how to invoke OpenSpec."
        )
        return self._invoke(task_id=task_id, prompt=prompt)

    def implement(
        self,
        task_id: str,
        spec_path: Path,
        retry_context: str | None = None,
    ) -> HarnessResult:
        prompt = f"Implement the OpenSpec change at {spec_path} (task {task_id})."
        if retry_context:
            prompt += f"\n\nThe previous attempt failed:\n{retry_context}"
        return self._invoke(task_id=task_id, prompt=prompt)

    def get_progress(self, task_id: str) -> tuple[int, int]:
        raise NotImplementedError(
            "reports_native_progress=False -- progress is watched from tasks.md (Phase 7)"
        )

    def cancel(self, task_id: str) -> None:
        with self._lock:
            process = self._running.get(task_id)
        if process is None:
            return
        if self._emitter is not None:
            cancel_and_reap(
                process,
                run_id=self._run_id or "",
                task_id=task_id,
                worktree_path=self.cwd,
                config=self.config,
                emitter=self._emitter,
            )
        else:
            process.cancel(grace_s=self.config.timeouts.kill_grace)

    # -- invocation mechanics ------------------------------------------------

    def _build_argv(self, prompt: str) -> list[str]:
        argv = [
            self._binary,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-turns",
            str(self.config.harness.max_turns),
            "--permission-mode",
            self.config.harness.permission_mode,
            # A headless run must run under Cosmo's own project settings
            # (spec 2.5 guardrail hooks, .claude/settings.json) and nothing
            # else -- `user` scope is the operator's global ~/.claude
            # (arbitrary personal hooks/plugins/MCP servers with unknown
            # token cost and side effects), `local` is a gitignored personal
            # override file that shouldn't exist in an unattended run at all.
            # Verified by a real invocation (Phase 4 state doc): with the
            # default (all scopes), this box's own global SessionStart/
            # UserPromptSubmit/Stop hooks fired even though cwd was /tmp,
            # nothing to do with the target repo; with `--setting-sources
            # project`, they do not fire, and the target repo's own
            # PreToolUse guardrail hooks still do.
            "--setting-sources",
            "project",
        ]
        # Spec 2.3: bypassPermissions / --dangerously-skip-permissions is
        # never used -- the droplet has real credentials, blast radius isn't
        # zero. Asserted, not just omitted, so a future edit can't reintroduce
        # it silently; `test_dangerously_skip_permissions_never_appears`
        # covers this from the outside too.
        assert "--dangerously-skip-permissions" not in argv
        assert "bypassPermissions" not in argv
        return argv

    def _build_env(self, task_id: str) -> dict[str, str]:
        env = dict(os.environ)
        # Spec 2.3: explicitly scrub rather than assume absence.
        env.pop(BILLING_ENV_VAR, None)
        env.update(TELEMETRY_ENV)
        env[TASK_ID_ENV_VAR] = task_id
        env[DB_PATH_ENV_VAR] = str(self.config.paths.db_path)
        return env

    def _invoke(self, *, task_id: str, prompt: str) -> HarnessResult:
        argv = self._build_argv(prompt)
        env = self._build_env(task_id)
        raw_log_path = (
            self.config.paths.log_dir / "harness" / task_id / f"{uuid.uuid4().hex}.ndjson"
        )
        reader = StreamReader()

        process = ManagedProcess(
            argv,
            raw_log_path=raw_log_path,
            cwd=self.cwd,
            env=env,
            on_stdout_chunk=reader.feed,
        )
        with self._lock:
            self._running[task_id] = process

        started = time.monotonic()
        try:
            # No timeout here: `has_internal_timeout=False` means Cosmo's
            # orchestration layer (Phase 7/8, not built yet) is the one that
            # decides a run has stalled and calls `cancel()` from another
            # thread -- which unblocks this `wait()` by actually killing the
            # child, not by any cooperation from this method. This adapter
            # alone also doesn't know which task-state wall clock (spec 3.3:
            # proposing/implementing/validating each have their own) applies
            # to a given call, so it has no correct value to guess here even
            # if it wanted one.
            exit_code = process.wait()
        finally:
            # Always finalize, even on the ordinary-exit path: `cancel()` is
            # what joins the stdout/stderr drain threads (see `ManagedProcess
            # ._finalize`), and it's a fast no-op on an already-exited process
            # (see `test_cancel_on_an_already_exited_process_returns_true`).
            # Without this, `reader`'s last chunk(s) might not have landed yet.
            process.cancel(grace_s=self.config.timeouts.kill_grace)
            with self._lock:
                self._running.pop(task_id, None)

        duration_seconds = time.monotonic() - started
        terminal = reader.terminal_result
        success = exit_code == 0  # spec 2.3: zero vs non-zero exit only, never a specific value

        return HarnessResult(
            success=success,
            output_summary=_summarize(terminal, success, exit_code),
            raw_log_path=raw_log_path,
            files_changed=[],  # no source of truth before Phase 5's git diff exists
            duration_seconds=duration_seconds,
            total_cost_usd=_extract(terminal, "total_cost_usd"),
            exit_code=exit_code,
            session_id=reader.session_id,
        )


def _extract(terminal: ClassifiedEvent | None, key: str) -> Any:
    if terminal is None:
        return None
    return terminal.payload.get(key)


def _summarize(terminal: ClassifiedEvent | None, success: bool, exit_code: int) -> str:
    # `subtype` on the terminal result is a structured field the CLI defines
    # (e.g. "success"), not prose -- reading it for a short summary label is
    # exactly the "reads the structured output for the reason" spec 2.3 asks
    # for, distinct from the prose-parsing spec 4 prohibits for classification.
    if terminal is not None:
        subtype = terminal.payload.get("subtype")
        if isinstance(subtype, str):
            return subtype
    return "success" if success else f"exit code {exit_code}"
