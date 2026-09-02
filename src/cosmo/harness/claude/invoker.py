"""Shared Claude Code invocation mechanics (spec 2.2, plan v13 Phase 1).

Both routes that end up running the real `claude` binary -- native
(`harness.claude.adapter.ClaudeCodeAdapter`) and Ori-routed
(`harness.ori.adapter.OriClaudeAdapter`) -- share everything about how that
binary is launched, watched, and its stream-json output turned into a
`HarnessResult`: only argv shape (`--model` placement, the wrapper prefix)
and env assembly (which credential var is scrubbed vs. passed through)
genuinely differ. Extracted here so neither adapter is the other's base
class, and so the cancel/process-group logic `write-a-new-adapter.md` calls
out as the easiest thing to get wrong is written, and tested, exactly once.
"""

from __future__ import annotations

import threading
import time
import uuid
from abc import abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cosmo.checks import CheckResult, fail, ok, warn
from cosmo.config import CosmoConfig
from cosmo.events import EventEmitter
from cosmo.harness.base import HarnessAdapter, HarnessResult
from cosmo.harness.claude.stream import (
    ClassifiedEvent,
    ClassifiedKind,
    StreamReader,
    describe_tool_call,
    extract_quota_signal,
)
from cosmo.proc import ManagedProcess, cancel_and_reap
from cosmo.task.review import REVIEW_RESULT_RELATIVE_PATH

# Spec 2.3: never used on either route. The droplet holds SSH keys and real
# credentials, regardless of which binary launched the session.
FORBIDDEN_PERMISSION_MODES = frozenset({"bypassPermissions"})
SUPPORTED_PERMISSION_MODES = frozenset({"dontAsk", "auto"})

# Spec 9.4: enable Claude Code's native OTel export, but keep content logging
# off explicitly rather than trusting the CLI's own default -- prompts and
# file contents in a telemetry backend are a data-exfiltration path for a
# private codebase. `OTEL_LOG_USER_PROMPTS` is what gates that content. Same
# `claude` binary either route, so this is Claude-Code-wide, not native-only.
TELEMETRY_ENV = {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_LOG_USER_PROMPTS": "0",
}

# Consumed by the test-path guard hook (templates/harness/<name>/hooks/
# test_path_guard.py) to read `task_queue.allow_test_edits` for the running
# task -- a hook is a separate OS process from Cosmo's own, so it has no
# other way to ask Cosmo's state (spec 2.5 / plan Phase 4 handoff). Not
# Claude-CLI flags themselves, but this is where the child's environment is
# assembled either route, so this is where they're set.
TASK_ID_ENV_VAR = "COSMO_TASK_ID"
DB_PATH_ENV_VAR = "COSMO_DB_PATH"


def check_permission_mode(mode: str) -> CheckResult:
    """Identical forbidden/supported check either route -- `permission_mode`
    is a `claude` CLI concept, not something Ori changes."""
    if mode in FORBIDDEN_PERMISSION_MODES:
        return fail(
            "permission mode",
            f"{mode!r} is never permitted (spec 2.3) -- the host holds real credentials",
        )
    if mode not in SUPPORTED_PERMISSION_MODES:
        return warn(
            "permission mode",
            f"{mode!r} is not a mode this adapter knows; "
            f"expected one of {sorted(SUPPORTED_PERMISSION_MODES)}",
        )
    return ok("permission mode", mode)


