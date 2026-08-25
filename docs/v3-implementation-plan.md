# Cosmo — v3 Implementation Plan

## Status
Planning document for building the agent described in [v3-cosmo-autonomous-agent-spec.md](v3-cosmo-autonomous-agent-spec.md). Section references (§) point at that spec. Nothing here changes the spec; where the spec leaves a choice open, this plan records the choice and marks it **[decision]**.

## Environment as found
| Tool | Status |
|---|---|
| Python | 3.14.4 (`/usr/bin/python3`) |
| `uv` | present |
| `docker` | present (Docker Desktop / WSL2 backend) |
| `git` | present |
| `claude` | present |
| `openspec` | present (via fnm/node) |
| Cosmo repo | git repository initialized (branch `master`, no commits yet) |

---

## Cross-cutting principles

These hold across every phase and are the difference between a plan that builds and one that stalls.

1. **Fakes before the real thing.** A `FakeHarnessAdapter` and a `FakeGate` land in Phases 3 and 6 respectively. Every state-machine, retry, circuit-breaker, and quota test runs against them. Testing the loop by invoking `claude -p` would be slow, non-deterministic, and would burn the very subscription quota the loop depends on (§7.1). Real-harness runs are reserved for the explicitly-marked integration tests in each phase's exit criteria.
2. **The gate is the only truth (§ Overview).** No phase may introduce a path where a task reaches `DONE` on a harness self-report. When in doubt while implementing, this is the tiebreaker.
3. **Every phase ends in something runnable.** Each has an exit criterion phrased as a command a human can type and a result they can see. No phase is "the module is written."
4. **Persist first, act second.** State transitions and events are written before the side effect they describe wherever ordering permits, so a crash leaves a queryable trail rather than a mystery (§9.1 `sequence` is transactional).
5. **Config is one schema, validated at startup.** Every tunable named in the spec (timeouts §3.3, retry limits §6.3, cost ceilings §7.3, disk floor §9.5, knowledge-file caps §11) is a field in one validated config model, not a scattered constant.

### Target package layout

```
cosmo/
  pyproject.toml            # uv-managed, console_script: cosmo
  src/cosmo/
    cli/                    # typer commands: init, run, queue, events, validate, templates, doctor
    config/                 # pydantic settings model + defaults.toml
    store/                  # sqlite: connection, pragmas, migrations, repositories
    events/                 # envelope, emitter, sequence allocation
    proc/                   # Popen wrapper, process-group kill, orphan sweep, timers
    harness/                # base interface, capabilities, registry
      claude/               # adapter + its own stream-json reader (Phase 3)
      fake/                 # scriptable test double (Phase 3)
    git/                    # worktree manager, commit, merge/rebase recovery
    gate/                   # docker gate runner, diff gate, flaky handling, quarantine
    task/                   # task state machine, progress watcher, failure classifier
    run/                    # run state machine, DAG scheduler, circuit breaker, quota
    knowledge/              # docs/ append + size caps
  templates/
    harness/claude/         # CLAUDE.md, settings.json, agents/, skills/, hooks/
    projects/_blank/
    projects/java-spring-react/
  deploy/                   # systemd unit, install notes
  quarantine.yml
  tests/
```

---

## Phase 0 — Repository skeleton and configuration

**Spec:** foundation for everything; §1 (stack), plus the config surface named across §3.3 / §6.3 / §7.3 / §9.5 / §11.

