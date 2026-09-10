# v13 — Ori-routed Claude Code as a second harness adapter and template

**Status: plan only. Nothing implemented, no code changed.** This document
turns [v12-ori-opencode-harness-info.md](v12-ori-opencode-harness-info.md)'s
research findings into a build plan for one specific thing: running the real
Claude Code binary through **Ori** (`ori claude`, routed to OpenRouter) as a
**second, separately-named, separately-templated harness**, alongside — not
replacing, not reconfiguring — the existing native `claude` adapter.

OpenCode is explicitly **not** in scope here (v12 records it as the stronger
long-term bet but a full new adapter, new stream parser and TS guardrail
plugins); this plan does not prejudge it.

## What "two harnesses" means concretely

| | `claude` (existing) | `ori-claude` (this plan) |
|---|---|---|
| Binary launched | `claude` | `ori`, which execs `claude` in place |
| Billing | Pro/Max subscription; `ANTHROPIC_API_KEY` is a hard preflight **fail** (spec 2.3 footgun) | OpenRouter, metered per token; `OPENROUTER_API_KEY` is the credential, `ANTHROPIC_API_KEY` is scrubbed because Ori supplies its own |
| Model ids | `claude-sonnet-5`, … | OpenRouter ids: `anthropic/claude-sonnet-4.5`, `openai/gpt-5`, `qwen/qwen3-coder`, … |
| Template | `templates/harness/claude/` | `templates/harness/ori-claude/` |
| Root symlinks | `CLAUDE.md`, `.claude`, `agents`, `skills` → `.agent/claude/` | same four names → `.agent/ori-claude/` |
| Gating | `PreToolUse` hooks | the same hooks, same binary — confirmed firing through Ori (v12) |
| Stream / result parsing | `stream.py` | **identical**, reused unchanged |

Both stay selectable exactly as today: `harness.name` in config, `--harness`
on any command, or per-project via `cosmo init --harness`. No core code
learns that either exists.

## Decisions taken before planning

These four were put to the user and answered; the rest of this document
assumes them.

1. **Code sharing: extract a shared invoker, two thin adapters.** The
   Claude-Code-invoking mechanics (`_invoke`, `ManagedProcess` wiring,
   `StreamReader` hookup, `cancel`, `HarnessResult` mapping, the
   `_relay_activity`/`_summarize` helpers) move into a shared internal base;
   `ClaudeCodeAdapter` and `OriClaudeAdapter` each become a small class that
   declares `name`, `capabilities`, `_build_argv`, `_build_env` and
   `preflight`. Rejected: subclassing `ClaudeCodeAdapter` directly (makes one
   sibling the implicit base of the other), and full duplication (re-writes
   the cancel/process-group logic that `write-a-new-adapter.md` itself names
   as the easiest thing to get wrong).
2. **Template: a full independent copy** at `templates/harness/ori-claude/`.
   Templates are data, synced wholesale; a self-contained directory is
   auditable by reading one place, and lets the operating policy genuinely
   diverge for non-Anthropic models. Rejected: a template-inheritance
   mechanism and a shared `_shared/` tree — both add new load-bearing code to
   the sync path and a new `template_version` hashing failure mode for a
   second case that doesn't need it yet. The accepted cost (hook scripts
   existing twice) is bought back by a **parity test** (Phase 5).
3. **Model config: per-harness override sections.** New optional
   `[harness.overrides.<harness-name>]` tables carrying their own
   `model`/`propose_model`/`implement_model`/`review_model`, falling back to
   `[harness]` when unset. One config file can then hold sane models for both
   harnesses and switching is a `--harness` flag, not a config edit.
4. **Scope: Ori + Claude Code only**, named `ori-claude` so a future
   `ori-codex`/`ori-opencode` needs no rename. No generic Ori-wrapper layer —
   v6's status is the standing evidence that abstracting ahead of the second
   real case costs more than it saves.

## What v12 already proved, and what it did not

Load-bearing facts this plan builds on, all confirmed by real invocation
(v12, this host, 2026-09-01/02):

- `ori claude -- <args>` passes everything after `--` to the real `claude`
  binary untouched — verified by shimming `claude` and dumping argv.
- `PreToolUse` hooks fire and **block** through Ori; the denial surfaces
  structurally in the terminal result's `permission_denials`.
- The terminal `result` JSON is byte-for-byte the schema `stream.py` already
  parses, including a populated `total_cost_usd` against a real OpenRouter
  model.
