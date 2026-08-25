# Handoff — continue at Phase 8

You are picking up Cosmo mid-build. Phases 0-7 are complete. Your job is
Phase 8: the run loop — the `IDLE → RUNNING → PAUSED → STOPPED` run-level
state machine, DAG scheduling over the task queue, the global circuit
breaker, quota detection and auto-resume/stop, and dollar-cost ceilings.

## Read these first, in this order

| Document | What it is | How to treat it |
|---|---|---|
| [v3-cosmo-autonomous-agent-spec.md](v3-cosmo-autonomous-agent-spec.md) | The authoritative specification | **Source of truth.** v1 and v2 are superseded — read them only for history |
| [v3-implementation-plan.md](v3-implementation-plan.md) | 11-phase build plan | The map. Phase 8 is your scope (§3.1, §5, §6.5, §7.1, §7.2, §7.3) |
| [v3-implementation-state.md](v3-implementation-state.md) | What actually exists, plus decisions and gotchas | Read the "Phase 7 — Complete" section in full before writing code — several of its decisions and deferred items are load-bearing for Phase 8 |

`v1-*` and `v2-*` in this folder are earlier spec drafts. v3 is a superset of
both. Do not implement from them.

**Do not edit the plan document.** It is the agreed scope. Record what you
build, and any decision you make along the way, in `v3-implementation-state.md`.

## Where things are

```
/home/dev/delta/cosmo/          # working branch: develop
├── docs/                       # the three documents above
├── templates/                  # harness + project templates (source of truth)
├── src/cosmo/
│   ├── checks.py, config/, doctor.py, harness/, bootstrap/
│   ├── git/                      # Phase 5: worktree lifecycle, merge ladder
│   ├── gate/                     # Phase 6: the Docker validation gate
│   ├── task/                     # Phase 7: the per-task state machine -- your real caller now
│   │   ├── machine.py              # run_task -- drives one task through every state
│   │   ├── timeouts.py             # run_with_wall_clock_timeout / run_with_liveness_timeout
│   │   ├── progress.py             # ProgressWatcher (watchdog + polling)
│   │   ├── classify.py             # PROPOSING/IMPLEMENTING failure classification
│   │   └── retry.py                # informed-retry prompt construction
│   ├── knowledge/                # Phase 7: spec 11's COMMITTING-step guardrails
│   ├── store/                    # SQLite schema, StoreWriter, reader queries
│   │   └── enums.py                 # RunStatus/PauseReason/StopReason already exist, unused by any writer yet -- yours
│   ├── events/                   # envelope + EventEmitter + emit_state_changed (Phase 7)
│   ├── proc/                     # ManagedProcess, WallClockTimer/StallTimer/LivenessTimers, orphan sweep
│   ├── cli/main.py               # `cosmo` command -- `cosmo run --task <id>` (Phase 7) is what you extend
│   └── run/                      # EMPTY — this is your package
├── tests/                       # 264 passing + 7 opt-in real-Docker (COSMO_GATE_DOCKER_E2E=1)
│   └── fixtures/gate_repo/        # real Spring Boot + Vite/React fixture, reusable for your own tests too
└── check.sh                     # ruff + format + mypy --strict + pytest
```

`src/cosmo/run/` is empty and is exactly where Phase 8's DAG scheduler and
run-level state machine go. `run_state`/`run_cost`/`task_cost` tables are
already schema'd (Phase 1) but have **no writer anywhere yet** — Phase 8 is
their first real caller, the same "built ahead, first caller now" pattern
`task_progress`/`task_heartbeat` were for Phase 7 (`StoreWriter.submit()`/
`drain()`, `watchdog`) and `gate_rerun`/`sync_harness_assets(run_id=...)`
were for Phases 6/5 before that.

## Get oriented (2 minutes)

```bash
cd /home/dev/delta/cosmo
git log --oneline           # Phase 7 should be committed at HEAD
git branch --show-current   # should say develop
./check.sh                  # must be green before you change anything
cosmo doctor                # core checks + harness checks in two tables
```

