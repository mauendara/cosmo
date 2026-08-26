# Handoff — continue at Phase 9

You are picking up Cosmo mid-build. Phases 0-8 are complete. Your job is
Phase 9: observability (native OTel export, log/disk management, Playwright
trace retention) and deployment (the systemd unit, watchdog pings, journald
rate limits).

## Read these first, in this order

| Document | What it is | How to treat it |
|---|---|---|
| [v3-cosmo-autonomous-agent-spec.md](v3-cosmo-autonomous-agent-spec.md) | The authoritative specification | **Source of truth.** v1 and v2 are superseded — read them only for history |
| [v3-implementation-plan.md](v3-implementation-plan.md) | 11-phase build plan | The map. Phase 9 is your scope (§9.4, §9.5, §1, §12) |
| [v3-implementation-state.md](v3-implementation-state.md) | What actually exists, plus decisions and gotchas | Read the "Phase 8 — Complete" section in full before writing code — several of its decisions and deferred items are load-bearing for Phase 9 |

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
│   ├── task/                     # Phase 7: the per-task state machine
│   │   ├── machine.py              # run_task -- gained run_id/on_harness_result/check_run_guard hooks in Phase 8
│   │   └── types.py                # RunGuardAction (Phase 8) -- the run loop's seam into run_task
│   ├── knowledge/                # Phase 7: spec 11's COMMITTING-step guardrails
│   ├── run/                      # Phase 8: run-level state machine, DAG, breaker, quota, cost
│   │   ├── loop.py                 # run_queue -- the orchestrator; your own new work plugs in around this
│   │   ├── dag.py                  # resolve_execution_order, find_cycle
│   │   ├── breaker.py              # CircuitBreaker
│   │   ├── quota.py                # observe_harness_result, HeuristicTracker, decide
│   │   ├── cost.py                 # check_run_cost, task_cost_ceiling_reached
│   │   └── types.py                # RunSummary, RunOutcome
│   ├── store/                    # SQLite schema, StoreWriter, reader queries
│   │   └── enums.py                 # RunStatus/PauseReason/StopReason -- run_state/run_cost/task_cost now have real writers (Phase 8)
│   ├── events/                   # envelope + EventEmitter + emit_state_changed
│   │   └── envelope.py              # EventType.RUN_COST_WARNING added in Phase 8 (deviation 21) -- not in spec 9.2's own list
│   ├── proc/                     # ManagedProcess, WallClockTimer/StallTimer/LivenessTimers, orphan sweep
│   ├── cli/main.py               # `cosmo` command -- `cosmo run` (--task or the DAG path), `cosmo doctor` is what you likely extend
│   └── run/                      # not empty anymore -- Phase 8's run loop
├── tests/                       # 316 passing + 7 opt-in real-Docker (COSMO_GATE_DOCKER_E2E=1)
│   └── fixtures/gate_repo/        # real Spring Boot + Vite/React fixture, reusable for your own tests too
└── check.sh                     # ruff + format + mypy --strict + pytest
```

There is no empty package waiting for you the way `cosmo.run` was for Phase
8 — Phase 9's build items are additive to existing modules
(`harness/claude/adapter.py`'s env vars, `doctor.py`'s disk check,
`run.loop.run_queue`'s pre-run gate, log/trace retention somewhere new) plus
one genuinely new piece: the systemd unit and its watchdog-ping wiring,
which has no existing home at all. Decide where that lives (a new
`deploy/` directory at the repo root is the natural guess, matching where a
systemd unit file conventionally lives — not under `src/cosmo/`, which is
importable Python) and document the choice, the same way every previous
phase has for an ambiguous surface.

## Get oriented (2 minutes)

```bash
cd /home/dev/delta/cosmo
git log --oneline           # Phase 8 should be committed at HEAD
git branch --show-current   # should say develop
./check.sh                  # must be green before you change anything
cosmo doctor                # core checks + harness checks in two tables
```

**Known, pre-existing environment noise on this host** (not something Phase
9 broke, don't chase it): `cosmo doctor` may show `disk space: FAIL` — this
WSL2 box runs close to the 10 GB floor at the *test* data path it checks
(`/tmp` is a small tmpfs on this box); the real filesystem has hundreds of
GB free. This box has no *global* git identity either — only this repo's
own local config has one — so any test fixture your own work adds that
calls `git commit` needs `-c user.name=...`/`-c user.email=...` passed
explicitly. `gitleaks` is on PATH, `docker` works.

**Two real environment gotchas from Phase 6, reconfirmed in Phase 7** —
read Phase 6's state-doc section for the full diagnosis before you touch
anything Docker- or npm-related: **`npm install` can hang indefinitely on
this host if a previous run was killed mid-install** (fix: verified-clean
`rm -rf node_modules package-lock.json` first, not waiting longer), and
**Docker containers write bind-mounted build artifacts as root**, which
blocks a later unprivileged `rm -rf` — worked around by hand with a
throwaway `alpine` container. **This is still unfixed** and will bite you
again if you run any real-Docker test repeatedly.

One more: **this session's shell may have `XDG_DATA_HOME=/tmp/cosmo-test/data`
set** (sandboxing `cosmo`'s own runtime state away from the real home
directory). `uv run cosmo ...` (this project's own `.venv`) is unaffected by
this and is the more reliable invocation for anything scripted; if you ever
need `uv tool install --editable .` again, run it as `env -u XDG_DATA_HOME
uv tool install --editable --force .` or it will reinstall into the wrong
place and leave `~/.local/bin/cosmo` dangling.

**New from Phase 8, worth knowing before you touch the run loop:** the
circuit breaker's tally, and the quota heuristic's consecutive-failure
count, both live in-memory inside `run.loop.run_queue`'s single call —
neither survives a process restart, and neither is reconstructed from the
database on startup. A `PAUSED` run's *reason* survives (the persisted
`run_state.status`/`pause_reason` row), but nothing currently resumes a
paused run except re-invoking `cosmo run` from scratch, which starts a
brand-new `run_id`. If Phase 9's systemd unit needs to "restart a wedged
loop," it will restart it as a fresh run, re-resolving the DAG from
whatever `task_queue` state currently holds — confirm that's the behavior
you actually want before building the watchdog-restart path around it.

## Conventions this codebase follows

- **Python 3.12+, `uv`-managed.** Add dependencies with `uv add`, not by hand.
- **`mypy --strict` passes.** Annotate everything, including test helpers.
- **Comments explain *why*, never *what*.** Existing comments cite the spec
  section that forced the decision. Match that.
- **Config over constants.** Every tunable goes in `config/model.py` and
  `config/defaults.toml`, annotated with its spec section. No magic numbers.
  `DiskConfig`/`min_free_gb` already exists and is already read by `cosmo
  doctor` (`doctor.py:41`) — Phase 9's "pre-run disk check" needs a new
  *call site* (inside `run.loop.run_queue`, before the loop starts), not
  necessarily a new config field. Check what's already there before adding
  more, the same instruction every previous phase's handoff has given.
- **Tests isolate from the developer's environment.** Anything touching
  config must set `COSMO_CONFIG` and `XDG_DATA_HOME` to temp paths — see the
  autouse fixture in `tests/test_cli.py`/`test_cli_run_queue.py`. Anything
  touching a real git repo should build one in `tmp_path`, never touch this
  repo or a real target repo. Retry-driven tests should override
  `retries.delay_min`/`delay_max` to `0` via `cfg.model_copy(...)`.
- **Fake the external process, test the mechanics — except where "check by
  hand, then use the real thing" already proved out.** `FakeHarnessAdapter`
  and `FakeGate` are the two test doubles later phases should target
  directly; `run.loop.run_queue`'s own integration tests
  (`tests/test_run_loop.py`) are the most recent example of that pattern at
  the run-loop level. Real-process/real-Docker tests exist but are
  skip-guarded (`COSMO_GATE_DOCKER_E2E=1`) because they take real minutes.
  A systemd-unit exit criterion ("a run under systemd survives a restart")
  almost certainly cannot be a fast unit test at all — treat it the way
  Phase 6/7 treated their own opt-in real-Docker tests: a documented, opt-in
  integration check, run for real by hand at least once this session, not
  just asserted possible.
- **Boundary tests are load-bearing, not optional.** `test_harness_boundary.py`,
  `test_store_boundary.py`, `test_git_boundary.py`, `test_gate_boundary.py`.
  None of them currently restrict `cosmo.run`'s imports either (confirmed
  while building Phase 8, which imports `cosmo.task`/`cosmo.gate`/
  `cosmo.git`/`cosmo.harness` freely) — re-verify this yourself before
  assuming it still holds for whatever Phase 9 touches.
- **Run `./check.sh` before committing.** All four must pass.
- **When something fails, check with a real invocation before trusting a
  unit test's green.** This has found a real bug or made a real design
  decision correctly in every phase so far, Phase 8 included: a standalone
  script driving `run_queue` directly (not a pytest test) caught a genuine
  unstubbed 5-hour `time.sleep` that every mocked/fake-clock test in the
  suite passed straight through, because none of them happened to script
  the exact sequence that triggered it (see state doc Phase 8 decision 6).
  Do the same for Phase 9: a real `CLAUDE_CODE_ENABLE_TELEMETRY=1` probe
  invocation to actually inspect exported telemetry for content leakage,
  not just a code-review assertion that content logging is off; a real (or
  at least real-`systemctl`-shaped) run to confirm the watchdog ping and
  restart behavior, not just a unit test of the ping-emission code.

## Phase 9 scope

Spec §9.4 (native OpenTelemetry export), §9.5 (log/disk management, systemd
unit), §1 (environment/stack — the systemd unit lives here conceptually),
§12 (non-goals — check nothing you build here quietly re-opens one).

Summary from the plan:

1. **Native OTel export**: `CLAUDE_CODE_ENABLE_TELEMETRY=1`, 60s export
   interval, **content logging off** (`OTEL_LOG_USER_PROMPTS=0` — already
   set by `harness/claude/adapter.py`'s `TELEMETRY_ENV`, confirm it's still
   correct and sufficient; this may already be fully done, verify before
   building anything new). Prompts and file contents in a telemetry backend
   is a data-exfiltration path on a private codebase — this is a hard
   requirement, not a nice-to-have.
2. **Log retention** (§9.5): per-task `raw_log_path` rotation — 7 days for
   `DONE`, 30 days for `BLOCKED`. Playwright traces/screenshots retained
   only for failing runs. Nothing in the codebase currently rotates or
   deletes anything under `paths.log_dir` — this is new.
3. **Pre-run disk check**: abort the run at `severity=critical` below
   `disk.min_free_gb` (already exists, already used by `cosmo doctor`) —
   wire the same check into `run.loop.run_queue` itself, before the loop's
   first task starts, not just as a `cosmo doctor` advisory.
4. **systemd unit**: `OOMPolicy=stop`, memory accounting, `WatchdogSec`
   with a ping issued on each state transition (the loop needs to actually
   call `sd_notify` or equivalent — check what's idiomatic in Python
   without a heavy dependency), raised journald rate limits so the loop's
   own logs aren't dropped. "Identical on the droplet and under WSL2" per
   the plan — WSL2 has systemd support behind a flag
   (`/etc/wsl.conf`'s `[boot] systemd=true`) on modern builds; confirm
   whether this host actually has it enabled before assuming the exit
   criterion is testable here at all, and say so either way rather than
   silently skipping it.
5. **`cosmo events`/`cosmo report` for post-run triage**: `cosmo events
   tail` already exists (Phase 1); a `run.summary` renderer is new — Phase
   8's `run.summary` event payload (`run.loop._fill_summary_extras`'s
   shape) is what it should render.

### Exit criteria (from the plan)

- A run under systemd survives a restart, and a deliberately wedged loop is
  caught by the watchdog and restarted.
- A simulated low-disk condition aborts the run before any task starts.
- No prompt or file content appears anywhere in exported telemetry —
  verified by inspection.

## Things to know before you start

**Phase 8's `run.loop.run_queue` is your real entry point for the pre-run
disk check and any watchdog-ping wiring** — it already has a clean
before-the-loop section (right after `writer.run_create`/`RUN_STARTED`)
that a disk check slots into, and a natural per-task-transition point
(everywhere it already calls `writer.run_transition`/emits an event) for a
watchdog ping. Don't reimplement the loop; extend it, the same "call it,
don't reimplement" discipline Phase 8 itself applied to Phase 7's
`run_task`.

**`EventType`'s enumerated list has drifted from spec 9.2's own text twice
now** (deviation 17's `HeartbeatSource.STREAM`, deviation 21's
`RUN_COST_WARNING`) — if Phase 9 needs its own new event type (a log-
rotation event? a disk-abort event?), the pattern is: add it, emit it, and
record it as the next cumulative deviation (next number is 26) rather than
overloading an existing type's payload to avoid adding one.

**No project/repo linkage on `task_queue` still** (Phase 7's "things that
will matter later," restated in Phase 8's own list) — if log
retention needs to know which *project* a task's logs belong to for
retention-policy purposes, this gap is still there. Likely doesn't matter
for Phase 9 (retention is keyed by task outcome and age, not project), but
worth checking before assuming otherwise.

## When you finish

1. `./check.sh` green.
2. Update `v3-implementation-state.md`: mark Phase 9 complete, list what
   exists, record every decision made and anything a future session would
   otherwise rediscover. Append any new spec deviation to the cumulative
   table at the bottom (next number is 26).
3. Commit to `develop` with a message explaining *why*, in the style of the
   Phase 0-8 commits.
4. Rewrite this handoff for Phase 10 — or delete it if the next session
   continues immediately.

Phase 10 is next: acceptance — a real target repo, 5-10 genuine OpenSpec
changes with real `depends_on` edges, run unattended overnight under
systemd with production config, then a post-run review against the spec's
own claims (nothing reached `DONE` without a passing gate, no test was
weakened, no orphan processes/containers, quota handling behaved, p95 gate
numbers match §3.3's defaults or get retuned — Open Item 2, still open).