- `ori` **execs in place** (same pid) — `ManagedProcess`'s
  SIGTERM→SIGKILL-on-pgid pattern needs no special-casing.
- `OPENROUTER_API_KEY` resolves headlessly with no login step.
- Ori **consumes `--model` itself** and translates it to `ANTHROPIC_MODEL`;
  it does not forward a `--model` flag to `claude`.
- Ori sets `ANTHROPIC_API_KEY` on the child to the OpenRouter key, and
  additionally passes `--settings '{...}'` with an `apiKeyHelper` and an
  `env` block blanking every other provider-routing var.

Facts this plan **assumes but must verify with a real invocation** before it
can be called done — each is a Phase 6 item, not a hand-wave:

- **V1.** `--output-format stream-json --verbose` through Ori. v12 only
  exercised `--output-format json`. Same binary, so this is expected to work
  — but the adapter's whole liveness and cost story depends on it, and this
  project's own convention is to check rather than assume.
- **V2.** Ori's inline `--settings '{...}'` argument coexisting with the
  adapter's `--setting-sources project` without dropping the project's
  `settings.json` hooks. v12 confirmed hooks fire through Ori, but *without*
  `--setting-sources project` in the argv. If these interact badly, gating
  silently disappears — the single highest-consequence unknown in this plan.
- **V3.** No `--model` conflict: `ori claude --model X -- ...` with **no**
  `--model` after `--`, and `ANTHROPIC_MODEL` actually honored end to end.
- **V4.** Whether any quota/rate-limit-shaped stream event appears at all
  through OpenRouter (expected: none — see "Quota and cost" below).

## Phase 1 — Extract the shared Claude Code invoker

**Files:** new `src/cosmo/harness/claude/invoker.py`; `src/cosmo/harness/
claude/adapter.py` slims down.

Move, unchanged in behavior, out of `adapter.py` into a
`_ClaudeCodeInvoker(HarnessAdapter)` base:

- `__init__` (config, cwd, binary, run_id, emitter, lock, `_running` dict)
- `_invoke()` — raw-log path, `StreamReader`, `ManagedProcess`, `wait()`,
  the `finally` finalize, `HarnessResult` assembly
- `cancel()`
- `probe`/`propose`/`implement`/`review`/`get_progress` — the prompts are
  harness-independent (they talk about OpenSpec and `REVIEW_RESULT_RELATIVE_PATH`,
  not about Claude), so they live here too
- module-level `_relay_activity`, `_extract`, `_summarize`
- the telemetry/task-id/db-path env constants that are Claude-Code-wide

Left abstract for the two concrete adapters:

```python
class _ClaudeCodeInvoker(HarnessAdapter):
    @abstractmethod
    def _build_argv(self, prompt: str, model: str) -> list[str]: ...
    @abstractmethod
    def _build_env(self, task_id: str) -> dict[str, str]: ...
```

Plus one shared helper both `_build_argv`s call, so the security-critical
flag set cannot drift between them:

```python
def _claude_flags(self, prompt: str) -> list[str]:
    """Everything after `-p` that is identical for both routes: --output-format
    stream-json --verbose, --max-turns, --permission-mode, --setting-sources
    project, --allowedTools Write Edit Bash. Notably NOT --model: the native
    route passes it here, the Ori route passes it to `ori` before `--`."""
```

The two `assert` statements pinning `--dangerously-skip-permissions` and
`bypassPermissions` out of argv stay in the shared helper **and** are
re-asserted in each `_build_argv` on the final list, so an adapter that
prepends its own wrapper flags can't reintroduce them.

Model-role resolution also moves here, so both adapters get per-harness
overrides for free:

```python
model = self.config.harness.resolve_model(self.name, "implement")
```

**Exit criteria:** `tests/test_harness_claude_adapter.py` passes untouched
except for import paths. `./check.sh` green. No behavior change to the
native path — this phase is a pure refactor and should be its own commit.

## Phase 2 — Per-harness model config

**Files:** `src/cosmo/config/model.py`, `src/cosmo/config/defaults.toml`.

