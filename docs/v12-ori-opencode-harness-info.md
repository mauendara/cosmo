# Ori Harness + OpenCode — harness-adapter research findings

**Status: research only, no implementation plan.** This document records what
was found while investigating two OpenRouter-routed paths for a new Cosmo
harness, following directly from
[v11-cline-harness-info.md](v11-cline-harness-info.md)'s conclusion that
Cline has no working headless gating mechanism today. It does **not**
propose an adapter design, a `HarnessCapabilities` declaration, a file
layout, or a build plan — see
`user-docs/en/how-to/write-a-new-adapter.md` for what an adapter needs to
provide, and treat this document purely as factual input to that decision.

Everything below was verified by real invocation on this host
(2026-09-01/02), using the same `OPENROUTER_API_KEY` from
`~/.config/cosmo/.env.cline` that backed the Cline research, not inferred
from either project's docs alone — both projects' docs turned out to have
gaps or unverified claims, same lesson as Cline.

## Bottom line

Both paths have a **real, working, headlessly-reachable pre-execution
gating mechanism** — the thing that blocked Cline. That changes the
calculus entirely from the Cline research:

| | Ori + Claude Code | OpenCode (native OpenRouter) |
|---|---|---|
| Gating | **Real.** It's the actual Claude Code binary — the same `PreToolUse` hooks Cosmo's existing template already uses, confirmed firing and blocking through Ori | **Real.** A `tool.execute.before` plugin hook, confirmed firing and blocking under headless `--auto` |
| New code needed | Small — likely a variant/config toggle on the existing `ClaudeCodeAdapter`, not a whole new adapter tree | A full new adapter: new template, new `.opencode/plugin/*.ts` guardrails mirroring the Python hook scripts |
| Agent loop | Claude Code's own — tuned for Claude-shaped tool calling, retargeted at other models via OpenRouter's Anthropic-compatible endpoint | Provider-agnostic by design — likely the better fit for genuinely non-Anthropic model families |
| Structured terminal result | Identical to native Claude Code's `result` JSON (same fields the adapter already parses) | No single terminal event in the run's own stream; requires a follow-up `opencode export <sessionID>` call for authoritative cost/tokens |
| Auth for headless/CI | `OPENROUTER_API_KEY` env var resolves with zero setup (`ori auth` confirms it); `ori login --with-key` also documents a fully non-interactive path | `OPENROUTER_API_KEY` env var resolves with zero setup, no separate login step at all |
| Cancel/process-group | Confirmed clean — `ori claude` execs into the real `claude` binary in place (same pid), so `ManagedProcess`'s existing SIGTERM→SIGKILL-on-pgid pattern needs no special-casing | Confirmed clean — single process, SIGTERM alone reaped it |