**Build**
- `.gitignore` covering `data/`, `work/`, `logs/`, `.venv/`; first commit (repo already initialized on `master`).
- `uv init` project, Python 3.14, `pyproject.toml` exposing the `cosmo` console script. Dependencies: `typer`, `pydantic`/`pydantic-settings`, `watchdog`, `rich`; dev: `pytest`, `pytest-asyncio`, `ruff`, `mypy`.
- Package skeleton per the layout above — modules with interfaces and `NotImplementedError`, no logic.
- Config model with every spec tunable and its spec default: state timeouts, stall timeouts, run wall clock (10 h), `max_attempts` (2), retry delay (30–60 s), circuit-breaker N, `max_cost_per_run_usd` / `max_cost_per_task_usd`, disk floor (10 GB), knowledge-file cap (400 lines), `permission_mode` (`dontAsk`), `max_turns`, base branch for target repos (`develop`). Note this is the *target* repo's branch (§3.2); Cosmo's own repo branch is unrelated.
- **Harness resolution, before any harness-dependent command runs.** A harness registry (name → adapter class) plus a resolution order: `--harness` flag > per-project registration (§10.4, from Phase 1) > `harness` config default. The resolved name is printed by every command that depends on it, so an audit log never has to guess which adapter ran.
- **Harness adapter stub** — the `HarnessAdapter` base class with its declared capabilities (§2.2) and a `preflight()` method, plus a `claude` adapter that implements *only* `capabilities` and `preflight()`. Its `propose`/`implement`/`validate` raise `NotImplementedError` until Phase 3.
- `cosmo doctor` — split along the abstraction boundary (§2):
  - **Core checks (harness-agnostic):** `git`, free disk against the §9.5 floor, config validity, and a WSL2 warning if the work dir is under `/mnt` (§1).
  - **Harness checks (delegated):** obtained by calling `preflight()` on the resolved adapter. Cosmo core never names a harness-specific condition. The `claude` adapter's own preflight is what asserts the `claude` binary is present and that **`ANTHROPIC_API_KEY` is unset** (§2.3: its presence silently reroutes billing from the subscription to per-token API rates).
  - Whether `openspec` and `docker` are core or harness-specific is decided per check: both are Cosmo's own dependencies (Cosmo calls OpenSpec's CLI at §10.4, and the validation gate at §2.2 bypasses the harness entirely), so both stay **core**.
- `cosmo harness list` — registered adapters and their declared capabilities.

**Exit criteria**
- `cosmo --version`, `cosmo config show`, `cosmo harness list`, `cosmo doctor` all run and report honestly.
- `cosmo doctor` reports core and harness checks in separate sections, with the resolved harness named.
- A test asserts **no harness-specific string** (`claude`, `ANTHROPIC_API_KEY`, …) appears anywhere in `cosmo.config`, `cosmo.cli.doctor`, or other core modules — the abstraction boundary is enforced by test, not by discipline.
- `ruff`, `mypy`, and a green `pytest` run in one command.

**Interface addition (extends §2.2).** The spec's adapter interface lists `propose`/`implement`/`validate`/`get_progress`/`cancel`. Building `doctor` correctly requires a sixth method, `preflight() -> list[CheckResult]`, so each adapter declares its own environmental preconditions instead of the core hardcoding them. Recorded here as a deliberate extension to fold into a future spec revision.

**Phase 0 outcome (built).** Two design corrections surfaced while implementing:
1. `cosmo doctor` originally put the `ANTHROPIC_API_KEY` check in core. That hardcodes one harness into the harness-agnostic layer (§2). Split into core checks plus a delegated `preflight()` on the resolved adapter, and enforced by `tests/test_harness_boundary.py`.
2. The plan's top-level `stream/` package was removed. `stream-json` is Claude Code's wire format, not a universal one; the reader belongs inside `harness/claude/`. The boundary test caught this on its first run.

---

## Phase 1 — Persistent state and the event log

**Spec:** §8, §8.1, §9.1, §9.2, §9.3, §5 (queue columns).

**Build**
- SQLite schema DDL (**Open Item 5**), split by the §8 discipline:
  - *Append-only*: `events` (full §9.1 envelope incl. `sequence`, `schema_version`, `severity`), `task_transitions`, `task_failures`.
  - *UPSERT / current-state*: `task_queue` (all §5 columns incl. `blocked_reason`, `allow_test_edits`, `worktree_path`, `session_id`), `task_progress`, `task_heartbeat`, `run_state`, `run_cost`, `task_cost`, `projects` (§10.4 step 6).