```python
class HarnessModelOverrides(_Strict):
    """Per-harness model ids. A harness's model namespace is its own -- an
    OpenRouter id (`openai/gpt-5`) is meaningless to native Claude Code and a
    bare `claude-sonnet-5` is meaningless to OpenRouter -- so a host that runs
    both cannot express both in one flat `[harness]` table."""

    model: str | None = None
    propose_model: str | None = None
    implement_model: str | None = None
    review_model: str | None = None


class HarnessConfig(_Strict):
    name: str = Field(min_length=1)
    permission_mode: str = Field(min_length=1)
    max_turns: int = Field(gt=0)
    model: str = Field(min_length=1)
    propose_model: str | None = None
    implement_model: str | None = None
    review_model: str | None = None
    overrides: dict[str, HarnessModelOverrides] = Field(default_factory=dict)

    def resolve_model(self, harness: str, role: str) -> str:
        """Resolution order, narrowest first:
        overrides[harness].<role>_model -> overrides[harness].model
        -> <role>_model -> model.

        `harness` is an opaque key, never matched against a literal here --
        this method stays harness-agnostic in exactly the way
        `resolve_harness_name` does."""
```

`role` is one of `"probe" | "propose" | "implement" | "review"`; `"probe"`
resolves straight to the `model` rungs, matching today's behavior.

`defaults.toml` gains a commented-out example block only (no active
overrides shipped — the default host has one harness):

```toml
# Per-harness model overrides. Each harness's model namespace is its own;
# an OpenRouter model id means nothing to native Claude Code and vice versa.
# [harness.overrides.ori-claude]
# model = "anthropic/claude-sonnet-4.5"
# propose_model = "openai/gpt-5"
# implement_model = "qwen/qwen3-coder"
# review_model = "google/gemini-2.5-pro"
```

**One core call site changes**: `cli/main.py`'s `spec add` currently passes
`cfg.harness.propose_model` directly into `adapter.probe(...)`. That becomes
`cfg.harness.resolve_model(resolved_name, "propose")` — still
harness-agnostic (the name is a variable), but now correct when the active
harness has overrides.

**Exit criteria:** `resolve_model` unit-tested across all four rungs and the
fallback chain; `extra="forbid"` still rejects a typo'd key inside an
override table; existing config tests unchanged.

## Phase 3 — The `OriClaudeAdapter`

**Files:** new `src/cosmo/harness/ori/__init__.py`, `src/cosmo/harness/ori/
adapter.py`; one line in `src/cosmo/harness/registry.py`.

```python
ORI_BINARY = "ori"
ORI_SUBCOMMAND = "claude"
CLAUDE_BINARY = "claude"  # exec'd by ori; checked in preflight only
CREDENTIAL_ENV_VAR = "OPENROUTER_API_KEY"
ORI_TELEMETRY_ENV = {"ORI_TELEMETRY": "0"}


class OriClaudeAdapter(_ClaudeCodeInvoker):
    name: ClassVar[str] = "ori-claude"

    capabilities: ClassVar[HarnessCapabilities] = HarnessCapabilities(
        reports_native_progress=False,
        supports_retry_context=True,
        has_internal_timeout=False,
        reports_native_cost=True,  # total_cost_usd populated -- v12, real invocation
        supports_gating=True,  # PreToolUse hooks fire and block -- v12, real invocation
        supports_structured_stream=True,  # same binary, same stream-json (verify: V1)
    )
```

Constructor adds `ori_binary` and `claude_binary` as keyword-only, defaulted,
injectable for tests — same pattern `write-a-new-adapter.md` prescribes.

### `_build_argv`

```
[ori, claude, --model, <resolved model>, --, -p, <prompt>, *self._claude_flags(prompt)]
```

The `--model` placement is the one genuinely Ori-specific fact in the whole
adapter, and it carries a comment citing v12's argv-shim finding: Ori
consumes `--model` and translates it to `ANTHROPIC_MODEL`, so a second
`--model` after `--` is a *conflicting flag*, not an override. A test asserts
`argv.count("--model") == 1` and that its index is before the `--`.

### `_build_env`

- `env.pop("ANTHROPIC_API_KEY", None)` — **kept**, for a different reason
  than the native adapter's. Ori sets its own `ANTHROPIC_API_KEY` on the
  child unconditionally (v12), so scrubbing the operator's cannot break the
  call, and it preserves spec 2.3's footgun protection if that ever changes.
  The comment must say *this* reason, not the native one.
- pass `OPENROUTER_API_KEY` through untouched (it is the credential).
- `ORI_TELEMETRY=0` alongside the existing Claude Code telemetry vars —
  v12 flagged Ori's own anonymous telemetry as on by default.