**Known, pre-existing environment noise on this host** (not something Phase
8 broke, don't chase it): `cosmo doctor` may show `disk space: FAIL` — this
WSL2 box runs close to the 10 GB floor at the *test* data path it checks
(`/tmp` is a small tmpfs on this box); the real filesystem has hundreds of
GB free. This box has no *global* git identity either — only this repo's
own local config has one — so any test fixture your own work adds that
calls `git commit` needs `-c user.name=...`/`-c user.email=...` passed
explicitly (see `tests/test_git_merge.py`'s `_git` helper, or
`tests/test_task_machine.py`'s). `gitleaks` is on PATH, `docker` works.

**Two real environment gotchas from Phase 6, reconfirmed for real in Phase
7** — read Phase 6's state-doc section for the full diagnosis before you
touch anything Docker- or npm-related: **`npm install` can hang
indefinitely on this host if a previous run was killed mid-install**
(fix: verified-clean `rm -rf node_modules package-lock.json` first, not
waiting longer), and **Docker containers write bind-mounted build
artifacts as root**, which blocks a later unprivileged `rm -rf` — Phase 7's
own opt-in real-gate test hit this for real (`backend/target/` left behind,
`remove_worktree`'s fallback silently tolerates it via
`ignore_errors=True`); worked around by hand with a throwaway `alpine`
container, same as Phase 6. **This is still unfixed and will bite you
again** if you run any real-Docker test repeatedly — see Phase 7's "Things
that will matter later."

One more: **this session's shell may have `XDG_DATA_HOME=/tmp/cosmo-test/data`
set** (sandboxing `cosmo`'s own runtime state away from the real home
directory). `uv run cosmo ...` (this project's own `.venv`) is unaffected by
this and is the more reliable invocation for anything scripted; if you ever
need `uv tool install --editable .` again, run it as `env -u XDG_DATA_HOME
uv tool install --editable --force .` or it will reinstall into the wrong
place and leave `~/.local/bin/cosmo` dangling (Phase 6 found this the hard
way).

## Conventions this codebase follows

- **Python 3.12+, `uv`-managed.** Add dependencies with `uv add`, not by hand.
- **`mypy --strict` passes.** Annotate everything, including test helpers.
- **Comments explain *why*, never *what*.** Existing comments cite the spec
  section that forced the decision. Match that.
- **Config over constants.** Every tunable goes in `config/model.py` and
  `config/defaults.toml`, annotated with its spec section. No magic numbers.
  Phase 7 needed a `[progress]` section that didn't exist before — expect
  Phase 8 needs its own new sections too (breaker thresholds and cost
  fields already exist as `CircuitBreakerConfig`/`CostConfig`, unused by any
  real logic yet; check what's already there before adding more).
- **Tests isolate from the developer's environment.** Anything touching
  config must set `COSMO_CONFIG` and `XDG_DATA_HOME` to temp paths — see the
  autouse fixture in `tests/test_cli.py`/`test_cli_run.py`. Anything
  touching a real git repo should build one in `tmp_path`, never touch this
  repo or a real target repo. Retry-driven tests should override
  `retries.delay_min`/`delay_max` to `0` via `cfg.model_copy(...)` — Phase 7's
  `_retry_delay` is a real `time.sleep()` in production and every one of its
  own tests does this (see `tests/test_task_machine.py`'s `_fast_config`).
- **Fake the external process, test the mechanics — except where "check by
  hand, then use the real thing" already proved out.** `FakeHarnessAdapter`
  and `FakeGate` are the two test doubles later phases should target
  directly. Real-process/real-Docker tests exist but are skip-guarded (an
  opt-in env var, `COSMO_GATE_DOCKER_E2E=1`) because they take real minutes.
  Follow the same posture for Phase 8: fake harness/gate for the run-loop
  unit tests (breaker trips, quota transitions, DAG ordering), one real
  multi-task run against fakes for the plan's own exit criterion, and treat
  any *new* real-external-system testing need (if one comes up) the same way.
- **Boundary tests are load-bearing, not optional.** `test_harness_boundary.py`,
  `test_store_boundary.py`, `test_git_boundary.py`, `test_gate_boundary.py`.
  None of them currently restrict a `cosmo.run` package's imports (confirmed
  while building Phase 7's `cosmo.task`, which also imports both
  `cosmo.harness` and `cosmo.gate` freely) — but re-verify this yourself
  before assuming it still holds, the same way Phase 7's own handoff asked
  of `cosmo.task`.
- **Run `./check.sh` before committing.** All four must pass.
- **When something fails, check with a real invocation before trusting a
  unit test's green.** This has found a real bug or made a real design
  decision correctly in every phase so far, Phase 7 included:
  `test_watchdog_observer_detects_a_real_write_to_tasks_md` caught a real
  cross-thread SQLite bug (`EventEmitter.emit` called from the wrong
  thread) and a missing-transaction-commit bug, neither of which any
  synchronous/fake-clock test in the same file exercised;
  `test_docs_md_files_finds_only_docs_markdown_touched_on_the_branch`
  caught `fnmatch`'s `**` silently rejecting a docs file with no
  subdirectory; a manual `cosmo run --harness bogus` invocation (not a
  unit test) surfaced an uncaught `UnknownHarnessError` producing a raw
  traceback instead of the same clean error `cosmo doctor` already gives.
  Do the same for Phase 8: a real multi-task DAG run through
  `FakeHarnessAdapter`+`FakeGate`, driven by hand via the actual `cosmo run`
  CLI command, before calling any of it done — not just asserted possible
  by a unit test.

## Phase 8 scope

Spec §3.1 (run-level state machine), §5 (task queue as a DAG), §6.5 (global
circuit breaker), §7.1-7.3 (quota windows, detection, cost ceilings).

Summary from the plan:

1. **Run state machine**: `IDLE → RUNNING → PAUSED → STOPPED`, with a real
   `run_id` and the §3.1 stop reasons (`completed | max_time | queue_empty
   | cost_limit_reached | manual`). `run_state`/`run_cost` tables already
   exist (Phase 1 schema, `RunStatus`/`PauseReason`/`StopReason` already in
   `store/enums.py`) with **no writer anywhere** — this is their first real
   caller. Once a `run_state` row exists, `task_transitions.run_id`/
   `task_failures.run_id` can finally carry a real value instead of the
   `None` Phase 7 used everywhere (see its decision on why: the FK is
   enforced and there was no row to reference yet).
2. **DAG scheduler**: `depends_on` is a hard ordering constraint,
   `priority` a soft tie-breaker among already-eligible tasks. Cycle
   detection at enqueue. Strictly serial execution (spec 5 — no parallel
   harness runs). This is the first real multi-task caller of Phase 7's
   `task.machine.run_task` — up to now every test and the CLI itself only
   ever drove one task.
3. **Circuit breaker (§6.5)**: trips to `PAUSED` on N *distinct* tasks
   `BLOCKED` consecutively, or repeated `environment_error` across distinct
   tasks (`circuit_breaker.consecutive_blocked_threshold`/
   `environment_error_threshold`, already in config, unused until now).
   `merge_conflict` and `flaky` blocks are excluded from the tally. A
   process-reap failure counts double
   (`circuit_breaker.reap_failure_weight`). This is the real decision
   `gate.validate_task`'s docstring and Phase 7's decision 6 (the bounded
   local environment-error retry) both explicitly deferred to you — read
   both before designing this.
4. **Quota handling (§7.1/§7.2)**: 5-hour window exhaustion → `PAUSED` with
   a scheduled auto-resume; weekly cap → `PAUSED`, or `STOPPED` with
   `quota_exhausted_weekly` if the reset is beyond the run's remaining
   wall-clock budget. Detection order: `stream-json`'s rate-limit signal
   first (deviation 5's `rate_limit_event` finding already covers the
   *classification* side of this in `harness/claude/stream.py` — check
   whether anything there needs to surface further before this is usable),
   terminal `result` error subtype second, wall-clock heuristic last
   (`severity=warning`, must never masquerade as confirmed).
5. **Cost ceilings (§7.3)**: `max_cost_per_run_usd` → `STOPPED`;
   `max_cost_per_task_usd` → that task `BLOCKED` with `blocked_reason=cost`,
   queue continues; 80% warning event. Already in `CostConfig`, inert until
   this phase wires it. Inert in practice for the v1 subscription-billed
   Claude adapter (`HarnessResult.total_cost_usd` is populated but nothing
   currently reads it for a hard stop) but must be implemented so a future
   per-token adapter needs no new mechanism.
6. **Run-level 10h wall clock**; in-flight task returns to `QUEUED` on
   expiry (`timeouts.run_wall`, already in config).
7. **`run.summary`** (§9.2): completed/blocked/retried/flaky/merge-conflict
   counts, total duration/cost, plus repeated-merge-conflict and
   knowledge-file-approaching-cap recommendations (spec 3.4, 11).

### Exit criteria (from the plan)

- `cosmo run` executes a multi-task DAG in dependency order against fakes.
  (This almost certainly means extending the existing `cosmo run` command
  from Phase 7 — currently `--task <id>`, single task only — rather than
  adding a second command; decide and document whichever you choose, the
  same way every previous phase has for an ambiguous CLI surface.)
- Tests: breaker trips on distinct-task blocks and *not* on merge conflicts
  or flakes; 5-hour pause auto-resumes; weekly cap beyond budget stops
  rather than idles; per-task cost ceiling blocks one task and leaves the
  queue running.
- `cosmo run --dry-run` prints the resolved execution order without
  executing.

## Things to know before you start

**Phase 7's `task.machine.run_task` is your real per-task caller — call it,
don't reimplement any part of it.** It already owns: the full per-task
state machine, per-state timeouts, progress/heartbeat watching, the
`IMPLEMENTING`/`VALIDATING` retry cycle with correct (0-indexed,
peek-before-increment) attempt-count bookkeeping, informed retries, and the
`COMMITTING`/`MERGING` steps. Phase 8's run loop calls `run_task` once per
eligible task in DAG order; it does not re-derive any of Phase 7's
per-task retry/classification logic.

**Phase 7 deliberately left `environment_error` retries bounded by a local,
ad hoc counter (`config.retries.max_attempts`, reused) instead of the real
circuit breaker** (decision 6, state doc) — this was explicitly flagged as
an interim measure. Phase 8's circuit breaker is the real mechanism spec
6.5 describes; it is not required to preserve Phase 7's local bound once
the breaker exists, but should be designed with awareness that Phase 7's
bound already stops a *single task* from looping forever against a broken
environment — the breaker's job is stopping the *whole run* across
distinct tasks, a different scope.

**`VALIDATING`'s own external wall/stall timeout was deliberately not
wired in Phase 7** (decision 7, state doc) — `run_validation_gate` has no
`cancel()` hook, so a real fix needs either adding one (bigger surgery on
Phase 6's gate runner) or accepting the current structural protection via
`gate.stage_timeout_seconds`. Not obviously Phase 8's job either, but worth
being aware of if a run-loop-level timeout ever needs to reach into a
single task's `VALIDATING` state.

**No project/repo linkage on `task_queue` yet** (Phase 7's own "Things that
will matter later") — a multi-task run loop driving tasks that might belong
to different registered projects will need either a `task_queue.project_id`
column or an equivalent resolution step; Phase 7's `cosmo run` sidesteps
this entirely with an explicit `--repo` flag, which won't scale to a real
multi-task DAG run across projects if that's ever a real scenario (v1 may
be fine assuming one project per run — decide and document).

## When you finish

1. `./check.sh` green.
2. Update `v3-implementation-state.md`: mark Phase 8 complete, list what
   exists, record every decision made and anything a future session would
   otherwise rediscover. Append any new spec deviation to the cumulative
   table at the bottom (next number is 21).
3. Commit to `develop` with a message explaining *why*, in the style of the
   Phase 0-7 commits.
4. Rewrite this handoff for Phase 9 — or delete it if the next session
   continues immediately.

Phase 9 is next: observability (native OTel export, log/disk management,
Playwright trace retention), and deployment (the systemd unit, watchdog
pings, journald rate limits) — the plan's own §9.4/§9.5 items Phase
0-8 have not yet touched.