- `blocked_reason` and `failure_type`/`failure_stage` as constrained enums in the schema, not free text (§5).
- Pragmas applied **on every connection**: `journal_mode=WAL`, `busy_timeout=10000`, `synchronous=NORMAL`, `foreign_keys=ON`. Checkpoint `TRUNCATE` at run boundaries.
- **Single-writer discipline (§8):** one write connection owned by the main loop; an in-process queue that the file-watcher and stream reader push onto. Enforce it structurally — the writer connection is not importable from watcher/stream modules.
- Event emitter: monotonic `sequence` allocated transactionally with the row.
- A forward-only migration runner (`schema_version` table), because §9.1's whole point is migrating without backfill archaeology.
- CLI: `cosmo queue add|ls|show|retry|block`, `cosmo events tail [--run] [--task] [--severity]`.

**Exit criteria**
- `cosmo queue add` then `cosmo queue ls` round-trips a DAG of tasks with `depends_on`.
- A concurrency test writes progress events from a watcher thread while the main loop writes state, with zero `SQLITE_BUSY`.
- Killing the process mid-write leaves an event log whose `sequence` has no gaps or duplicates.

---

## Phase 2 — Process supervision

**Spec:** §2.4 (all six contract points), §3.3 (timers).

This is early and standalone because §2.4 is the reason the raw-`subprocess` decision (§2.1) was made at all, and because a leaked process pool poisons every later phase.

**Build**
- `proc.ManagedProcess`: `Popen(..., start_new_session=True)`, non-blocking stdout/stderr drain to a per-task rotating log file (`raw_log_path`).
- `cancel()`: `os.killpg(os.getpgid(pid), SIGTERM)` → **20 s** grace → `killpg(SIGKILL)`.
- **Orphan sweep**: `docker ps -q --filter label=orchestrator.run_id=<id>` → `docker rm -f`; scan for processes holding the worktree path and log `critical`.
- Two independent timers per managed run: wall-clock and stall. The stall timer accepts heartbeat pokes from *either* source (§4) so a long legitimate subtask does not trip it.
- Reap failure emits `task.failed` with `failure_type=environment_error` and **double-weights** the circuit breaker (§6.5).

**Exit criteria**
- Test: a shell script spawning a grandchild that ignores `SIGTERM` is fully reaped, verified by PID absence — the child does not survive re-parented to init.
- Test: a labeled container left running by a killed process is removed by the sweep.
- Test: stall timer fires at the configured interval and is correctly reset by a poke.

---

## Phase 3 — Harness abstraction and the Claude Code CLI adapter

**Spec:** §2.1, §2.2, §2.3, §4 (stream-json), §7.2 (quota detection).

**Build**
- `HarnessAdapter` base: `propose`, `implement(retry_context=None)`, `validate`, `get_progress`, `cancel`; the uniform result object (§2.2) including `session_id` and `total_cost_usd`; the six declared capability flags.
- **`FakeHarnessAdapter`** — scriptable outcomes (success, code failure, environment failure, hang, rate-limit, cost overrun) driving every later phase's tests. **[decision]** every state-machine test targets this, never the real CLI.
- `ClaudeCodeAdapter`:
  - `claude -p --output-format stream-json --verbose`, always with `--max-turns` and `--permission-mode` from config.
  - Child environment **explicitly scrubs** `ANTHROPIC_API_KEY` rather than assuming its absence (§2.3).
  - Sets `CLAUDE_CODE_ENABLE_TELEMETRY=1` with content logging off (§9.4).
  - Never emits `--dangerously-skip-permissions`; add a unit test asserting the flag can never appear in a constructed argv.
  - Branches on **zero vs non-zero exit only** (§2.3) — a test asserts no specific non-zero value carries meaning.
  - Headless prompt that the permission layer cannot resolve → fail as `environment_error`, never hang.
- Stream reader, **inside the claude adapter package, not in core**: NDJSON line reader tolerant of partial lines and non-JSON noise, classifying: heartbeat (any event), tool-call records, `system/api_retry` → rate-limit state + ETA (§7.2 **primary** source), terminal `result` → `total_cost_usd`, `duration_ms`, `num_turns`, `session_id`.
- **Prose parsing is prohibited** as a signal (§4). A test asserts no classification path greps human-readable text.