- `COSMO_TASK_ID` / `COSMO_DB_PATH` as today (the guardrail hooks read them).

### `preflight`

Cheap and side-effect free — `PATH` lookups and env reads only, no `ori auth`
call (that is a network round-trip; `preflight` runs inside scripts).

| Check | Outcome |
|---|---|
| `ori` on PATH | `check_executable` |
| `claude` on PATH | `check_executable` — Ori execs it; a missing `claude` fails at run time with a confusing error otherwise |
| `OPENROUTER_API_KEY` set | `ok`; **`warn`** if unset (not `fail`: `ori login --with-key` stores a credential file, so an unset env var is not proof of no auth) |
| `ANTHROPIC_API_KEY` set | `ok` either way, detail noting it is scrubbed and Ori supplies its own — **never** the native adapter's hard `fail` |
| `permission_mode` | same forbidden/supported check as native (shared helper) |
| `cost.max_cost_per_run_usd == 0` | **`warn`**: unlike subscription-billed native Claude Code, this harness is metered per token and the cost hard stop is disabled |

That last row is the honest inverse of the native adapter's billing check,
and is the reason this adapter's preflight is not simply inherited.

### Registry

```python
_REGISTRY = {
    ClaudeCodeAdapter.name: ClaudeCodeAdapter,
    OriClaudeAdapter.name: OriClaudeAdapter,
    FakeHarnessAdapter.name: FakeHarnessAdapter,
}
```

`cosmo harness list`, `cosmo doctor --harness ori-claude`, `cosmo templates
list`, `--harness ori-claude` on every command, and `cosmo init --harness
ori-claude` all then work with **zero** further CLI changes.

## Phase 4 — The `ori-claude` template

**Files:** new `templates/harness/ori-claude/` (full copy of
`templates/harness/claude/`), one entry in `src/cosmo/bootstrap/symlinks.py`.

Deltas from the copied tree, all of them real and none cosmetic:

1. **`settings.json`** — every hook command path becomes
   `$CLAUDE_PROJECT_DIR/.agent/ori-claude/hooks/...`. A hook pointing at
   `.agent/claude/` in an `ori-claude` worktree silently does not exist,
   which means it silently does not block; this is the file to get right.
2. **`settings.json`** — the pinned `"model": "claude-sonnet-5"` is
   **removed**. Model routing on this path is `ori --model` →
   `ANTHROPIC_MODEL`; leaving an Anthropic model id in the settings file
   invites a conflicting or nonsensical value at OpenRouter.
3. **`CLAUDE.md`** — the "This call is one-shot" section says `claude -p`;
   it becomes `ori claude -- -p`, with the same substance. The
   background-task prohibition, the validation-gate-is-truth rule, the
   OpenSpec instructions and the guardrail table are all model-independent
   and are copied verbatim.
4. **`CLAUDE.md`** — one added paragraph: the model behind this session may
   not be an Anthropic model, so it must not assume Claude-specific tool
   affordances or defaults, and must follow the written policy rather than
   habit. (The `agents/` and `skills/` trees are copied unchanged; nothing
   in them is Anthropic-specific.)
5. **`hooks/`** — copied byte-for-byte, and **held** byte-for-byte by the
   Phase 5 parity test.

`symlinks.py`:

```python
HARNESS_ROOT_LINKS = {
    "claude": (
        ("CLAUDE.md", "CLAUDE.md"),
        (".claude", ""),
        ("agents", "agents"),
        ("skills", "skills"),
    ),
    # Same four names: the tool reading them is still Claude Code, so it still
    # looks for `.claude`/`CLAUDE.md` -- only the `.agent/<harness>/` tree they
    # resolve into differs.
    "ori-claude": (
        ("CLAUDE.md", "CLAUDE.md"),
        (".claude", ""),
        ("agents", "agents"),
        ("skills", "skills"),
    ),
}
```

**Accepted limitation, documented not engineered around:** a repo `init`-ed
for `claude` and later re-`init`-ed for `ori-claude` gets its four symlinks
re-pointed (`create_root_symlinks` refreshes links it owns) but keeps a stale
`.agent/claude/` tree on disk. It is inert — nothing reads it once the links
move — but it is confusing in a `git status`. `cosmo init` will print a line
naming any other `.agent/<harness>/` directory it found; removing it stays a
human decision, consistent with `create_root_symlinks` never clobbering
content it did not create.

