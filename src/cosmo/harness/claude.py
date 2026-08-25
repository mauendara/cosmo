"""Claude Code CLI adapter (spec 2.3).

Phase 0 implements only `capabilities` and `preflight()`. The execution methods
land in Phase 3, together with the structured-stream reader and the process
supervision from Phase 2.

In Phase 3 this module becomes a package (`harness/claude/`) so the stream reader
sits beside the adapter rather than in Cosmo core: the stream format is this
harness's, not a universal one, and a core-level reader would leak Claude's wire
protocol across the section 2 boundary.

This module is the ONLY place in Cosmo that may name Claude-specific binaries,
environment variables, or flags.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar

from cosmo.checks import CheckResult, check_executable, fail, ok, warn
from cosmo.harness.base import HarnessAdapter, HarnessCapabilities, HarnessResult

BINARY = "claude"

# Spec 2.3: setting this silently switches billing from the Pro/Max subscription
# to per-token API rates. Unattended overnight runs make that an expensive
# surprise, so it is a hard failure rather than a warning.
BILLING_ENV_VAR = "ANTHROPIC_API_KEY"

# Spec 2.3: never used. The droplet holds SSH keys and real credentials.
FORBIDDEN_PERMISSION_MODES = frozenset({"bypassPermissions"})

SUPPORTED_PERMISSION_MODES = frozenset({"dontAsk", "auto"})


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

    def preflight(self) -> list[CheckResult]:
        results = [check_executable("claude cli", BINARY, "running the harness")]

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

    def propose(self, spec_path: Path, context: dict[str, Any]) -> HarnessResult:
        raise NotImplementedError("Claude Code adapter execution lands in Phase 3")

    def implement(
        self,
        task_id: str,
        spec_path: Path,
        retry_context: str | None = None,
    ) -> HarnessResult:
        raise NotImplementedError("Claude Code adapter execution lands in Phase 3")

    def get_progress(self, task_id: str) -> tuple[int, int]:
        raise NotImplementedError("progress watching lands in Phase 7")

    def cancel(self, task_id: str) -> None:
        raise NotImplementedError("process supervision lands in Phase 2")