**Exit criteria**
- `cosmo harness probe --prompt "print hello"` invokes the real CLI, streams events, and prints the parsed result object with a `session_id`. *(Integration; consumes a small amount of quota.)*
- Recorded NDJSON fixtures (normal run, `api_retry`, truncated stream, malformed line) replay through the reader in unit tests.

---

## Phase 4 — Template system and `cosmo init`

**Spec:** §10 in full, §2.5 (the hooks themselves), Open Item 4.

Placed before the loop because §10.5 makes template sync a precondition of worktree creation, and because the §2.5 hooks are a hard security boundary that must exist before any unattended run.

**Build**
- `templates/harness/claude/`:
  - `settings.json` — `permissions.deny` for secret paths (`./.env*`, `./secrets/**`, `**/*.pem`, `**/id_rsa*`). Deny is used deliberately because it is **absolute across all permission modes** (§2.3).
  - `hooks/` — `PreToolUse` implementations, each **synchronous, local, no network, no LLM**, budgeted under 2 s with `timeout: 5000` (§2.5):
    - test-path guard (`src/test/**`, `**/*.spec.ts`, `**/*.test.ts`, `e2e/**`), bypassed only on `allow_test_edits`
    - annotation guard (`@Disabled`, `@Ignore`, `test.skip`, `it.skip`, `xit`, `describe.skip`)
    - commit-integrity guard (`git commit *--no-verify*`, `git push *`, `git reset --hard*`, force-push forms)
  - Async hooks are **not** used for gating (§2.5 — they do not block); telemetry only.
  - `CLAUDE.md`, `agents/*.md`, `skills/*/SKILL.md` — Cosmo's operating policy, harness-facing.
- `templates/projects/_blank/` (schema-only headings) and `templates/projects/java-spring-react/` (real starter content per the §10.3 file list).
- `sync_harness_assets(target, harness)` — **one function, two call sites** (§10.5): `cosmo init`, and worktree creation in Phase 5. Replaces `.agent/<harness>/` wholesale; computes a `template_version` hash of the source tree; emits `agent_assets.synced` (§9.2).
- Root symlinks (§10.2), **relative only** — an absolute or cross-repo symlink breaks when the repo moves between droplet and WSL2. A test asserts relativity.
- `cosmo init <path> --harness claude --project-template <name>` executing §10.4 steps 1–7 in order, with never-overwrite semantics for `docs/`, an explicit `created: N / skipped: M` report, and `--force` behind a confirmation prompt.
- `cosmo templates list`.

**Exit criteria**
- `cosmo init` against a scratch git repo produces `openspec/`, `docs/`, `.agent/claude/`, correct relative symlinks, a `projects` row, and an `agent_assets.synced` event.
- Re-running `init` reports skipped `docs/` files and refreshes `.agent/` wholesale.
- Each hook is unit-tested for both deny and allow paths, and timed to confirm it stays under budget.
- A manual adversarial check: a `claude -p` run inside a synced repo is genuinely blocked from editing a test file and from `git commit --no-verify`.

---

## Phase 5 — Worktree lifecycle and git operations

**Spec:** §3.2 (isolation, retention), §3.4 (merge policy), §6.1 (secret handling).

**Build**
- `git worktree add <work>/<run_id>/<task_id> -b task/<spec-id> develop`; call `sync_harness_assets` immediately after, before `PROPOSING` (§10.5).
- Install a `gitleaks` pre-commit hook in each worktree (§6.1).
- Teardown: `git worktree remove --force` on `DONE`; **retain** on `BLOCKED` for inspection. Track `worktree_path` on the queue row.
- Startup sweep pruning worktrees belonging to completed runs.
- Commit step, then merge into `develop` with the §3.4 ladder: merge → on conflict, **exactly one** rebase-onto-`develop` + **full gate re-run** → merge if green, else `BLOCKED` with `merge_conflict`.
- **The conflict is never handed back to the agent to resolve blind** (§3.4 step 2) — enforce structurally, not by convention.
- `merge_conflict` is excluded from the circuit-breaker tally (§3.4, §6.5) and surfaced in `run.summary` as a signal that `depends_on` edges are under-specified.