**Recommendation implied by this research** (not a decision — that's the
user's to make): Ori + Claude Code is the cheaper, lower-risk next harness
to build, because it reuses gating infrastructure Cosmo already trusts.
OpenCode is the stronger longer-term investment for testing genuinely
non-Anthropic models, but costs a full new adapter.

## Ori Harness

**Version tested**: `@ori-runtime/cli` `0.12.1+e1631f8` (npm-independent
standalone binary, installed via `curl -fsSL https://openrouter.ai/labs/ori/install.sh | bash`,
`ORI_INSTALL_DIR` override respected). **Not** the npm/OAuth-only tool the
public docs pages implied — installed and ran entirely from the CLI's own
`--help` output and real invocation, which is more complete than
`openrouter.ai/docs/guides/ori/harness` (that page doesn't document
`--with-key`, `ori auth`, or the `OPENROUTER_API_KEY` env-var resolution
path at all — all three were found from `ori --help`/`ori login --help`
directly).

Ori is a thin, honest wrapper: `ori claude -- <args>` launches the real,
separately-installed `claude` binary (resolved from `PATH`, same as any
other invocation) with an OpenRouter-pointed environment. It does not
reimplement Claude Code's agent loop, tool set, or hook system. "Everything
after the flags above is passed to claude untouched" (from `ori claude
--help`) is accurate — confirmed by direct observation below.

### Auth — genuinely headless

```
export OPENROUTER_API_KEY=sk-or-v1-...
ori auth
```

```json
{"ok":true,"command":"auth","data":{"authenticated":true,
 "source":{"kind":"environment","location":"OPENROUTER_API_KEY"},
 "userId":"user_..."}}
```

No `ori login` step was needed at all — the env var resolves on its own,
confirmed by a real `ori auth` call with no prior login. `ori login
--with-key` (piped from stdin, or a hidden prompt) is a second, documented,
fully non-interactive path for the case where you don't want to rely on an
ambient env var. `ori --json` (or the auto-detection when stdout isn't a
TTY) puts exactly one JSON document on stdout and routes every notice/log
to stderr — clean for scripting; every `ori` subcommand tested returned
this shape (`{"ok": bool, "command": str, "data": {...}}`).

### Real invocation and JSON output — identical to native Claude Code

```
ori claude --model openai/gpt-4o-mini -- -p "reply with exactly the word ok" --output-format json
```

```json
{"type":"result","subtype":"success","is_error":false,"result":"ok",
 "session_id":"230c49a4-...","total_cost_usd":0.0009183,
 "usage":{...},"modelUsage":{"openai/gpt-4o-mini":{...}},
 "permission_denials":[],"terminal_reason":"completed", ...}
```

This is byte-for-byte the same `result`-event schema Cosmo's
`ClaudeCodeAdapter` already parses for native Anthropic-billed calls —
`type`, `subtype`, `total_cost_usd`, `session_id`, `permission_denials`,
`usage` all present and populated correctly against a real OpenRouter model
(`openai/gpt-4o-mini`). **Model routing works; result parsing needs zero
new logic**, only a new code path for how the argv/env get built.

### Gating — confirmed real, not dead code

Reused Cosmo's actual deployed hook shape
(`templates/harness/claude/settings.json`'s `PreToolUse` mechanism) in a
scratch `.claude/settings.json` + `.claude/hooks/deny_bash.py` that exits 2
unconditionally for any `Bash` call, then ran a real prompt asking the
model to `touch denied_marker.txt` via `ori claude`:

```
ori claude --model openai/gpt-4o-mini -- -p "Run the shell command: touch denied_marker.txt..." \
  --output-format json --permission-mode acceptEdits
```

Result: the file was **never created**. The terminal `result` object's
`permission_denials` array populated with the exact blocked call:

```json
"permission_denials":[{"tool_name":"Bash","tool_use_id":"call_...",
  "tool_input":{"command":"touch denied_marker.txt", ...}}]
```

This directly resolves what blocked Cline: a real `PreToolUse`-equivalent
gate, reachable from a one-shot headless invocation, with a structured
(not prose-parsed) signal that it fired.

One gotcha reproduced, already solved once in Cosmo's own adapter and
worth carrying forward: a fresh, never-interactively-trusted directory (any
Cosmo worktree, always) makes Claude Code print (to stderr, not part of the
JSON stream) *"Ignoring N permissions.allow entries from .claude/settings.json:
this workspace has not been trusted"* and silently drop the settings-file
allow list — **but the deny-side hook still fired and blocked regardless**,
since hook denial doesn't depend on workspace trust the way an allow list
does. `src/cosmo/harness/claude/adapter.py`'s existing workaround (passing
`--allowedTools Write Edit Bash` as a CLI flag instead of relying solely on
settings.json) is unaffected by Ori — flags placed after `ori claude --
...` reach `claude` completely untouched, confirmed by inspecting the real
argv Ori constructs (see below).

### What Ori actually does to argv/env (found by shimming `claude`)

Temporarily replaced `claude` on `PATH` with a script that dumps its
environment and args instead of running anything, then called `ori claude
--model openai/gpt-4o-mini -- -p hi --output-format json` for real. Ori:

- Sets `ANTHROPIC_API_KEY` on the child's OS environment to the **real
  OpenRouter key** (`sk-or-v1-...`), `ANTHROPIC_BASE_URL=https://openrouter.ai/api`,
  and `ANTHROPIC_MODEL=<the --model value>` directly as env vars.
- Additionally passes a `--settings '{"apiKeyHelper":"printf %s \"$OPENROUTER_API_KEY\"", "env":{...}}'`
  CLI argument to `claude` — an `apiKeyHelper` that re-derives the key from
  `$OPENROUTER_API_KEY` at call time (belt-and-suspenders beyond the static
  env var), plus an `env` block that explicitly blanks every *other*
  provider-routing var Claude Code recognizes (`ANTHROPIC_AWS_BASE_URL`,
  `..._BEDROCK_...`, `..._VERTEX_...`, `..._FOUNDRY_...`,
  `CLAUDE_CODE_USE_BEDROCK`, etc.) — defensive scrubbing so a stray
  operator-global config can't redirect the call to a real cloud provider
  by accident.
- Isolates Claude Code's local credential/session storage into
  `~/.ori/claude-secure-storage` (a `CLAUDE_SECURESTORAGE_CONFIG_DIR`
  override) rather than touching the operator's real `~/.claude`.
- **Consumes `--model` itself** — it does not forward a `--model` flag to
  `claude` at all, translating it into `ANTHROPIC_MODEL` instead. An
  adapter built on this must route model selection through `ori claude
  --model <id> -- ...` and must **not** also append its own `--model`
  flag after `--` the way the native adapter does today — that would be a
  second, conflicting flag on the far side of `--`, not an override.
- Does **not** touch `--setting-sources`, `--allowedTools`,
  `--permission-mode`, or any other flag placed after `--` — all reached
  `claude` exactly as passed, confirmed via the shim's argv dump.

**Direct consequence for adapter design**: the existing adapter's
`_build_env()` unconditionally does `env.pop(BILLING_ENV_VAR, None)` where
`BILLING_ENV_VAR` is `ANTHROPIC_API_KEY`, scrubbed because a *real*
Anthropic key switches native Claude Code from subscription to metered
billing — spec 2.3's billing footgun. **That check does not apply, and
must not run, for an Ori-routed variant**: Ori's `ANTHROPIC_API_KEY` is an
OpenRouter-shaped key, not a real Anthropic one, and scrubbing it would
break the call. The honest analogue for an Ori-routed path is to require
`OPENROUTER_API_KEY` (or a resolvable `ori auth`) instead, in `preflight()`.

### Cancel / process-group behavior — confirmed clean

`ori claude` **execs into the real `claude` binary in place** — the process
observed at the launched pid, after a few seconds, was directly `/home/dev/.local/bin/claude
--settings {...} ...`, not a lingering Node/Ori wrapper process with
`claude` as a separate child. Verified with a real long-running call
(`sleep 45` via the Bash tool) started with `start_new_session=True`
(mirroring `ManagedProcess`): `os.killpg(pgid, SIGTERM)` followed by
`SIGKILL` and a `wait()` reaped the whole tree cleanly, no orphan. This
means Cosmo's existing `ManagedProcess`/`cancel_and_reap` machinery needs
**no special-casing** for the Ori-routed path — same pattern as native.

One side-finding, not a defect: without `--setting-sources project` (an
existing native-adapter flag not yet added to this scratch test), the
launched `claude` process also picked up this *research host's own*
global `~/.claude.json` MCP server config (an unrelated `engram` MCP
server spawned as a child) — exactly the failure mode
`src/cosmo/harness/claude/adapter.py`'s comment already documents and
already works around with `--setting-sources project`. Re-confirms that
flag is load-bearing for the Ori path too, not just native.

### Other real findings

- `ori --json`'s uniform `{"ok", "command", "data"}` envelope, and
  `ori auth`'s non-zero exit when unauthenticated, make `preflight()`
  cheap to implement: `ori auth` alone answers "is there a usable
  OpenRouter credential" without spending a model call.
- `ori harness-doctor claude` was not available — only `ori harness-doctor
  codex` exists as of `0.12.1`; no Ori-side pre-flight diagnostic for the
  Claude path specifically yet.
- Telemetry: Ori collects anonymous usage telemetry by default (prints a
  one-time notice on first run), disableable with `ORI_TELEMETRY=0` — an
  adapter's `_build_env()` should set this the same way the native
  adapter already sets `TELEMETRY_ENV` for Claude Code's own telemetry.
- `ori claude --reasoning-effort <max|xhigh|high|medium|low|minimal|none>`
  exists as a first-class flag, "translated to the harness's native
  mechanism" — not tested here, but a candidate lever for later per-role
  model tuning (`harness.propose_model`/etc. territory) if OpenRouter
  reasoning models get used for `PROPOSING`.
- Ori also wraps Codex, Grok Build, OpenCode, Hermes, Pi, DeepSeek Harness,
  Prime Agent, and Kilo Code the same way (`ori <name> -- <args>`) — not
  exercised here, out of scope for this document, but the same
  argv/env-wrapping pattern likely generalizes; each still inherits
  whatever gating story (or lack of one) that tool has natively, since Ori
  changes nothing about the wrapped tool's own tool-execution loop.

## OpenCode

**Version tested**: `opencode-ai` `1.18.26` (npm, `npm install -g opencode-ai`
— note the package is `opencode-ai`, not `opencode`; installing required
`--allow-scripts=opencode-ai` for the postinstall step that fetches the
real platform binary). Native OpenRouter support, no wrapper needed —
`opencode models openrouter` lists the catalog and `OPENROUTER_API_KEY`
resolves automatically with **zero login step**, confirmed by a real
`opencode run` against `openrouter/openai/gpt-4o-mini` with only the env
var set.

### Real invocation and JSON output — a genuine stream, no single terminal event

```
opencode run "reply with exactly the word ok" -m openrouter/openai/gpt-4o-mini --format json
```

Produces NDJSON typed events (`step_start`, `text`, `step_finish`, ...),
each carrying `sessionID` and, on `step_finish`, real `tokens`/`cost`
figures. **Exit code is genuinely 0/1** (verified: 0 on the success above;
1 on a real provider error from a bogus model id, which also emitted a
structured `{"type":"error","error":{"name":"UnknownError","data":{...}}}`
event). Unlike Claude Code's `result` event or Cline's `run_result`, there
is **no single terminal summary object in the run's own stdout stream** —
authoritative cost/token totals require a follow-up call:

```
opencode export <sessionID>
```

which returns a full structured session document (`info.cost`,
`info.tokens`, `info.model`, message/part history). Workable for
`HarnessResult`, but it's a second real call after the run returns, not a
field already sitting in the stream — a genuine adapter-implementation
cost this path has that Ori+Claude Code doesn't.

### Gating — confirmed real via a hand-written plugin, including under `--auto`

No built-in policy engine ships by default; gating is opt-in via a
TypeScript/JS plugin. Wrote one from scratch (not from a tutorial) at
`.opencode/plugin/deny-test.js`:

```js
export const DenyTouch = async () => ({
  "tool.execute.before": async (input, output) => {
    if (input.tool === "bash") {
      throw new Error("blocked by Cosmo research test hook: bash denied unconditionally");
    }
  },
});
```

Ran headlessly with `--auto` (opencode's own docs and `--help` both flag
`--auto` as "auto-approve permissions that are not explicitly denied
(dangerous!)" — the same shape as Claude Code's `acceptEdits`, not a
full bypass):

```
opencode run "Run the shell command: touch denied_marker.txt..." \
  -m openrouter/openai/gpt-4o-mini --format json --auto
```

Result: the file was **never created**. The stream's `tool_use` part
carried a structured deny, not prose:

```json
{"type":"tool_use","part":{"tool":"bash","state":{"status":"error",
  "input":{"command":"touch denied_marker.txt"},
  "error":"blocked by Cosmo research test hook: bash denied unconditionally"}}}
```

The overall run still exited **0** (the model adapted and explained it
couldn't do it) — same shape as Claude Code's `permission_denials` not
flipping `success`: a denied tool call is not a failed run, and Cosmo's
own classifier would need to inspect these structured per-tool-call
`state.error` entries the same way it already would inspect
`permission_denials`, not the exit code.

**Caveat worth flagging, not resolved here**: two real GitHub issues exist
about `tool.execute.before` — one ("hook never fires") that turned out to
be the *reporting plugin's own* bug (a sibling plugin's `tool.execute.after`
worked fine in the same environment), and one open bug specifically about
subagent tool calls not being intercepted by this hook. This research only
exercised the top-level agent's own tool calls, matching how Cosmo's
`implement`/`review`/`propose` calls are invoked today (no subagent
delegation in the current OpenSpec workflow) — but if a future OpenCode
adapter's operating policy ever delegates to subagents, the subagent-hook
gap would need real re-verification before trusting it, the same way
Cline's docs needed re-verification before trusting them.

### Cancel / process-group behavior — confirmed clean

Single process, no wrapper layer (unlike Ori, there's no separate CLI to
exec through — `opencode` *is* the binary). A real long-running call
(`sleep 45` via the bash tool) under `start_new_session=True`: `SIGTERM`
to the process group alone was sufficient — the process was already
`<defunct>` (zombie, successfully signaled) within 2 seconds, no `SIGKILL`
escalation needed. Clean reap, no orphan.

### Other real findings

- Plugin load order (per `opencode.ai/docs/plugins/`, not independently
  re-verified beyond confirming project-local `.opencode/plugin/` loads
  for a one-shot `run`): global config → project config → global plugin
  dir (`~/.config/opencode/plugins/`) → project plugin dir
  (`.opencode/plugin/`). A future adapter's template would ship guardrails
  in the project-local directory, mirroring where
  `templates/harness/claude/hooks/` lives today.
- `--pure` disables *external* plugins — not tested whether project-local
  `.opencode/plugin/` counts as "external" for this flag's purposes; would
  need to confirm a future adapter never accidentally passes `--pure`
  (that would silently defeat its own gating, the same class of mistake
  the security-posture checklist in `write-a-new-adapter.md` already warns
  about for "skip all permissions" flags).
- `opencode session`/`opencode export`/`opencode stats` all read back
  structured, non-prose data — useful beyond just the terminal result:
  `opencode export` alone is enough to reconstruct `files_changed`-style
  info from the message/part history if needed.
- Package-name gotcha for anyone re-running this: `npm install -g opencode`
  is a *different, unrelated* package — the real CLI is `opencode-ai`, and
  its postinstall (which fetches the real ~100MB+ platform binary) is
  blocked by default under npm's `install-scripts` allowlist mechanism,
  needing `npm install -g --allow-scripts=opencode-ai opencode-ai` (or a
  project-level `allow-scripts` config) to actually complete.

## Not researched here

**Hermes** and **Pi** were explicitly in scope for this round but not
hands-on verified — deprioritized after the Ori/OpenCode results came back
positive, since the user's immediate question (which harness to plan
*next*) was answered by these two. Surface-level findings from public
docs/source only, **not verified by real invocation**, so treat these as
leads, not conclusions, the same caveat v11 places on anything not
cross-checked against real behavior:

- **Hermes**: has a real one-shot/JSON mode (`hermes -z`, `--usage-file`,
  per-command `--json`) and a write-approval story that on paper covers
  more than shell commands (`skills.write_approval`, protected
  agent-instruction files always requiring approval since v0.21.0,
  denylist/sandbox for `write_file`/`patch`). But the approval flow reads
  as console/session-oriented (`hermes hooks`, `/skills pending`,
  `/skills approve`) rather than an externally-scriptable synchronous
  hook — structurally closer to Cline's broken desktop-approval shape than
  to Ori/OpenCode's in-process deny hooks. Needs the same real-invocation
  scrutiny Cline got before trusting it.
- **Pi**: ships with **no permission system by default, by explicit
  design** ("four tools, no approval prompts, no sandbox" — bash runs with
  full host permissions). Third-party extensions exist
  (`pi-permission-system`, an ACP extension) but gating would then depend
  on an unofficial, separately-maintained plugin rather than anything Pi
  itself guarantees. Weakest gating story of the four tools looked at
  across this document and v11 combined.

## Sources

- Real invocations of `ori` `0.12.1+e1631f8` and `opencode-ai` `1.18.26`
  against OpenRouter, run on this host 2026-09-01/02, using the same
  user-supplied OpenRouter API key as the Cline research
  (`~/.config/cosmo/.env.cline`, not committed anywhere).
- `ori --help`, `ori login --help`, `ori auth --help`, `ori claude --help`,
  `ori harness-doctor --help` — the CLI's own output, treated as
  authoritative over the docs site per the same lesson v11 already
  established for Cline.
- `opencode --help`, `opencode run --help`, `opencode providers --help`,
  `opencode plugin --help`.
- `https://openrouter.ai/docs/guides/ori/harness`,
  `https://openrouter.ai/blog/announcements/ori-harness/`,
  `https://github.com/OpenRouterTeam/skills/blob/main/skills/install-ori-harness/SKILL.md`
  — useful for install/context, incomplete on headless auth and gating
  (both gaps filled by direct CLI testing instead).
- `https://opencode.ai/docs/plugins/` — plugin file format and hook names;
  cross-checked against a real hand-written plugin rather than trusted
  alone.
- `https://github.com/rtk-ai/rtk/issues/1706`,
  `https://github.com/sst/opencode/issues/5894` — real reported gaps in
  `tool.execute.before` (a reporting plugin's own bug in the first case; an
  open, unresolved subagent-scoping gap in the second), flagged above
  rather than silently trusted past.
