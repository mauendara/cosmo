"""Claude Code CLI adapter, native (subscription-billed) route (spec 2.3).

This module (with `stream.py` and `invoker.py` beside it) is the ONLY place
in Cosmo that may name Claude-specific binaries, environment variables, or
flags -- enforced by `tests/test_harness_boundary.py`. The invocation
mechanics shared with the Ori-routed adapter (`harness.ori.adapter.
OriClaudeAdapter`) live in `invoker.py`'s `_ClaudeCodeInvoker`; this module
only declares what's genuinely native-specific: argv/env assembly and
preflight (spec 2.3's subscription-billing footgun check).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar

from cosmo.checks import CheckResult, check_executable, fail, ok
from cosmo.config import CosmoConfig
from cosmo.events import EventEmitter
from cosmo.harness.base import HarnessCapabilities
from cosmo.harness.claude.invoker import (
    DB_PATH_ENV_VAR,
    TASK_ID_ENV_VAR,
    TELEMETRY_ENV,
    _ClaudeCodeInvoker,
    check_permission_mode,
)

BINARY = "claude"

# Spec 2.3: setting this silently switches billing from the Pro/Max subscription
# to per-token API rates. Unattended overnight runs make that an expensive
# surprise, so it is a hard failure rather than a warning.
BILLING_ENV_VAR = "ANTHROPIC_API_KEY"


class ClaudeCodeAdapter(_ClaudeCodeInvoker):
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
        super().__init__(config, cwd=cwd, binary=binary, run_id=run_id, emitter=emitter)

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

        results.append(check_permission_mode(self.config.harness.permission_mode))
        return results

    # -- invocation mechanics ------------------------------------------------

    def _build_argv(self, prompt: str, model: str) -> list[str]:
        argv = [
            self._binary,
            "-p",
            prompt,
            *self._claude_flags(),
            "--model",
            model,
        ]
        # Spec 2.3: bypassPermissions / --dangerously-skip-permissions is
        # never used -- the droplet has real credentials, blast radius isn't
        # zero. Asserted, not just omitted, so a future edit can't reintroduce
        # it silently; `test_dangerously_skip_permissions_never_appears`
        # covers this from the outside too.
        assert "--dangerously-skip-permissions" not in argv
        assert "bypassPermissions" not in argv
        return argv

    def _build_env(self, task_id: str, model: str) -> dict[str, str]:
        del model  # resolved via the --model CLI flag on this route, not the environment
        env = dict(os.environ)
        # Spec 2.3: explicitly scrub rather than assume absence.
        env.pop(BILLING_ENV_VAR, None)
        env.update(TELEMETRY_ENV)
        env[TASK_ID_ENV_VAR] = task_id
        env[DB_PATH_ENV_VAR] = str(self.config.paths.db_path)
        return env