**Exit criteria**
- A scripted two-task conflict scenario in a fixture repo: rebase recovery succeeds in one case, and in the other produces `BLOCKED` with `merge_conflict`, retained worktree, and a `warning`-severity `task.blocked`.
- `master` is never a merge target anywhere in the codebase — asserted by test (§3.2).

---

## Phase 6 — Validation gate

**Spec:** §1.1, §1.2, §1.3, §6.1 layer 2, §6.4, §9.3.

The largest phase and the correctness core. It bypasses the LLM harness entirely (§2.2 `validate`).

**Build**
- Docker gate runner, **serial: build → unit → e2e** (§1.2), each stage attributed to a distinct `failure_stage`.
- Container flags, non-negotiable (§1.1): `--ipc=host`, `--shm-size=2gb`, and `--label orchestrator.run_id=... --label orchestrator.task_id=...` (required by the Phase 2 sweep).
- **Atomic version pinning** (§1.1): Playwright npm version, `mcr.microsoft.com/playwright` image tag, browser binaries, and cache key bump as one unit. A test asserts no `latest` tag appears anywhere.
- **Diff gate** (§6.1 layer 2), run *before* tests execute, against `git diff develop...task/<spec-id>`, failing the task when `allow_test_edits` is unset and any of: test-path file modified/deleted; net assertion count decreased; skip/disable annotation introduced; test-file LOC drop beyond threshold. Language-specific assertion counting for JUnit/AssertJ and Vitest/Playwright (**Open Item 1**). Classified `code_error` / `failure_stage=test_integrity` with the specific violation named in `error_detail`.
- **Flaky handling** (§6.4): quarantine list from `quarantine.yml` (owner + expiry required; expired entries fail validation of the file itself); confirm-by-rerun — failing non-quarantined e2e test re-run in isolation up to 3×, a pass classifies `flaky` and consumes no attempt; three `flaky` classifications across distinct runs appends to `quarantine-candidates.yml`. **Never auto-quarantine** (§6.4 step 4).
- `gitleaks` scan as gate-side backstop (§6.1).
- Structured gate result: unit and e2e reported **separately, never one combined boolean** (§9.2), plus `flaky_detected[]` and `quarantined_skipped[]`.
- `error_detail` construction (§9.3): failing test name + assertion + trimmed stack; build error; failing Playwright step + trace/screenshot **path only, never embedded binary**. Model-consumable, not archival — a test asserts a size ceiling.
- **Log actual gate duration on every run** (§3.3 note) so the 45-min `VALIDATING` timeout becomes empirically tunable (**Open Item 2**).
- `FakeGate` for Phases 7–8.

**Exit criteria**
- `cosmo validate <worktree>` on a fixture Java+Spring / Vite+React repo produces a full structured result.
- Fixture cases pass: green run; compile failure; unit failure; e2e failure; an injected flaky test correctly classified `flaky`; a deliberately weakened test caught by the diff gate.
- Gate durations are recorded and queryable.

---

## Phase 7 — Task state machine, progress, liveness, retries

**Spec:** §3.2, §3.3, §4, §6.2, §6.3, §11 (the `COMMITTING` knowledge step).