## Phase 5 — Tests

New `tests/test_harness_ori_adapter.py`:

1. **argv shape** — `ori claude --model <id> --` prefix; exactly one
   `--model`, before `--`; `-p` and the prompt after `--`; `stream-json`,
   `--setting-sources project`, `--allowedTools Write Edit Bash` present;
   `--max-turns`/`--permission-mode` carry config values.
2. **permission test** — `--dangerously-skip-permissions` and
   `bypassPermissions` absent, asserted from the outside (mirrors the
   existing native test).
3. **env** — `ANTHROPIC_API_KEY` scrubbed, `OPENROUTER_API_KEY` passed
   through, `ORI_TELEMETRY=0`, `COSMO_TASK_ID`/`COSMO_DB_PATH` set.
4. **preflight** — each row of the table above, including that a set
   `ANTHROPIC_API_KEY` is *not* a failure here and that a zeroed cost cap
   warns.
5. **cancel** — fixture script forking a SIGTERM-ignoring child; assert the
   whole process group is gone. Not inherited from the native test: this is
   the failure that costs a host.
6. **result mapping** — feed the existing recorded `stream-json` fixtures
   through and assert `HarnessResult` fields, proving the shared parser is
   genuinely shared.
7. **model resolution** — a config with `[harness.overrides.ori-claude]` set
   produces the OpenRouter id in argv for each of the three roles, and the
   native adapter in the same config is unaffected.

New `tests/test_harness_template_parity.py`:

- every file under `templates/harness/ori-claude/hooks/` is byte-identical to
  its `templates/harness/claude/` counterpart, and the two hook directories
  have identical file lists. This is what makes the accepted duplication
  safe: drift becomes a test failure, not a silent gating hole.
- every hook command string in `templates/harness/ori-claude/settings.json`
  references `.agent/ori-claude/`, and none references `.agent/claude/`.
- `templates/harness/ori-claude/settings.json` declares no `model` key.

Updates to existing tests:

- `tests/test_harness_boundary.py`: add `harness/claude/invoker.py`,
  `harness/ori/__init__.py`, `harness/ori/adapter.py` to
  `ALLOWED_HARNESS_AWARE`; add `OPENROUTER_API_KEY`, `ORI_TELEMETRY` and
  `"ori-claude"` to the harness-specific token/name checks so core can never
  learn them either. The existing `["']claude["']` check keeps working — the
  Ori adapter names `"claude"` as an `ori` subcommand and is allowlisted.
- `tests/test_bootstrap_symlinks.py`: parametrize over both harnesses.
- `tests/test_harness_registry.py`: the capability-completeness loop picks
  the new adapter up for free; assert `ori-claude` is registered by name.
- `tests/test_config.py`: `resolve_model` fallback chain, and that an
  unknown key inside an override table still raises.

## Phase 6 — Real-invocation validation

Unit tests cannot prove any of the four unknowns above. Each of these runs
against a **scratch** repo in a scratch directory (never a real target repo),
and is cleaned up afterward — worktree and branch removed, seeded rows
deleted in FK order, scratch repo deleted — per the handoff's standing rule.

| # | Validation | Fails the plan if |
|---|---|---|
| V1 | `cosmo harness probe --harness ori-claude --prompt "reply with the word ok"` | no `stream-json` NDJSON, or no terminal `result` with `total_cost_usd`/`session_id` |
| V2 | A `Bash`-denying `PreToolUse` hook in an `ori-claude`-synced scratch worktree, driven through the **real adapter argv** (with `--setting-sources project`) | the file gets created, or `permission_denials` is empty — meaning Ori's inline `--settings` displaced the project hooks |
| V3 | Inspect the launched `claude`'s real argv/env (the v12 shim technique) | a second `--model` reaches `claude`, or `ANTHROPIC_MODEL` is not the requested id |
| V4 | A long-running `Bash` call cancelled mid-flight via `adapter.cancel` | any surviving process in the group |
| V5 | Full `cosmo run --harness ori-claude` on a scratch project, one small task, propose → implement → gate → review → commit | any stage fails for a reason traceable to the adapter rather than the model's own output |
| V6 | Note whether any rate-limit-shaped stream event appears at all | (informational — records the real quota-degradation behavior, see below) |

