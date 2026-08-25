# Cosmo — Implementation State

Running record of what actually exists in the codebase, phase by phase. Updated at
the end of each working session.

The plan ([v3-implementation-plan.md](v3-implementation-plan.md)) says what *will*
be built. This document says what *is* built, and records decisions and gotchas
made during implementation that a future session would otherwise have to
rediscover.

| | |
|---|---|
| Last updated | 2026-08-24 |
| Working branch | `develop` |
| Head commit | `02ca48e` — Phase 0 |
| Spec | [v3-cosmo-autonomous-agent-spec.md](v3-cosmo-autonomous-agent-spec.md) |

## Phase status

| Phase | Status |
|---|---|
| 0 — Repository skeleton and configuration | **Complete** |
| 1 — Persistent state and the event log | Not started — next |
| 2 — Process supervision | Not started |
| 3 — Harness abstraction and Claude Code adapter | Stub only (see below) |
| 4 — Template system and `cosmo init` | Not started |
| 5 — Worktree lifecycle and git operations | Not started |
| 6 — Validation gate | Not started |
| 7 — Task state machine | Not started |
| 8 — Run loop, DAG, circuit breaker, quota | Not started |
| 9 — Observability, logs, deployment | Not started |
| 10 — Acceptance run | Not started |

---

## Phase 0 — Complete

All exit criteria met. 38 tests passing; `ruff`, `ruff format`, and `mypy --strict`
clean. `./check.sh` runs all four in one command.

### What exists

| Path | Contents |
|---|---|
| `src/cosmo/checks.py` | `CheckResult` / `CheckStatus` — the neutral result type both core and adapter preflight produce |
| `src/cosmo/config/model.py` | The full typed config model, every spec tunable, with cross-field validators |
| `src/cosmo/config/defaults.toml` | Shipped spec defaults; each value annotated with its spec section |
| `src/cosmo/config/loader.py` | Three-layer loading: shipped defaults → user config → CLI overrides |
| `src/cosmo/doctor.py` | Core (harness-agnostic) preflight checks |
| `src/cosmo/harness/base.py` | `HarnessAdapter` ABC, `HarnessCapabilities`, `HarnessResult` |
| `src/cosmo/harness/registry.py` | Name → adapter mapping and resolution-with-provenance |
| `src/cosmo/harness/claude.py` | Claude adapter: capabilities + `preflight()` implemented; execution methods raise `NotImplementedError` |
| `src/cosmo/cli/main.py` | `cosmo` command: `--version`, `config show`, `harness list`, `doctor` |
| `src/cosmo/{store,events,proc,git,gate,task,run,knowledge}/` | Empty packages, each with a one-line comment naming its phase and spec section |
| `check.sh` | Lint + format + types + tests |

Working commands:

```
cosmo --version
cosmo config show [--paths]
cosmo harness list
cosmo doctor [--harness NAME] [--config PATH]
```

### Decisions made during Phase 0

**1. `doctor` is split along the harness boundary.**
The original plan put an `ANTHROPIC_API_KEY` check in a generic `cosmo doctor`.
That check is meaningless to a Cursor or Codex adapter and hardcodes one harness
into the harness-agnostic layer, which §2 forbids. Now:
- `cosmo/doctor.py` holds core checks only and **does not import `cosmo.harness`
  at all**.
- Harness-specific preconditions come from `preflight()` on the resolved adapter.
- `cli/main.py` composes the two and renders them as separate tables.

**2. `HarnessAdapter.preflight()` — extension to spec §2.2.**
The spec's interface lists `propose`/`implement`/`validate`/`get_progress`/`cancel`.
A sixth method was needed so each adapter declares its own environmental
preconditions rather than core hardcoding them. Fold into a future spec revision.

**3. `validate()` deliberately omitted from the adapter interface.**
Spec §2.2 lists `validate(task_id)` as an adapter method while also stating that
validation "bypasses the LLM harness entirely (direct Docker invocation)." Those
conflict. Validation is owned by `cosmo.gate` (Phase 6). Recorded in a comment at
the bottom of `harness/base.py`.

**4. Top-level `stream/` package removed from the plan's layout.**
`stream-json` is Claude Code's wire format, not a universal one. A core-level
reader would leak this harness's wire protocol across the §2 boundary. The reader
belongs in `harness/claude/` in Phase 3. Found by the boundary test on its first
run, not by inspection.