**Build**
- `QUEUED → PROPOSING → PROPOSED → IMPLEMENTING → VALIDATING → COMMITTING → MERGING → DONE`, with `FAILED_RETRY` and `BLOCKED`, every transition persisted and emitting `task.state_changed`.
- Per-state timeouts wired to Phase 2 timers with the §3.3 table's exact semantics — in particular, **`VALIDATING` timeouts do not consume the code-level retry budget** (a hanging gate is an environment problem), while `IMPLEMENTING` timeouts do.
- **Progress watcher** (§4): `watchdog`/inotify on the change's `tasks.md`, polling fallback at 5–10 s. Store **numerator and denominator separately, never percent alone** — the total is not constant and progress can legitimately move backwards. Debounced writes through the Phase 1 queue.
- **Heartbeat** (§9.2) with an explicit `source: stream | file | mtime`; mtime fallback where `supports_structured_stream` is false.
- Failure classifier producing the §6.2 quadrant: `code_error` (counts), `environment_error` (does not count, feeds breaker), `timeout` (counts in `IMPLEMENTING` only), `flaky` (counts nowhere).
- **Informed retries** (§6.3): the retry prompt carries the previous `error_detail` plus `previous_attempts_summary`, so the harness does not repeat the failed approach. 30–60 s delay between attempts to let Docker resources and ports settle. Stage-varying budget: build/compile failures get the full budget; e2e failures pass through §6.4 before consuming an attempt.
- `COMMITTING` also runs the §11 knowledge step: append 2–3 lines to the relevant `docs/` file **as an edit/reconcile instruction** (revise a contradicted line, do not stack contradictions), append a structured `decisions-log.md` entry, and **fail `COMMITTING` if a knowledge file exceeds its 400-line cap**.
- No mid-state resumption (§3.2) — but `session_id` is persisted now so deferred item 3 needs no schema change later.

**Exit criteria**
- `cosmo run --task <id>` drives one task through every state against `FakeHarnessAdapter` + `FakeGate`, with a complete event trail.
- Tests: retry exhaustion → `BLOCKED` with correct `blocked_reason`; environment error does not consume an attempt; `VALIDATING` timeout does not consume an attempt; a checkbox count that shrinks mid-run does not produce a nonsense percent.
- One real end-to-end task against the real adapter and real gate on a fixture repo. *(Integration.)*

---

## Phase 8 — Run loop, DAG scheduling, circuit breaker, quota and cost

**Spec:** §3.1, §5, §6.5, §7.1, §7.2, §7.3.

**Build**
- Run state machine `IDLE → RUNNING → PAUSED → STOPPED` with `run_id`, and the §3.1 stop reasons (`completed | max_time | queue_empty | cost_limit_reached | manual`).
- DAG scheduler: `depends_on` as a **hard** ordering constraint, `priority` only as a **soft tie-breaker among already-eligible tasks**. Cycle detection at enqueue. Strictly serial execution in v1 (§5).
- Circuit breaker (§6.5): trips on N **distinct** tasks blocked consecutively, or repeated `environment_error` across distinct tasks; `merge_conflict` and `flaky` excluded; reap failure counts double. `PAUSED` preserves process and queue state; resume is manual.
- Quota handling (§7.1) with the branch that actually matters: 5-hour window → `PAUSED` with a scheduled auto-resume; **weekly cap** → `PAUSED`, and if the computed reset lies beyond the run's remaining wall-clock budget, `STOPPED` with reason `quota_exhausted_weekly` rather than idling a process for days. Neither feeds the breaker.
- Quota detection strictly in the §7.2 priority order: `system/api_retry` primary, terminal `result` error subtype second, wall-clock heuristic last-resort at `severity=warning` that **must never masquerade as a confirmed quota state**.
- Cost ceilings (§7.3): `max_cost_per_run_usd` (→ `STOPPED`), `max_cost_per_task_usd` (→ that task `BLOCKED` with `blocked_reason=cost`, queue continues), 80% warning event. Inert for the v1 subscription adapter but implemented so a future per-token adapter needs no new mechanism.
- Run-level 10 h wall clock; in-flight task returns to `QUEUED` on expiry.
- `run.summary` (§9.2) including repeated-merge-conflict signal and knowledge-file compaction recommendations (§11).

**Exit criteria**
- `cosmo run` executes a multi-task DAG in dependency order against fakes.
- Tests: breaker trips on distinct-task blocks and *not* on merge conflicts or flakes; 5-hour pause auto-resumes; weekly cap beyond budget stops rather than idles; per-task cost ceiling blocks one task and leaves the queue running.
- `cosmo run --dry-run` prints the resolved execution order without executing.

---