**V2 is the gate on the whole plan.** If Ori's `--settings` argument turns
out to displace the project's `settings.json` hooks under `--setting-sources
project`, `supports_gating=True` becomes a lie and the adapter must either
find another route (e.g. dropping `--setting-sources project` and accepting
the operator-global-config leak, which is its own spec 2.5 problem) or
declare `supports_gating=False` and be honest that it is diff-gate-only.
Decide that against real output; do not ship a guess either way.

## Quota and cost — what degrades, deliberately

- **Quota detection.** `stream.py`'s primary signal is Anthropic's
  `rate_limit_event`/`system/api_retry` shape. Through OpenRouter, expect
  neither to appear (V6 records the truth). `extract_quota_signal` then
  returns `(None, None)`, `HarnessResult.quota_window` stays `None`, and
  `run.quota` degrades to its secondary (`quota.result_error_subtypes`) and
  tertiary (wall-clock heuristic) detectors — **exactly the documented
  fallback for a harness with no primary signal** (spec 7.2). No new code,
  no new capability flag; this is the abstraction working as designed. It
  gets one sentence in the docs, not a special case in the parser.
- **`quota.bypass_5h_with_credits`** is meaningless on this path (there is no
  5-hour subscription window to bypass). It stays inert; the config docs get
  a note.
- **Cost.** This is the substantive difference. Native Claude Code is
  subscription-billed, which is why `cost.max_cost_per_run_usd = 0.0`
  (disabled) is a safe shipped default. On OpenRouter every token is real
  money, and an unattended overnight run with the cost stop disabled is a
  genuine footgun. The preflight `warn` in Phase 3 is the minimum honest
  response. **Deliberately not done here:** making `[cost]` per-harness the
  way `[harness]` models became per-harness — that is a bigger config change
  than the question asked for, and the `warn` plus a docs line covers the
  real risk. Recorded in `v9-out-of-scope-desirables.md` rather than silently
  skipped.

## Documentation to update when this ships

Per the handoff's "when you finish" checklist — a behavior change that leaves
the public docs stale is a bug:

- `user-docs/{en,es}/reference/config-schema.md` — `[harness.overrides.<name>]`
  and its resolution order; the cost/quota notes above.
- `user-docs/{en,es}/how-to/write-a-new-adapter.md` — the sentence "Claude
  Code is the only adapter implemented today" is now false; and the shared-
  invoker split is worth one paragraph as the pattern for a second adapter
  that wraps the same underlying tool.
- `user-docs/{en,es}/concepts/architecture-overview.md` and
  `quota-and-safety-model.md` — the harness table and the quota-degradation
  sentence.
- `README.md` — harness list, if it enumerates one.
- No `cli.md` change: no new flags or commands exist. `--harness ori-claude`
  is the existing flag with a new value.
- `docs/v3-implementation-state.md` — deviations table entries starting at
  **85**, one per real finding (especially whatever V1–V6 turn up).
- `docs/v8-validations-for-later.md` — V1–V6 as entries, updated in place as
  each gets a real run.
- `docs/handoff.md` — replace the "Ori and OpenCode researched" bullet with
  what actually shipped.

## Commit shape

Five commits, in this order, each independently green under `./check.sh`:

1. Refactor: extract `_ClaudeCodeInvoker` (no behavior change).
2. Config: per-harness model overrides + `resolve_model`.
3. Adapter: `OriClaudeAdapter` + registry + tests.
4. Template: `templates/harness/ori-claude/` + symlinks entry + parity test.
5. Docs: everything in the section above, after V1–V6 have really run.

Commit 5 must not precede the validation runs — the docs would otherwise
assert a gating guarantee nobody has watched work.

## Explicitly out of scope

- **OpenCode.** v12's stronger long-term pick, and a genuinely full adapter
  (new stream parser, new template, TS guardrail plugins). Not touched here.
- **A generic `ori <tool>` wrapper layer.** Decided against; each wrapped
  tool needs its own template, gating story and stream parser anyway.
- **Per-harness `[cost]`/`[timeouts]`/`[quota]` sections.** Only models were
  asked for. Recorded in `v9-out-of-scope-desirables.md`.
- **Per-spec or per-batch model selection.** Already declined once for the
  native adapter (deviation 84); nothing here changes that reasoning.
- **`ori harness-doctor claude`.** Does not exist as of Ori `0.12.1` (v12).
  If it appears later it would be an opt-in deep check, never part of
  `preflight()`, which must stay subprocess-free.
