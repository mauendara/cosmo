# Cline CLI — harness-adapter research findings

**Status: research only, no implementation plan.** This document records what
was found while investigating whether [Cline](https://cline.bot)'s CLI could
back a new Cosmo harness adapter, driven through OpenRouter so different
models can be tried against Cosmo. It exists so a future session (or a
different person) doesn't have to re-derive any of this from scratch. It does
**not** propose an adapter design, a `HarnessCapabilities` declaration, a file
layout, or a build plan — see `user-docs/en/how-to/write-a-new-adapter.md` for
what an adapter needs to provide, and treat this document purely as the
factual input to that decision, made in a later session.

Everything below was verified by either reading `cline/cline`'s real source
(cloned locally, not from documentation) or by running the real `cline` CLI
against OpenRouter with a real API key — not inferred from Cline's own docs
site alone, which turned out to be unreliable in places (see
["Where the docs are wrong or dead"](#where-the-docs-are-wrong-or-dead)
below).

- **Cline CLI version tested**: `3.0.60` (npm package `cline`, installed
  2026-09-01). Native binary, no Node/Bun runtime required at install time.
- **Provider tested**: OpenRouter (`-P openrouter`), models
  `openai/gpt-4o-mini` (worked) and `deepseek/deepseek-chat` (one real
  provider-side failure, useful as a negative-path example — see below).
- **Source cloned from**: `https://github.com/cline/cline` (`git clone
  --depth 1`), at the commit whose `apps/cli/package.json` reports version
  `3.0.60` — i.e. the exact same version as the installed CLI, so there is no
  version-drift caveat on any of the source-level findings below.

## Installing and invoking the CLI

```
npm install -g cline
```

Installs a native platform binary via `optionalDependencies`
(`@cline/cli-linux-x64` etc.) resolved by `bin/cline`, a small wrapper around
a ~150MB compiled binary (`bin/.cline`). No `ANTHROPIC_API_KEY`-style
subscription-vs-metered-billing footgun exists for the OpenRouter path the
way it does for Claude Code — OpenRouter is inherently metered, so there is
nothing to accidentally switch.

Headless/non-interactive invocation, as actually used in every test below:

```
cline --json --auto-approve true -P openrouter -m <model-id> \
  -c <cwd> --data-dir <isolated-state-dir> "<prompt>"
```

- `--json`: NDJSON to stdout instead of styled text.
- `--auto-approve true|false`: global tool-approval switch (see
  [Gating](#gating-investigation) — this is coarser than it sounds).
- `-P openrouter -m <model-id> -k <key>` (or `OPENROUTER_API_KEY` env var):
  selects OpenRouter and a specific model id, e.g. `openai/gpt-4o-mini`,
  `deepseek/deepseek-chat`. No forced OAuth/browser flow for API-key
  providers (unlike the `cline`/`openai-codex`/`oca` providers, which fail
  fast with no saved credentials rather than opening a hidden browser flow).
- `-c <path>`: working directory the tools operate in.
- `--data-dir <path>`: isolated local state directory; using a fresh one per
  invocation avoids touching a real `~/.cline` install and (per the CLI's own
  flag description) "enables sandbox mode."
- `--timeout <seconds>`, `--retries <count>` (max consecutive mistakes,
  default 6 per `cli-reference.md`'s Global Options table — the CLI's own
  `--help` output printed a default of 6, though one doc page said 3;
  `--help` is the more authoritative source per Cline's own "Help Menu
  (Source of Truth)" framing), `--id <session-id>` (resume — see
  [Session identity](#session-identity-and-resume) for a known bug).
- `--hooks-dir <path>` (default `~/.cline/hooks`): see
  [Gating investigation](#gating-investigation) — did not work in testing.

Exit code is genuinely zero-vs-nonzero: `0` on a successful run, `1` on a
real failure (verified with a real OpenRouter provider error from
`deepseek/deepseek-chat`, see [JSON output schema](#json-output-schema)
below).

## JSON output schema (confirmed by real invocation)

`--json` emits one JSON object per line (NDJSON). Two documented-and-real
top-level shapes were observed, plus a third that turned out not to be
documented at all:

**`hook_event`** — Cline's own internal lifecycle stream (not related to the
external hooks system despite the name; see
[Gating investigation](#gating-investigation)):

```json
{"ts":"2026-09-02T02:00:20.194Z","type":"hook_event","hookEventName":"agent_start","agentId":"agent_...","taskId":"conv_...","parentAgentId":null}
```

Observed `hookEventName` values: `agent_start`, `agent_error`, `agent_end`,
`tool_call`, `tool_result`. Useful as a liveness/activity signal (roughly
analogous to Claude Code's `system`/`init` heartbeat), not as a gating
mechanism.

**`agent_event`** — the actual model/tool activity stream, wrapping an inner
`event` object with its own `type`:

```json
{"type":"agent_event","event":{"type":"iteration_start","iteration":1}}
{"type":"agent_event","event":{"type":"content_start","contentType":"text","text":"ok","accumulated":"ok"}}
{"type":"agent_event","event":{"type":"content_start","contentType":"tool","toolName":"run_commands","toolCallId":"call_...","input":{"commands":["touch ok.txt"]}}}
{"type":"agent_event","event":{"type":"content_end","contentType":"tool","toolName":"run_commands","toolCallId":"call_...","output":[{"query":"touch ok.txt","result":"","success":true}],"durationMs":12}}
{"type":"agent_event","event":{"type":"usage","inputTokens":3668,"outputTokens":2,"cost":0.0005514,"totalInputTokens":3668,"totalOutputTokens":2,"totalCost":0.0005514}}
{"type":"agent_event","event":{"type":"iteration_end","iteration":1,"hadToolCalls":true,"toolCallCount":1}}
{"type":"agent_event","event":{"type":"done","reason":"completed","text":"...","iterations":1,"usage":{...}}}
```

Tool calls carry `toolName`/`input`/`output`, and `iteration_end` carries
`toolCallCount` directly — a real per-run tool-call counter is available from
the stream.

**`run_result`** — a genuine structured terminal event, emitted once at the
end of the run:

```json
{"type":"run_result","finishReason":"completed","iterations":2,
 "usage":{"inputTokens":7525,"outputTokens":109,"cacheReadTokens":7296,"cacheWriteTokens":0,"totalCost":0.00064695},
 "aggregateUsage":{"inputTokens":7525,"outputTokens":109,"cacheReadTokens":7296,"cacheWriteTokens":0,"totalCost":0.00064695},
 "durationMs":2533,"text":"...",
 "model":{"id":"openai/gpt-4o-mini","provider":"openrouter","info":{"id":"openai/gpt-4o-mini","name":"GPT-4o mini","contextWindow":128000,"maxInputTokens":128000,"maxTokens":16384,"capabilities":["images","files","tools","structured_output","temperature","prompt-cache"],"pricing":{"input":0.15,"output":0.6,"cacheRead":0.075,"cacheWrite":0},"releaseDate":"2024-07-18","family":"gpt-mini","operation":"language"}}}
```

Observed `finishReason` values: `"completed"` (success) and `"error"` (a
real OpenRouter routing failure against `deepseek/deepseek-chat`, message
`"Provider returned error"`, exit code 1 — this is a genuine per-model
reliability variance, not a Cline bug, since the same prompt worked fine
against `openai/gpt-4o-mini`). No explicit `session_id` field on this
object, but the `taskId` seen on the `hook_event` rows for the same run
(e.g. `"conv_1788314570720_smtm83r"`) serves that role.

Cline's own docs (`cli-reference.md`, `usage/cli-overview.md`) additionally
describe a much thinner, generic `{"type":"say"|"ask","text","ts","say"|"ask","reasoning","partial"}`
shape as "the" JSON output schema. That shape was never observed in any real
invocation in this research — the real stream is the three-shape structure
above. The documented generic shape may describe an older CLI version or a
different invocation mode (e.g. the interactive TUI's underlying event feed)
that this research didn't reach.

## Gating investigation

This was the bulk of the research effort, in three parts.

### 1. `CLINE_COMMAND_PERMISSIONS` — documented, but genuinely dead code

Both `docs.cline.bot/cli/cli-reference` and `docs.cline.bot/usage/cli-overview`
document this environment variable, explicitly for headless/CI use:

```
export CLINE_COMMAND_PERMISSIONS='{"allow": ["npm *", "git *"], "deny": ["rm -rf *", "sudo *"]}'
```

Documented schema: `allow: string[]` (glob patterns, if set only matching
commands are permitted), `deny: string[]` (glob patterns, "deny rules always
take precedence"), `allowRedirects: boolean` (default `false`).

**Tested against real invocations and found not to work.** Three
independent, clean tests against `-P openrouter -m openai/gpt-4o-mini`:

- `deny:["*"]` under `--auto-approve true`, asked to `touch
  denied_marker.txt` → file created, `"success":true`.
- `deny:["touch *"]` (unambiguous glob, no wildcard-syntax ambiguity) under
  `--auto-approve true`, asked to `touch specific_deny_test.txt` → same,
  created, no denial anywhere in the JSON stream.
- `allow:["touch *"]` (no deny rule at all) under `--auto-approve false`
  (no TTY) → denied anyway, with the message *"Tool `run_commands` requires
  approval in a TTY session"* — the allow rule did not create an exception
  to the blanket no-TTY denial.

Two theories were tested and ruled out rather than assumed:

- **Tool-name mismatch?** No. `cline`'s own `tools-reference/all-cline-tools.md`
  names the canonical shell tool `bash`, while the real runtime calls it
  `run_commands` — this looked like the explanation at first, but the SDK's
  own `sdk/guides/permission-handling.md` documentation uses `run_commands`
  in its own policy examples (`toolPolicies: { run_commands: {...} }`),
  confirming `run_commands` is the correct, expected wire-level name, not a
  mismatch.
- **Env var not reaching the process?** No. Confirmed with `echo
  "$CLINE_COMMAND_PERMISSIONS"` immediately before invocation, and via
  `cline doctor` (which prints no permission-related diagnostics either
  way).

**Root cause, found by cloning `cline/cline` and grepping the real source**
(not inferred): the string `CLINE_COMMAND_PERMISSIONS` appears exactly once
in the entire monorepo, in `apps/vscode/src/core/prompts/responses.ts`:

```ts
permissionDeniedError: (reason: string) =>
    `Command execution blocked by CLINE_COMMAND_PERMISSIONS: ${reason}. You must try a different approach or ask the user to update the permission settings.`,
```

This is a message *template*. Grepping for callers of `permissionDeniedError`
anywhere in the repo returns **zero results** — nothing calls it. Grepping
`apps/cli/` and `sdk/` (the packages that build the actual standalone
`cline` binary) for `CLINE_COMMAND_PERMISSIONS` or `commandPermissions`
(case-insensitive) also returns **zero results**. The feature documented on
the public docs site corresponds to no working code anywhere in the shipped
CLI or SDK — it is either a leftover from an abandoned VS-Code-extension-only
implementation attempt, or documentation written ahead of code that was
never finished.

### 2. Vendored Claude-Code-style hooks (`--hooks-dir`) — also did not fire

The compiled `cline` binary contains a full Claude-Code-shaped hook
vocabulary verbatim (found via `strings` on the binary before the source
clone was available): `PreToolUse`, `PostToolUse`, `PostToolUseFailure`,
`UserPromptSubmit`, `SessionStart`, `SessionEnd`, a
`{"PostToolUse":[{"matcher":"Edit|Write","hooks":[{"type":"command","command":"..."}]}]}`
config shape, block-via-exit-code-2 semantics, and even literal
`.claude/settings.json`/`managed-settings.json` path handling and a
`"[claude-code] Hook started"` log line — strong evidence Cline vendors (or
closely mirrors) Anthropic's own Claude Agent SDK hook engine internally.

Built a real `hooks.json` under the documented `--hooks-dir <path>` flag:

```json
{
  "PreToolUse": [
    { "matcher": "", "hooks": [{ "type": "command", "command": "<script that always exits 2>" }] }
  ]
}
```

Ran a prompt that made Cline write a file (`apply_patch` tool). **The hook
script never executed** (no evidence of it running was left behind) and the
file write succeeded anyway. This may mean the config file name/nesting
guessed here is wrong (the "plugin" system's own docs mention
`hooks/hooks.json` *relative to a plugin root*, suggesting hooks may need
`cline plugin install` registration rather than a flat file under
`--hooks-dir`), or that this hook path is only reachable via `ClineCore` SDK
embedding (`sdk/plugins`), not the standalone CLI's own tool-execution loop.
Not resolved further — deprioritized once the desktop-approval mechanism
below turned out to be the more promising real lead.

### 3. Desktop tool-approval file-IPC — real, well-designed, and broken by one specific bug

A third, entirely different and genuinely real mechanism exists, found by
reading the real source (`sdk/packages/core/src/runtime/tools/tool-approval.ts`
and `apps/cli/src/utils/approval.ts`):

Setting `CLINE_TOOL_APPROVAL_MODE=desktop` and `CLINE_TOOL_APPROVAL_DIR=<dir>`
routes every tool-approval request through a synchronous file-IPC protocol
instead of a TTY prompt — no terminal required:

1. Cline writes a request file:
   `<approvalDir>/<sessionId>.request.<requestId>.json`, containing
   `requestId`, `sessionId`, `createdAt`, `toolCallId`, `toolName`, `input`,
   `iteration`, `agentId`, `conversationId`.
2. Cline polls (200ms interval, 5-minute default timeout, both configurable)
   for a decision file: `<approvalDir>/<sessionId>.decision.<requestId>.json`,
   expected shape `{"approved": true|false, "reason": "..."}`.
3. Both files are deleted once a decision is read; a timeout resolves to
   `{approved: false, reason: "Tool approval request timed out"}`.

This is a genuine, synchronous, per-tool-call, external gate — structurally
equivalent to what a PreToolUse hook provides, and does not require a TTY.

Separately confirmed: in the CLI's one-shot (non-interactive) invocation
path, `--auto-approve false` sets a single wildcard tool policy
(`toolPolicies = {"*": {autoApprove: false}}`, built in `apps/cli/src/main.ts`
around line 896) with **no safe-tool carve-out** — unlike the interactive
TUI, which has a `SAFE_AUTO_APPROVE_TOOLS` allowlist
(`apps/cli/src/utils/approval.ts`) for tools like `read_files`/`search`. In
the one-shot path, *every* tool call — reads included — would route through
the approval gate if it worked, which is actually the stronger, more useful
behavior for an external gate to build on.

**Tested end-to-end and it does not work today.** Ran `cline` in the
background with `--auto-approve false`, `CLINE_TOOL_APPROVAL_MODE=desktop`,
and `CLINE_TOOL_APPROVAL_DIR` set, watching for a request file to appear so
it could be answered. No request file ever appeared. The tool call instead
failed immediately with `{"error":"Desktop tool approval IPC is not
configured"}`.

**Root cause, confirmed from source: a real ordering bug in
`apps/cli/src/runtime/run-agent.ts`**, the one-shot/headless entry point.
`requestDesktopToolApprovalFromCore` (`apps/cli/src/utils/approval.ts`)
reads the session id from a module-level singleton,
`getActiveCliSession()?.manifest.session_id`. In `run-agent.ts`,
`setActiveCliSession(...)` is called at line 310 — **after**
`const started = await sessionManager.start(...)` on line 283. But for a
non-interactive one-shot prompt, `run-agent.ts`'s own comment confirms the
entire first turn — including any tool calls — already executes **during**
that `start()` call:

> *"When start() already ran the first turn (non-interactive with prompt),
> the session is finalized before start() returns."*

So every tool-approval request in a one-shot run fires before
`activeCliSession` is ever set, `sessionId` is `undefined`, and the desktop
IPC path immediately reports itself as "not configured" — unconditionally,
regardless of provider or model. This is not a configuration mistake on the
calling side; it reproduces every time.

For comparison, the interactive TUI path
(`apps/cli/src/runtime/interactive/session-runtime.ts`, `applyStartedSession`)
calls the equivalent function right after session creation and before any
user turn — structurally different ordering, so this bug appears to be
**specific to the one-shot/headless invocation path** (untested directly in
TUI mode, but the code path is unambiguously different there).

### Net conclusion on gating

As of Cline CLI `3.0.60`, no gating mechanism reachable from headless
one-shot invocation actually works:

| Mechanism | Documented? | Wired to real code? | Works headlessly today? |
|---|---|---|---|
| `CLINE_COMMAND_PERMISSIONS` | Yes | No (dead code, zero call sites) | No |
| `--hooks-dir` / `hooks.json` (Claude-Code-shaped) | Partially | Present in binary, integration unconfirmed | No (tested, did not fire) |
| `CLINE_TOOL_APPROVAL_MODE=desktop` file-IPC | Yes | Yes, real and reachable | No — blocked by one specific, well-scoped ordering bug in `run-agent.ts` |

The third mechanism is the one worth watching: it is a real, well-designed,
already-implemented feature, blocked by a single identifiable bug rather
than a missing feature. A future Cline release (or a small upstream patch)
could plausibly make it work without any other change.

## Where the docs are wrong or dead

- `docs.cline.bot` is a Mintlify-hosted SPA. Fetching pages with a
  JS-unaware tool (this research initially used a generic WebFetch tool)
  returned stub/redirect content for several pages that do have real
  content when fetched as raw HTML and parsed properly, or fetched directly
  as Markdown via the `<page>.md` suffix (every page has one, and
  `https://docs.cline.bot/llms.txt` lists all of them). **Prefer the `.md`
  URL over the rendered page for any future research here.**
- `CLINE_COMMAND_PERMISSIONS` is documented in detail (see above) but is
  dead code with zero implementation anywhere in the shipped CLI/SDK.
- The `tools-reference/all-cline-tools.md` page's canonical tool name
  `bash` does not match the real runtime tool name `run_commands` seen in
  every real invocation and confirmed correct by the SDK's own
  `permission-handling` guide examples — the tools-reference page appears to
  be stale or describes an aliasing layer not exercised by the OpenRouter
  provider path.
- The generic `{"type":"say"|"ask",...}` JSON schema documented on
  `cli-reference.md`/`usage/cli-overview.md` was never observed in any real
  invocation; the real schema is the three-shape `hook_event`/`agent_event`/
  `run_result` structure documented above.
- `--retries` default is documented as `3` in `apps/cli/README.md` but the
  installed CLI's own `--help` output says `6` — used `--help` as the
  authoritative source per Cline's "Help Menu (Source of Truth)" framing in
  its own `cli-reference.md`.

## Other confirmed facts, not yet exercised in depth

- **Project-level operating-policy file**: `.clinerules/` — a directory of
  `.md`/`.txt` files, mergeable, with YAML-frontmatter path conditions for
  scoping a rule to part of a project. This is the closest analogue to
  Claude Code's `CLAUDE.md`.
- **Config file layout** (from `cli-reference.md`'s "Configuration Files"
  section): global state under `~/.cline/data/` (`settings/providers.json`,
  `settings/rules/`, `settings/skills/`, `teams/`, `sessions/` — a SQLite
  session DB, `logs/`, `plugins/_installed/`); project-level state under
  `.cline/` at the project root (`rules/`, `skills/`, `hooks/`, `plugins/`,
  `mcp.json`, `agents.yaml`).
- **Model selection needs no new Cosmo config surface**: `-m <model-id>`
  just takes an OpenRouter model id string (e.g.
  `anthropic/claude-sonnet-4.6`, `deepseek/deepseek-chat`,
  `openai/gpt-4o-mini`); Cosmo's existing
  `harness.model`/`propose_model`/`implement_model`/`review_model` config
  keys already generalize across adapters.

## Session identity and resume

- `taskId` (seen on `hook_event` rows, e.g.
  `"conv_1788314570720_smtm83r"`) is the closest thing to a session
  identifier in the JSON stream; `run_result` carries no explicit
  `session_id` field of its own.
- `--id <session-id>` is documented as a resume flag. Not tested directly in
  this research. A public GitHub issue (cline/cline#10856, "Cline CLI 3.0.7
  --json mode can't resume an existing session ID") reports `--json` + `--id`
  resume not working as expected as of CLI 3.0.7 — flagged here as an
  unverified but credible known limitation, not independently confirmed.

## Environment/security notes relevant to any future adapter work

- No Claude-Code-style billing footgun: OpenRouter is inherently metered,
  so there is no equivalent to `ANTHROPIC_API_KEY` silently switching from
  subscription to per-token billing. The inverse risk exists instead: since
  the whole point of this harness would be trying arbitrary models, there is
  no built-in upper bound on a single call's cost.
- `--data-dir <path>` gives per-invocation state isolation and documents
  itself as enabling "sandbox mode," but this isolates Cline's *own* local
  state (sessions, settings) — it says nothing about filesystem or
  credential isolation for the worktree the agent's tools actually operate
  in, which is a Cosmo-side concern, not a Cline one.
- `CLINE_LOG_ENABLED=0` disables Cline's own runtime file logging; no
  equivalent to Claude Code's `OTEL_LOG_USER_PROMPTS` content-logging
  toggle was found in this research (an `OpenTelemetry Events Reference`
  doc page exists on the docs site but was not read in depth here).

## Sources

- `https://github.com/cline/cline` (cloned `--depth 1`, matching
  `apps/cli` version `3.0.60`) — authoritative for everything under
  [Gating investigation](#gating-investigation).
- `https://docs.cline.bot/llms.txt` — full page index; append `.md` to any
  listed path for a clean, non-SPA-rendered fetch.
- `https://docs.cline.bot/cli/cli-reference`,
  `https://docs.cline.bot/usage/cli-overview`,
  `https://docs.cline.bot/tools-reference/all-cline-tools`,
  `https://docs.cline.bot/sdk/guides/permission-handling`,
  `https://docs.cline.bot/provider-config/openrouter`.
- `apps/cli/README.md` inside the `cline/cline` repo (more complete/current
  than some rendered docs pages, per direct comparison during this
  research).
- Real invocations of `cline` 3.0.60 against OpenRouter, run on this host
  2026-09-01/02, using a user-supplied OpenRouter API key
  (`~/.config/cosmo/.env.cline`, not committed anywhere).