## Phase 9 — Observability, logs, disk, deployment

**Spec:** §9.4, §9.5, §1 (systemd), §12.

**Build**
- OTel export via `CLAUDE_CODE_ENABLE_TELEMETRY=1`, 60 s interval, **content logging off** — prompts and file contents in a telemetry backend is a data-exfiltration path on a private codebase (§9.4).
- Log retention (§9.5): per-task `raw_log_path` rotation; 7 days for `DONE`, 30 for `BLOCKED`; Playwright traces and screenshots kept **only for failing runs**.
- **Pre-run disk check** aborting at `severity=critical` below the configured floor (default 10 GB) — a full disk fails every subsequent task in a way that reads as a code error.
- systemd unit (§9.5): `OOMPolicy=stop`, memory accounting, `WatchdogSec` with a ping issued on each state transition; raised journald rate limits so the loop's own logs are not dropped. Identical on the droplet and under WSL2.
- `cosmo events`/`cosmo report` querying for post-run triage; a `run.summary` renderer.

**Exit criteria**
- A run under systemd survives a restart, and a deliberately wedged loop is caught by the watchdog and restarted.
- A simulated low-disk condition aborts the run before any task starts.
- No prompt or file content appears anywhere in exported telemetry — verified by inspection.

---

## Phase 10 — Acceptance: unattended overnight run

**Build**
- Point Cosmo at a real target repo initialized by `cosmo init`. Queue 5–10 genuine OpenSpec changes with real `depends_on` edges.
- Run unattended overnight under systemd with production config.
- Post-run review against the spec's own claims: did anything reach `DONE` without a passing gate; did any test get weakened; were any orphan processes or containers left; did quota handling behave; are the p95 gate numbers consistent with the §3.3 defaults.

**Exit criteria**
- A full night's run completes with a coherent `run.summary` and an event log sufficient to reconstruct every decision without reading a raw log.
- **Open Item 2** closed: §3.3 timeouts retuned against real p95 data, or explicitly confirmed as-is.

---

## Open items, mapped to phases

| Spec open item | Phase |
|---|---|
| 1. `PreToolUse` hooks + diff-gate assertion heuristic | 4 (hooks) and 6 (diff gate) |
| 2. Empirical retuning of §3.3 timeouts | 6 (instrumentation) → 10 (retune) |
| 3. Quarantine ownership/expiry policy and escalation | 6 |
| 4. Concrete `templates/harness/claude/` contents | 4 |
| 5. SQLite schema DDL and the CLI adapter | 1 (DDL) and 3 (adapter) |

---

## Risks worth naming up front

| Risk | Where it bites | Mitigation in plan |
|---|---|---|
| Building the loop before the guardrails exist | An unattended run games its own tests on night one | Phase 4 precedes Phase 7 by construction |
| Testing against the real harness | Slow, flaky, burns the subscription quota the loop needs | `FakeHarnessAdapter` / `FakeGate`; real runs only at marked integration points |
| Phase 6 is disproportionately large | Schedule slip concentrated in one place | Its fixture repo is the critical path — build it first within the phase, before the gate runner |
| Docker Desktop / WSL2 differences vs. the droplet | Passes locally, fails on deploy | Gate flags (§1.1) and the systemd unit are identical in both; Phase 9 validates on both hosts |
| WSL2 filesystem I/O | Slow builds if the repo lives on `/mnt/c` | Keep worktrees inside the WSL2 filesystem (§1); `cosmo doctor` warns if the work dir is under `/mnt` |
| Quota policy changing under the loop | Hard-coded billing assumptions break silently | §2.3/§7.2 posture — detection is driven by observed rate-limit events, never a belief about the billing model |

---

## Explicitly out of scope (§12)

Telegram/notifications, web dashboard, harnesses beyond Claude Code CLI, parallel execution, automatic merge into `master`, mid-flight crash resumption, full OTel span-tree migration, automated flaky quarantine, and template token substitution. The worktree decision (§3.2) and the persisted `session_id` (§2.2) keep the first and third of the deferred items cheap to add later.