**5. Paths default to XDG, not the spec's `/var/cosmo`.**
`/var` requires root on a WSL2 development box. `paths.data_dir` /
`paths.work_dir` / `paths.log_dir` default to `~/.local/share/cosmo/*`; the
droplet overrides them to `/var/cosmo` via its own config file. Same code,
different config per host.

**6. Harness resolution returns provenance.**
`resolve_harness_name(flag, project, configured) -> (name, source)`. Every
command prints which adapter it chose and why; an audit log should never have to
guess. Order: `--harness` flag > project registration (Phase 1) > config default.

**7. Config rejects settings that would fail silently at runtime.**
Beyond type checking, the model refuses:
- a stall timeout at or above its wall clock — it could never fire, silently
  disabling the only guard against a hung harness (§3.3)
- `retries.delay_min > delay_max`
- a `playwright_image` pinned to `:latest` or with no tag — §1.1 requires atomic
  version pinning
- unknown keys (`extra="forbid"`), so a config typo fails loudly rather than
  being ignored

**8. A cost ceiling of `0.0` means "disabled."**
The posture for a subscription-billed harness, where §7.1 usage windows govern
instead. `CostConfig.run_limit_enabled` / `.task_limit_enabled` express this so
callers never compare against zero themselves.

### Things that will matter later

**The boundary test is load-bearing.**
`tests/test_harness_boundary.py` fails if any harness-specific token
(`ANTHROPIC_API_KEY`, `stream-json`, `--permission-mode`, `max-turns`,
`dangerously-skip-permissions`) or the bare literal `"claude"` appears in a core
module, or if `doctor.py` imports `cosmo.harness`. It already caught one real
violation. **When adding a genuinely harness-aware module, add it to
`ALLOWED_HARNESS_AWARE` rather than weakening the test.**

**`defaults.toml` is the only place in core that names a harness.**
It is configuration data, not logic, and is on the allow-list for that reason.

**`harness/claude.py` becomes a package in Phase 3.**
`harness/claude/` with `adapter.py` and `stream.py`, so the stream reader sits
beside the adapter rather than in core.

**Project registration is the missing middle tier of harness resolution.**
`cli/main.py` currently passes `None` for the project tier, with a comment. Phase 1
adds the `projects` table (§10.4 step 6); wire it in there.

**Tests must never read the developer's real user config.**
`tests/test_cli.py` sets `COSMO_CONFIG` and `XDG_DATA_HOME` to temp paths in an
autouse fixture. Any new test touching config needs the same isolation, or it will
pass or fail depending on whose machine it runs on.

**`cosmo doctor` warns rather than fails on a `/mnt` work dir.**
Slow WSL2 filesystem I/O distorts every §3.3 timeout, but it is not a hard block.

**Repo branch model mirrors the spec's target-repo model.**
`develop` is the working branch, `master` is promoted manually — the same shape
§3.2 describes for managed projects. Cosmo's own branches are unrelated to
`git.base_branch` in config, which refers to the *target* repo.

### Environment as verified on 2026-08-24

| | |
|---|---|
| Python | 3.14.4 |
| `uv` | 0.11.28 |
| `git`, `docker`, `claude`, `openspec` | all on PATH; `docker` resolves to the Docker Desktop shim under `/mnt/c` |
| `ANTHROPIC_API_KEY` | unset (correct) |
| Free disk | ~940 GB on the WSL2 ext4 filesystem |
| Install | `uv tool install --editable .` → `cosmo` on PATH at `~/.local/bin/cosmo`, pointing back at the source tree |

---

## Deviations from the spec, cumulative

Kept here so a future spec revision can absorb them in one pass.

| # | Deviation | Spec ref | Phase | Rationale |
|---|---|---|---|---|
| 1 | `preflight()` added to the adapter interface | §2.2 | 0 | Adapters must declare their own preconditions; core cannot know them |
| 2 | `validate()` not on the adapter interface | §2.2 | 0 | Contradicts §2.2's own statement that validation bypasses the harness |
| 3 | State paths default to XDG, not `/var/cosmo` | §3.2 | 0 | `/var` needs root on WSL2; droplet overrides via config |
