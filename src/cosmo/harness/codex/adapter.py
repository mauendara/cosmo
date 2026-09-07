"""First-class adapter for the non-interactive Codex CLI."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from cosmo.checks import CheckResult, check_executable, fail, ok
from cosmo.config import CosmoConfig
from cosmo.events import EventEmitter
from cosmo.harness.base import HarnessAdapter, HarnessCapabilities, HarnessResult
from cosmo.harness.codex.invoker import BILLING_ENV_VAR, BINARY, CodexInvoker
from cosmo.task.review import REVIEW_RESULT_RELATIVE_PATH

SUPPORTED_PERMISSION_MODE = "dontAsk"


class CodexAdapter(HarnessAdapter):
    """Translate Cosmo's harness roles into fresh ``codex exec`` calls."""

    name: ClassVar[str] = "codex"
    capabilities: ClassVar[HarnessCapabilities] = HarnessCapabilities(
        reports_native_progress=False,
        supports_retry_context=True,
        has_internal_timeout=False,
        reports_native_cost=False,
        supports_gating=True,
        supports_structured_stream=True,
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
        self._invoker = CodexInvoker(
            config,
            cwd=self.cwd,
            binary=binary,
            run_id=run_id,
            emitter=emitter,
        )

    def preflight(self) -> list[CheckResult]:
        results = [check_executable("codex cli", self._binary, "running the harness")]
        if os.environ.get(BILLING_ENV_VAR):
            results.append(
                fail(
                    "subscription billing",
                    f"{BILLING_ENV_VAR} is set and may select API-billed authentication. "
                    "Unset it before running unattended.",
                )
            )
        else:
            results.append(
                ok("subscription billing", f"{BILLING_ENV_VAR} is unset (saved login preserved)")
            )

        mode = self.config.harness.permission_mode
        if mode == SUPPORTED_PERMISSION_MODE:
            results.append(ok("permission mode", mode))
        else:
            results.append(
                fail(
                    "permission mode",
                    f"{mode!r} is unsupported by the Codex adapter; "
                    f"expected {SUPPORTED_PERMISSION_MODE!r}",
                )
            )
        return results

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
            model=model or self.config.harness.resolve_model(self.name, "probe"),
            role="probe",
            on_activity=on_activity,
        )

    def propose(
        self,
        spec_path: Path,
        context: dict[str, Any],
        *,
        on_activity: Callable[[str], None] | None = None,
    ) -> HarnessResult:
        task_id = str(context.get("task_id", spec_path.stem))
        spec_id = str(context.get("spec_id", spec_path.stem))
        prompt = (
            f"Run OpenSpec's propose workflow for the change at {spec_path}. "
            f"Name the change exactly {spec_id!r} (`openspec new change {spec_id}`) -- "
            f"do not pick a different name, even a shorter or more natural-looking one. "
            f"Follow this repository's operating policy for how to invoke OpenSpec."
        )
        return self._invoke(
            task_id=task_id,
            prompt=prompt,
            model=self.config.harness.resolve_model(self.name, "propose"),
            role="propose",
            on_activity=on_activity,
        )

    def implement(
        self,
        task_id: str,
        spec_path: Path,
        retry_context: str | None = None,
        *,
        on_activity: Callable[[str], None] | None = None,
        max_turns: int | None = None,
    ) -> HarnessResult:
        del max_turns  # Codex has no turn-count concept of its own (G1's adaptive budget)
        prompt = (
            f"Implement the OpenSpec change at {spec_path} (task {task_id}). "
            "Before changing anything, inspect the current worktree with `git log`, "
            "`git status`, and `openspec status --change <id>` so you continue from "
            "work that is already present instead of redoing it. Do not run `git add` "
            "or `git commit`: Codex's workspace sandbox protects linked-worktree Git "
            "metadata. Leave completed implementation and OpenSpec changes in the "
            "working tree; Cosmo stages and commits them after this call returns."
        )
        if retry_context:
            prompt += f"\n\nThe previous attempt failed:\n{retry_context}"
        return self._invoke(
            task_id=task_id,
            prompt=prompt,
            model=self.config.harness.resolve_model(self.name, "implement"),
            role="implement",
            on_activity=on_activity,
        )

    def review(
        self,
        task_id: str,
        spec_path: Path,
        base_branch: str,
        *,
        on_activity: Callable[[str], None] | None = None,
        live_verification: bool = False,
    ) -> HarnessResult:
        del live_verification  # G2's diff-only/live-verification prompt split is Claude-only so far
        verdict_path = self.cwd / REVIEW_RESULT_RELATIVE_PATH
        prompt = (
            f"Review this branch's implementation for task {task_id}. Run "
            f"`git diff {base_branch}...HEAD` to see the diff and read the OpenSpec "
            f"change at {spec_path} (its spec/tasks.md) for what was asked. You have "
            "no memory of the implementation session; judge only what these show. "
            "Do not modify source files or any other repository content. When done, "
            f"write only your verdict to the canonical path `{verdict_path}` as JSON: "
            f'`{{"verdict": "approved"}}` or `{{"verdict": "rejected", "reason": "<why, '
            f'specific enough to act on>"}}`.'
        )
        return self._invoke(
            task_id=task_id,
            prompt=prompt,
            model=self.config.harness.resolve_model(self.name, "review"),
            role="review",
            on_activity=on_activity,
        )

    def get_progress(self, task_id: str) -> tuple[int, int]:
        raise NotImplementedError(
            "reports_native_progress=False -- progress is watched from tasks.md"
        )

    def cancel(self, task_id: str) -> None:
        self._invoker.cancel(task_id)

    def _invoke(
        self,
        *,
        task_id: str,
        prompt: str,
        model: str,
        role: str,
        on_activity: Callable[[str], None] | None,
    ) -> HarnessResult:
        # The task runner rebinds `adapter.cwd` after it creates the task
        # worktree. Real Phase 5 lifecycle validation found that keeping the
        # invoker's constructor-time cwd made every injected hook point back
        # at Cosmo's own checkout, so all worktree tool calls failed closed.
        # Claude's adapter owns invocation state directly and reads its cwd at
        # call time; mirror that contract explicitly for this composed adapter.
        self._invoker.cwd = self.cwd
        return self._invoker.invoke(
            task_id=task_id,
            prompt=prompt,
            model=model,
            role=role,
            on_activity=on_activity,
        )
