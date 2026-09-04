"""Ori-routed Claude Code adapter (v13 plan, gated on validation V2).

`ori claude -- <args>` execs the real `claude` binary in place (same pid --
`ManagedProcess`'s SIGTERM->SIGKILL-on-pgid pattern needs no special-casing),
passing everything after `--` untouched. This module (with `invoker.py`,
shared with the native adapter) is the ONLY other place in Cosmo that may
name Ori-specific binaries, environment variables, or flags -- enforced by
`tests/test_harness_boundary.py`.

Facts this rests on, all confirmed by real invocation (v12,
docs/v12-ori-opencode-harness-info.md):

- The terminal `result` JSON is byte-for-byte the schema `stream.py` already
  parses, including a populated `total_cost_usd` against a real OpenRouter
  model.
- `OPENROUTER_API_KEY` resolves headlessly with no login step.
- Ori consumes `--model` itself and translates it to `ANTHROPIC_MODEL`; it
  does not forward a `--model` flag to `claude` -- a second `--model` after
  `--` would be a conflicting flag, not an override.
- Ori sets its own `ANTHROPIC_API_KEY` on the child unconditionally, so
  scrubbing the operator's cannot break the call.

**A real, unresolved `ori` bug on this route for non-Anthropic models
(found by hand, 2026-09-04).** `--output-format stream-json` requires
`--verbose` (Claude Code itself enforces this -- "requires --verbose" is a
hard error otherwise), and whenever both are present, `ori` makes Claude
Code request the `thinking.display: "updates"` Anthropic beta feature on
every turn. No OpenRouter provider for a non-Anthropic model (confirmed on
z-ai/glm-4.6 and z-ai/glm-4.7-flash) has an endpoint that supports it, so
the call 400s before the model ever sees the prompt -- every task blocks on
an environment_error with zero files touched. `--reasoning-effort` does NOT
fix this -- tried all five accepted levels (`low`/`medium`/`high`/`xhigh`/
`max`) against the real `--verbose --output-format stream-json` shape
Cosmo actually uses, all five still 400. (An earlier version of this
comment claimed `--reasoning-effort medium` fixed it -- that conclusion
came from testing with `--output-format json`, non-streaming, which Cosmo
never actually uses; retested against the real shape and it does not hold.
Left here as a correction, not silently dropped, so it isn't retried.) No
known fix from Cosmo's side -- this is an `ori` bug, not a `claude` one:
`harness/claude_openrouter/adapter.py`'s route talks to the same `claude`
binary directly with the same `--verbose --output-format stream-json` and
never triggers it. **Use `claude-openrouter` for any non-Anthropic
OpenRouter model** -- this route (`ori-claude`) only genuinely works for
Anthropic models routed through OpenRouter (which support their own beta
feature fine), a much narrower case than "test a budget model."

A related false lead worth recording so it isn't retried either:
`--setting-sources` looked, for a few hours by hand the same day, like it
independently broke `ori`'s OpenRouter credential injection ("Not logged
in", despite `ori auth` showing a valid credential). Every repro of that one
turned out to be run from inside an already-running Claude Code session,
which leaks that session's own `CLAUDECODE`/`CLAUDE_CODE_*` env vars into
the child (a gotcha docs/handoff.md already recorded, predating this
investigation) -- stripping those vars fixes it with `--setting-sources
project` present, no argv change needed. `--setting-sources` was never the
cause, so `_claude_flags()` (invoker.py) keeps passing it unconditionally
and this route's PreToolUse guardrail hooks fire exactly like the native
route's do.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar

from cosmo.checks import CheckResult, check_executable, ok, warn
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

ORI_BINARY = "ori"
ORI_SUBCOMMAND = "claude"
CLAUDE_BINARY = "claude"  # exec'd by ori; checked in preflight only, never launched directly
CREDENTIAL_ENV_VAR = "OPENROUTER_API_KEY"

# v12: Ori's own anonymous telemetry, on by default -- separate from
# Claude Code's own TELEMETRY_ENV, which stays on either route (spec 9.4).
ORI_TELEMETRY_ENV = {"ORI_TELEMETRY": "0"}


class OriClaudeAdapter(_ClaudeCodeInvoker):
    name: ClassVar[str] = "ori-claude"

    capabilities: ClassVar[HarnessCapabilities] = HarnessCapabilities(
        reports_native_progress=False,  # progress comes from watching tasks.md (spec 4)
        supports_retry_context=True,
        has_internal_timeout=False,  # Cosmo imposes the wall clock (spec 3.3)
        reports_native_cost=True,  # total_cost_usd populated -- v12, real invocation
        supports_gating=True,  # PreToolUse hooks fire and block -- v12, real invocation
        supports_structured_stream=True,  # same binary, same stream-json (verify: plan V1)
    )

    def __init__(
        self,
        config: CosmoConfig,
        *,
        cwd: Path | None = None,
        ori_binary: str = ORI_BINARY,
        claude_binary: str = CLAUDE_BINARY,
        run_id: str | None = None,
        emitter: EventEmitter | None = None,
    ) -> None:
        super().__init__(config, cwd=cwd, binary=ori_binary, run_id=run_id, emitter=emitter)
        self._claude_binary = claude_binary

    def preflight(self) -> list[CheckResult]:
        results = [
            check_executable("ori cli", self._binary, "running the harness"),
            check_executable("claude cli", self._claude_binary, "the binary ori execs in place"),
        ]

        if os.environ.get(CREDENTIAL_ENV_VAR):
            results.append(ok("openrouter credential", f"{CREDENTIAL_ENV_VAR} is set"))
        else:
            results.append(
                warn(
                    "openrouter credential",
                    f"{CREDENTIAL_ENV_VAR} is unset -- not necessarily a problem, `ori login "
                    f"--with-key` also stores a credential file this check cannot see",
                )
            )

        # Unlike the native adapter, an operator ANTHROPIC_API_KEY is never a
        # hard failure here: Ori sets its own on the child unconditionally
        # (v12), so the operator's value is scrubbed and cannot reach it
        # either way -- this is the honest inverse of ClaudeCodeAdapter's
        # spec 2.3 billing check, not an oversight.
        if os.environ.get(BILLING_ENV_VAR):
            results.append(
                ok(
                    "subscription billing",
                    f"{BILLING_ENV_VAR} is set but irrelevant on this route -- scrubbed "
                    f"before launch, Ori supplies its own to the child",
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

        results.append(
            warn(
                "non-Anthropic model support",
                "found by hand (2026-09-04): ori makes claude request an Anthropic-only "
                "beta feature whenever --output-format stream-json is used (Cosmo always "
                "uses it), which every non-Anthropic OpenRouter provider rejects with a "
                "400 -- no known fix on this route. Use the claude-openrouter harness for "
                "a non-Anthropic model; this route only reliably works for Anthropic "
                "models routed through OpenRouter.",
            )
        )

        return results

    # -- invocation mechanics ------------------------------------------------

    def _build_argv(self, prompt: str, model: str) -> list[str]:
        argv = [
            self._binary,
            ORI_SUBCOMMAND,
            "--model",
            model,
            "--",
            "-p",
            prompt,
            *self._claude_flags(),
        ]
        # Ori consumes `--model` itself (v12); a second one after `--` would
        # be a conflicting flag reaching `claude`, not an override.
        assert argv.count("--model") == 1
        assert argv.index("--model") < argv.index("--")
        assert "--dangerously-skip-permissions" not in argv
        assert "bypassPermissions" not in argv
        return argv

    def _build_env(self, task_id: str, model: str) -> dict[str, str]:
        del model  # resolved via --model on this route's own argv, not the environment
        env = dict(os.environ)
        # Different reason than the native adapter's scrub: Ori sets its own
        # ANTHROPIC_API_KEY on the child unconditionally (v12), so the
        # operator's value cannot reach `claude` either way -- scrubbing it
        # here just keeps it out of the child's environment on principle,
        # and preserves spec 2.3's footgun protection if that ever changes.
        env.pop(BILLING_ENV_VAR, None)
        env.update(TELEMETRY_ENV)
        env.update(ORI_TELEMETRY_ENV)
        env[TASK_ID_ENV_VAR] = task_id
        env[DB_PATH_ENV_VAR] = str(self.config.paths.db_path)
        return env