class _ClaudeCodeInvoker(HarnessAdapter):
    """Base for both `claude`-binary-launching adapters. `name` and
    `capabilities` stay declared on the concrete subclasses (registry needs
    them without instantiating anything); `_build_argv`/`_build_env` stay
    abstract here -- the one genuinely per-route difference."""

    def __init__(
        self,
        config: CosmoConfig,
        *,
        cwd: Path | None = None,
        binary: str,
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

    @abstractmethod
    def _build_argv(self, prompt: str, model: str) -> list[str]:
        """Full argv for this route. Must never contain
        `--dangerously-skip-permissions`/`bypassPermissions` -- assert it on
        the final list, in addition to `_claude_flags`' own assertion, so a
        route that prepends its own wrapper flags can't reintroduce them."""

    @abstractmethod
    def _build_env(self, task_id: str) -> dict[str, str]:
        """Full child environment for this route."""

    def _claude_flags(self) -> list[str]:
        """Everything after `-p <prompt>` that is identical either route --
        notably NOT `--model`: the native route passes it here directly, the
        Ori route passes it to `ori` before its own `--`, since Ori consumes
        `--model` itself and translates it to `ANTHROPIC_MODEL` rather than
        forwarding it (v12, real invocation)."""
        flags = [
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-turns",
            str(self.config.harness.max_turns),
            "--permission-mode",
            self.config.harness.permission_mode,
            # A headless run must run under Cosmo's own project settings
            # (spec 2.5 guardrail hooks, .claude/settings.json) and nothing
            # else -- see `ClaudeCodeAdapter._build_argv`'s original comment
            # (Phase 4 state doc) for the real-invocation finding this pins.
            "--setting-sources",
            "project",
            # Spec 2.3/2.5: `dontAsk` only executes calls matching
            # `permissions.allow`, but a headless worktree's workspace-trust
            # gate silently ignores that list from settings.json alone --
            # passing it here too, as a CLI flag, is unaffected (Phase 4
            # state doc, real invocation).
            "--allowedTools",
            "Write",
            "Edit",
            "Bash",
        ]
        assert "--dangerously-skip-permissions" not in flags
        assert "bypassPermissions" not in flags
        return flags

    # -- harness-agnostic prompts --------------------------------------------

    def probe(
        self,
        prompt: str,
        *,
        on_activity: Callable[[str], None] | None = None,
        model: str | None = None,
    ) -> HarnessResult:
        return self._invoke(
            task_id="probe",
            prompt=prompt,
            model=model or self.config.harness.model,
            on_activity=on_activity,
        )

    def propose(
        self,
        spec_path: Path,
        context: dict[str, Any],
        *,
        on_activity: Callable[[str], None] | None = None,
    ) -> HarnessResult:
        # The exact OpenSpec-facing prompt -- and how much of it leans on the
        # harness-facing CLAUDE.md operating policy vs. being spelled out here
        # -- is deliberately left thin. That policy doc is Phase 4's job
        # (§10.3); Phase 3's scope (§2.1-2.3, §4, §7.2) is invocation and
        # stream parsing, not prompt engineering. Revisit once Phase 4 exists.
        task_id = str(context.get("task_id", spec_path.stem))
        # `_do_finishing`'s `openspec archive` call and `_do_proposing`'s own
        # reused-worktree check both assume the change this session creates
        # is named `spec_id` (`Path(spec_path).stem`) -- found live: with
        # nothing pinning that down, the propose session picked its own
        # (reasonable-looking) name instead, e.g. stripping a task file's
        # `-task` suffix, and every later step assuming `spec_id` silently
        # missed the real change. Pinning the name here, rather than trying
        # to recover it after the fact, is what actually keeps this in sync.
        spec_id = str(context.get("spec_id", spec_path.stem))
        prompt = (
            f"Run OpenSpec's propose workflow for the change at {spec_path}. "
            f"Name the change exactly {spec_id!r} (`openspec new change {spec_id}`) -- "
            f"do not pick a different name, even a shorter or more natural-looking one. "
            f"Follow this repository's operating policy for how to invoke OpenSpec."
        )
        model = self.config.harness.propose_model or self.config.harness.model
        return self._invoke(task_id=task_id, prompt=prompt, model=model, on_activity=on_activity)

    def implement(
        self,
        task_id: str,
        spec_path: Path,
        retry_context: str | None = None,
        *,
        on_activity: Callable[[str], None] | None = None,
    ) -> HarnessResult:
        prompt = f"Implement the OpenSpec change at {spec_path} (task {task_id})."
        if retry_context:
            prompt += f"\n\nThe previous attempt failed:\n{retry_context}"
        model = self.config.harness.implement_model or self.config.harness.model
        return self._invoke(task_id=task_id, prompt=prompt, model=model, on_activity=on_activity)

    def review(
        self,
        task_id: str,
        spec_path: Path,
        base_branch: str,
        *,
        on_activity: Callable[[str], None] | None = None,
    ) -> HarnessResult:
        # No `retry_context`, no session resumption -- a fresh `claude -p`
        # call with no memory of the implementation session (v4 workflow
        # changes: "the review is real rather than the same session grading
        # its own work"). The verdict itself is never read from this call's
        # stream output (spec 4's prose-parsing prohibition, see
        # `HarnessAdapter.review`'s docstring) -- the prompt instead
        # instructs the reviewer to write it to
        # `task.review.REVIEW_RESULT_RELATIVE_PATH`, which
        # `task.machine._do_reviewing` reads back after this returns.
        prompt = (
            f"Review this branch's implementation for task {task_id}. Run "
            f"`git diff {base_branch}...HEAD` to see the diff and read the OpenSpec "
            f"change at {spec_path} (its spec/tasks.md) for what was asked -- you have "
            f"no memory of the implementation session, judge only what these show. "
            f"When done, write your verdict to "
            f"`{REVIEW_RESULT_RELATIVE_PATH.as_posix()}` as JSON: "
            f'`{{"verdict": "approved"}}` or `{{"verdict": "rejected", "reason": "<why, '
            f'specific enough to act on>"}}`.'
        )
        model = self.config.harness.review_model or self.config.harness.model
        return self._invoke(task_id=task_id, prompt=prompt, model=model, on_activity=on_activity)

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

    def _invoke(
        self,
        *,
        task_id: str,
        prompt: str,
        model: str,
        on_activity: Callable[[str], None] | None = None,
    ) -> HarnessResult:
        argv = self._build_argv(prompt, model)
        env = self._build_env(task_id)
        raw_log_path = (
            self.config.paths.log_dir / "harness" / task_id / f"{uuid.uuid4().hex}.ndjson"
        )
        reader = StreamReader(
            on_event=_relay_activity(on_activity, cwd=self.cwd) if on_activity else None
        )

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
        quota_window, quota_resets_at = extract_quota_signal(reader)

        return HarnessResult(
            success=success,
            output_summary=_summarize(terminal, success, exit_code),
            raw_log_path=raw_log_path,
            files_changed=[],  # no source of truth before Phase 5's git diff exists
            duration_seconds=duration_seconds,
            total_cost_usd=_extract(terminal, "total_cost_usd"),
            exit_code=exit_code,
            session_id=reader.session_id,
            quota_window=quota_window,
            quota_resets_at=quota_resets_at,
            tool_call_count=reader.tool_call_count,
        )


def _relay_activity(
    on_activity: Callable[[str], None], *, cwd: Path | None = None
) -> Callable[[ClassifiedEvent], None]:
    """Bridges `StreamReader`'s Claude-specific `ClassifiedEvent`s to the
    harness-agnostic `on_activity(line: str)` hook (item 3) -- only tool
    calls and the one session-start heartbeat are worth a human's attention
    live; every other heartbeat is already accounted for by `task.progress`
    (spec 4's liveness signal), not repeated here.

    `cwd`, when given, is the task's worktree root, passed through to
    `describe_tool_call` so it can collapse that prefix *before* its own
    length cap truncates the line -- doing the collapse here, after
    truncation, would be too late: a long absolute worktree path already
    eats the whole cap before the actual filename is ever reached (see
    `describe_tool_call`'s own docstring)."""
    seen_session_start = False

    def _on_event(event: ClassifiedEvent) -> None:
        nonlocal seen_session_start
        if event.kind is ClassifiedKind.TOOL_CALL:
            line = describe_tool_call(event.payload, cwd=cwd)
            if line is not None:
                on_activity(line)
            return
        if (
            not seen_session_start
            and event.kind is ClassifiedKind.HEARTBEAT
            and event.payload.get("type") == "system"
            and event.payload.get("subtype") == "init"
        ):
            seen_session_start = True
            model = event.payload.get("model")
            on_activity(f"session started (model={model})" if model else "session started")

    return _on_event


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
