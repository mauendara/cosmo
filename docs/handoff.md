# Handoff — continue at Phase 10

You are picking up Cosmo mid-build. Phases 0-9 are complete. Your job is
Phase 10: acceptance — a real target repo, 5-10 genuine OpenSpec changes
with real `depends_on` edges, run unattended overnight under systemd with
production config, then a post-run review against the spec's own claims.

## Read these first, in this order

| Document | What it is | How to treat it |
|---|---|---|
| [v3-cosmo-autonomous-agent-spec.md](v3-cosmo-autonomous-agent-spec.md) | The authoritative specification | **Source of truth.** v1 and v2 are superseded — read them only for history |
| [v3-implementation-plan.md](v3-implementation-plan.md) | 11-phase build plan | The map. Phase 10 is your scope (its own section, near the end) |
| [v3-implementation-state.md](v3-implementation-state.md) | What actually exists, plus decisions and gotchas | Read the "Phase 9 — Complete" section in full before doing anything — several of its decisions and open items are load-bearing for Phase 10 |

`v1-*` and `v2-*` in this folder are earlier spec drafts. v3 is a superset of
both. Do not implement from them.

**Do not edit the plan document.** It is the agreed scope. Record what you
build, and any decision you make along the way, in `v3-implementation-state.md`.

## Where things are

```
/home/dev/delta/cosmo/          # working branch: develop
├── docs/                       # the three documents above
├── deploy/                     # Phase 9: cosmo-run.service, README (install notes, WSL2 caveat)
├── templates/                  # harness + project templates (source of truth)
├── src/cosmo/
│   ├── checks.py, doctor.py, config/, harness/, bootstrap/
│   ├── watchdog.py               # Phase 9: sd_notify, hand-rolled, no dependency
│   ├── retention.py              # Phase 9: paths.log_dir rotation (7d done / 30d blocked)
│   ├── git/                      # Phase 5: worktree lifecycle, merge ladder
│   ├── gate/                     # Phase 6: the Docker validation gate
│   ├── task/                     # Phase 7: the per-task state machine
│   ├── knowledge/                # Phase 7: spec 11's COMMITTING-step guardrails
│   ├── run/                      # Phase 8/9: run-level state machine, DAG, breaker, quota, cost
│   │   └── loop.py                 # run_queue -- gained the pre-run disk check, log
│   │                                retention call, and watchdog pings this phase
│   ├── store/                    # SQLite schema, StoreWriter, reader queries
│   │   ├── migrations.py            # 3 migrations now -- run_state.stop_reason gained disk_low
│   │   └── enums.py                 # StopReason.DISK_LOW (Phase 9)
│   ├── events/                   # envelope + EventEmitter + emit_state_changed
│   ├── proc/                     # ManagedProcess, WallClockTimer/StallTimer/LivenessTimers, orphan sweep
│   └── cli/main.py               # `cosmo` command -- gained `cosmo report` this phase
├── tests/                       # 334 passing + 7 opt-in real-Docker (COSMO_GATE_DOCKER_E2E=1)
│   └── fixtures/gate_repo/        # real Spring Boot + Vite/React fixture, reusable for your own tests too
└── check.sh                     # ruff + format + mypy --strict + pytest
```

Nothing empty is waiting for you the way `cosmo.run` was for Phase 8, or
`deploy/` was for Phase 9 — Phase 10 is not a code-writing phase in the
same sense as 0-9. It is: seed real OpenSpec changes into a real target
repo, run the thing for real, unattended, overnight, then write down what
actually happened. Whatever code changes *do* come out of it should be
small and targeted — a real bug the overnight run surfaces, or Open Item
2's timeout retuning, not new features.

## Get oriented (2 minutes)

```bash
cd /home/dev/delta/cosmo
git log --oneline           # Phase 9 should be committed at HEAD
git branch --show-current   # should say develop
./check.sh                  # must be green before you change anything
cosmo doctor                # core checks + harness checks in two tables
```

**Known, pre-existing environment noise on this host** (not something
Phase 9 broke, don't chase it): `cosmo doctor` may show `disk space: FAIL`
— this WSL2 box runs close to the 10 GB floor at the *test* data path it
checks (`/tmp` is a small tmpfs on this box); the real filesystem has
hundreds of GB free. This box has no *global* git identity either — only
this repo's own local config has one — so any test fixture your own work
adds that calls `git commit` needs `-c user.name=...`/`-c
user.email=...` passed explicitly. `gitleaks` is on PATH, `docker` works.

**This host's WSL2 genuinely has systemd enabled** (`/etc/wsl.conf`'s
`[boot] systemd=true`, confirmed for real in Phase 9 — `ps -p 1 -o comm=`
reports `systemd`, `systemctl --user` works). This is exactly what Phase
10's "run unattended overnight under systemd" exit criterion needs — it is
testable here, not just on a real droplet. See `deploy/README.md` before
installing the unit; it documents the exact `Restart=`/
`RestartPreventExitStatus=` reasoning and how it was verified for real in
Phase 9 (throwaway `systemctl --user` units, not just read the docs).

**Two real environment gotchas from Phase 6, reconfirmed since** — read
Phase 6's state-doc section for the full diagnosis before you touch
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

**From Phase 9, worth knowing before an overnight run:**

- **`git.worktree.sweep_stale_worktrees` is still never called from
  anywhere** (flagged in Phase 8's state doc, restated in Phase 9's — still
  true). A multi-task overnight run will leave every `DONE` task's worktree
  on disk indefinitely; only a `BLOCKED` task's worktree is *supposed* to
  survive (spec 3.2). This will very likely bite a real overnight run on
  disk space alone — decide whether to wire the sweep in before or as part
  of Phase 10's own run, not discover it as the run's own failure mode.
