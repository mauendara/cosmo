"""Direct Claude Code + OpenRouter adapter -- no `ori` in the loop.

`harness.ori.adapter.OriClaudeAdapter` routes through the third-party `ori`
CLI, which turned out to have a real, unresolved bug (found by hand,
2026-09-04, see its module docstring for the full account): whenever `ori`
launches `claude` with `--verbose --output-format stream-json` -- which
Cosmo's `--output-format stream-json` always requires (`claude` itself
enforces the `--verbose` pairing) -- it makes `claude` request the
Anthropic-only `thinking.display: "updates"` beta feature, which no
OpenRouter provider for a non-Anthropic model supports. Every call to a
model like `z-ai/glm-4.6` 400s before the model ever sees the prompt,
regardless of `--reasoning-effort` (all five accepted levels tried, all
still 400). `ori-claude` still keeps its PreToolUse guardrail hooks (a
different, false lead about `--setting-sources` from the same
investigation turned out to be environment contamination, not a real bug --
see its module docstring), but it's only reliably usable for Anthropic
models routed through OpenRouter, not the "test a budget model" case this
was built for.

This module exists for that case: it reimplements the same OpenRouter
routing directly against the real `claude` binary, with no `ori` process
involved at all. Proven working by hand (2026-09-04) against the real
`--verbose --output-format stream-json` shape Cosmo actually uses, on both
the default (Anthropic) model and `z-ai/glm-4.6` explicitly -- real success,
real cost reported (`$0.016` for the GLM probe), no `thinking.display`
error either way. Whatever `ori` does differently to trigger that beta
request, this route's plain env-var + `--settings` mechanism doesn't do it:

```
ANTHROPIC_BASE_URL=https://openrouter.ai/api \\
ANTHROPIC_MODEL=z-ai/glm-4.6 \\
claude -p "..." --settings '{"apiKeyHelper":"printf %s \\"$OPENROUTER_API_KEY\\""}' \\
  --setting-sources project --verbose --output-format stream-json
```

This module (with `invoker.py`, shared with the native and ori-routed
adapters) is the ONLY other place in Cosmo that may name OpenRouter-specific
environment variables or flags -- enforced by `tests/test_harness_boundary.py`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import ClassVar

from cosmo.checks import CheckResult, check_executable, fail, ok, warn
from cosmo.config import CosmoConfig
from cosmo.events import EventEmitter
from cosmo.harness.base import HarnessCapabilities
from cosmo.harness.claude.adapter import BILLING_ENV_VAR
from cosmo.harness.claude.invoker import (
    DB_PATH_ENV_VAR,
    TASK_ID_ENV_VAR,
    TELEMETRY_ENV,
    _ClaudeCodeInvoker,
    check_permission_mode,
)

CLAUDE_BINARY = "claude"
CREDENTIAL_ENV_VAR = "OPENROUTER_API_KEY"
ANTHROPIC_BASE_URL_VAR = "ANTHROPIC_BASE_URL"
ANTHROPIC_MODEL_VAR = "ANTHROPIC_MODEL"
ANTHROPIC_BASE_URL_VALUE = "https://openrouter.ai/api"

# The credential mechanism proven working by hand: a shell one-liner that
# echoes the already-resolved env var back out. `apiKeyHelper` is executed
# by `claude` itself at call time (`printf` reads `$OPENROUTER_API_KEY` from
# the child's own environment, set below in `_build_env`), so this JSON
# blob is static -- no per-call secret ever appears in argv or a log line.
_APIKEY_HELPER_SETTINGS = json.dumps({"apiKeyHelper": 'printf %s "$OPENROUTER_API_KEY"'})


class ClaudeOpenRouterAdapter(_ClaudeCodeInvoker):
    name: ClassVar[str] = "claude-openrouter"

    capabilities: ClassVar[HarnessCapabilities] = HarnessCapabilities(
        reports_native_progress=False,  # progress comes from watching tasks.md (spec 4)
        supports_retry_context=True,
        has_internal_timeout=False,  # Cosmo imposes the wall clock (spec 3.3)
        reports_native_cost=True,  # total_cost_usd on the terminal result object
        supports_gating=True,  # --setting-sources project stays on -- no ori, no bug
        supports_structured_stream=True,  # same binary, same stream-json
    )

    def __init__(
        self,
        config: CosmoConfig,
        *,
        cwd: Path | None = None,
        binary: str = CLAUDE_BINARY,
        run_id: str | None = None,
        emitter: EventEmitter | None = None,
    ) -> None:
        super().__init__(config, cwd=cwd, binary=binary, run_id=run_id, emitter=emitter)

    def preflight(self) -> list[CheckResult]:
        results = [check_executable("claude cli", self._binary, "running the harness")]

        if os.environ.get(CREDENTIAL_ENV_VAR):
            results.append(ok("openrouter credential", f"{CREDENTIAL_ENV_VAR} is set"))
        else:
            # Unlike ori-claude, this route has no fallback credential store
            # (no `ori login`) -- the apiKeyHelper script above has nothing
            # to read without this, so it's a hard failure, not a warning.
            results.append(
                fail(
                    "openrouter credential",
                    f"{CREDENTIAL_ENV_VAR} is unset -- the apiKeyHelper this route injects "
                    f"reads it directly and has no other way to authenticate",
                )
            )

        if os.environ.get(BILLING_ENV_VAR):
            results.append(
                ok(
                    "subscription billing",
                    f"{BILLING_ENV_VAR} is set but irrelevant on this route -- scrubbed "
                    f"before launch, the apiKeyHelper supplies the credential instead",
                )
            )
        else:
            results.append(
                ok("subscription billing", f"{BILLING_ENV_VAR} is unset; irrelevant either way")
            )

        results.append(check_permission_mode(self.config.harness.permission_mode))

        if not self.config.cost.run_limit_enabled:
            results.append(
                warn(
                    "cost ceiling",
                    "cost.max_cost_per_run_usd is 0 (disabled). Native Claude Code is "
                    "subscription-billed, where that default is safe; this route is metered "
                    "per OpenRouter token, so an unattended run has no spend hard stop.",
                )
            )
        else:
            results.append(ok("cost ceiling", f"${self.config.cost.max_cost_per_run_usd:.2f}/run"))

        return results

    # -- invocation mechanics ------------------------------------------------

    def _build_argv(self, prompt: str, model: str) -> list[str]:
        del model  # threaded through ANTHROPIC_MODEL in _build_env instead, not a CLI flag here
        argv = [
            self._binary,
            "-p",
            prompt,
            *self._claude_flags(),  # includes --setting-sources project -- guardrails stay on
            "--settings",
            _APIKEY_HELPER_SETTINGS,
        ]
        assert "--dangerously-skip-permissions" not in argv
        assert "bypassPermissions" not in argv
        return argv

    def _build_env(self, task_id: str, model: str) -> dict[str, str]:
        env = dict(os.environ)
        # Spec 2.3's footgun protection: an operator ANTHROPIC_API_KEY must
        # not silently override the apiKeyHelper/BASE_URL routing below.
        env.pop(BILLING_ENV_VAR, None)
        env.update(TELEMETRY_ENV)
        env[ANTHROPIC_BASE_URL_VAR] = ANTHROPIC_BASE_URL_VALUE
        env[ANTHROPIC_MODEL_VAR] = model
        env[TASK_ID_ENV_VAR] = task_id
        env[DB_PATH_ENV_VAR] = str(self.config.paths.db_path)
        return env