- **`WatchdogSec` in the shipped unit is 10800s (3h), task-boundary
  granularity, not task-internal** (Phase 9 decision 7/8) — a single
  wedged `IMPLEMENTING`/`VALIDATING` attempt is only caught at the *next*
  task-boundary ping, not immediately. If the overnight run needs tighter
  detection, that's a real Phase 10 finding to record, not a Phase 9 bug.
- **The circuit breaker's tally and quota heuristic's consecutive-failure
  count still live in-memory inside one `run.loop.run_queue` call** —
  unchanged since Phase 8. A systemd-restarted run (post-watchdog-kill or
  a clean `on-failure` case) starts these counters from zero again, same
  as a hand-restarted one.
- **No CLI command to resume a `PAUSED` run** — still true. `cosmo report`
  (Phase 9) makes a paused run's state legible after the fact but doesn't
  add a resume path; re-running `cosmo run` starts a fresh `run_id`.
- **Quota heuristic and secondary-signal config values are still
  unverified guesses** (`quota.heuristic_consecutive_threshold`/
  `heuristic_max_duration_seconds`/`result_error_subtypes`, Phase 8
  decisions 4/5) — an overnight run is specifically positioned to confirm
  or falsify these for real.

## Conventions this codebase follows

- **Python 3.12+, `uv`-managed.** Add dependencies with `uv add`, not by hand.
- **`mypy --strict` passes.** Annotate everything, including test helpers.
- **Comments explain *why*, never *what*.** Existing comments cite the spec
  section that forced the decision. Match that.
- **Config over constants.** Every tunable goes in `config/model.py` and
  `config/defaults.toml`, annotated with its spec section. No magic numbers.
- **Tests isolate from the developer's environment.** Anything touching
  config must set `COSMO_CONFIG` and `XDG_DATA_HOME` to temp paths — see the
  autouse fixture in `tests/test_cli.py`/`test_cli_run_queue.py`. Anything
  touching a real git repo should build one in `tmp_path`, never touch this
  repo or a real target repo. Retry-driven tests should override
  `retries.delay_min`/`delay_max` to `0` via `cfg.model_copy(...)`. **New in
  Phase 9: any test exercising `run.loop.run_queue` for real must also
  override `disk.min_free_gb` down near zero** (`_fast_config` in
  `test_run_loop.py` already does this) — the pre-run disk check is real,
  not injectable, and will otherwise fail against this host's own small
  `/tmp` tmpfs.
- **Fake the external process, test the mechanics — except where "check by
  hand, then use the real thing" already proved out.** `FakeHarnessAdapter`
  and `FakeGate` are the two test doubles later phases should target
  directly. Real-process/real-Docker tests exist but are skip-guarded
  (`COSMO_GATE_DOCKER_E2E=1`) because they take real minutes. Phase 10's
  own overnight run is the largest instance of this pattern in the whole
  project — there is no way to fake your way through an acceptance phase.
- **Boundary tests are load-bearing, not optional.** `test_harness_boundary.py`,
  `test_store_boundary.py`, `test_git_boundary.py`, `test_gate_boundary.py`.
- **Run `./check.sh` before committing.** All four must pass.
- **When something fails, check with a real invocation before trusting a
  unit test's green.** This has found a real bug or made a real design
  decision correctly in every phase so far. Phase 9's own instance: the
  pre-run disk check exposed itself as *correct* against a real low-space
  `/tmp`, not broken — the fix was the tests' own isolation, not the
  check. Phase 9 also did the two other real-invocation checks the plan
  asked for: a real `claude -p` probe with console OTel exporters to grep
  for content leakage (found none — `TELEMETRY_ENV` was already correct),
  and real `systemctl --user` units to prove the watchdog restart/
  no-restart split actually behaves as designed.

## Phase 10 scope

Per the plan's own "Acceptance: unattended overnight run" section:

1. Point Cosmo at a real target repo initialized by `cosmo init`. Queue
   5-10 genuine OpenSpec changes with real `depends_on` edges.
2. Run unattended overnight under systemd (`deploy/cosmo-run.service`,
   Phase 9) with production config (a real `COSMO_CONFIG` pointing at
   non-XDG paths, not the dev defaults).
3. Post-run review against the spec's own claims: did anything reach
   `DONE` without a passing gate; did any test get weakened; were any
   orphan processes/containers left; did quota handling behave; are the
   p95 gate numbers consistent with §3.3's defaults.

### Exit criteria (from the plan)

- A full night's run completes with a coherent `run.summary` and an event
  log sufficient to reconstruct every decision without reading a raw log
  (`cosmo report` and `cosmo events tail`, both already built, are your
  tools for this — if either turns out insufficient for real post-run
  review, that's a real Phase 10 finding).
- **Open Item 2** closed: §3.3 timeouts retuned against real p95 data, or
  explicitly confirmed as-is with real data behind the confirmation.

## When you finish

1. `./check.sh` green (if any code changed at all).
2. Update `v3-implementation-state.md`: mark Phase 10 complete, record the
   overnight run's real findings (not a summary of what was *supposed* to
   happen — what actually did), and append any new spec deviation to the
   cumulative table (next number is 29).
3. Commit to `develop` with a message explaining *why*, in the style of
   the Phase 0-9 commits.
4. This is the last phase in the plan — there is likely no Phase 11
   handoff to write. If real work remains (the worktree sweep, watchdog
   granularity, a resume-paused-run command, or anything the overnight run
   itself surfaced), record it as an open item in the state doc rather
   than inventing a new phase number the plan never named.
