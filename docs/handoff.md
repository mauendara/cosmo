# Handoff — v5 improvements plan implemented; Phase 10 acceptance run still in progress

You are picking up Cosmo mid-build. **Phases 0-9 of the original plan, the
v4 workflow-changes feature, and now the v5 improvements plan (crash
recovery, `cosmo run resume`, notifications, `--follow`, live-terminal
observability, the quota-bypass flag, and part 5's Class 1
failure-signature classifier) are all implemented.** What's left is **Phase
10 — the original plan's last phase — and it means real validation of
what's now built, not more implementation**: the overnight acceptance run
itself is already underway (see below), and this session's own v5 work
added a specific, itemized list of real-invocation checks to Phase 10's
scope (real Telegram delivery, a real process kill, a real `cosmo run
resume`, a real credits-bypass run — see "What still needs validating"
below). Read [v3-implementation-state.md](v3-implementation-state.md)'s
two newest sections in full before doing anything: "Phase 10 — acceptance
run (in progress)" and "v5 improvements plan — Implemented" (including its
own "Two real bugs found and fixed after the first implementation pass"
subsection — one was a pre-existing observability bug, the other was a
bug this same v5 pass introduced and caught before it shipped).

## What actually happened this session (v5 improvements plan)

Everything in [v5-improvements-plan.md](v5-improvements-plan.md) parts 1-4,
6, and 7, plus part 5's Class 1, is now real code, not a design record:

- **Crash recovery** (`src/cosmo/run/recovery.py`): a pidfile lock (one
  `cosmo run`/`cosmo run resume` at a time per `data_dir`) and
  `reconcile_interrupted_tasks`, which requeues any task caught mid-flight
  by a crash without touching its retry budget, and marks orphaned
  `run_state` rows `crashed` — carefully excluding the run currently being
  started/resumed itself from that scan (deviation 58; this was a real bug
  caught by a new test, not shipped broken).
- **`cosmo run resume [run_id]`** — a real Typer subcommand (`run` is now a
  sub-app, not a leaf command; `cosmo run --task`/`--dry-run` are
  unchanged).
- **Notifications** (`src/cosmo/notify/`): a `Sink` protocol, a Telegram
  implementation (stdlib `urllib`, no new dependency), `cosmo notify
  watch`, and `deploy/cosmo-notify.service`.
- **`cosmo events tail --follow` / `cosmo report --follow`.**
- **A `failure_signature` classifier** (`src/cosmo/store/failure_signature.py`,
  migration 8) — deterministic substring matching (`missing_lockfile`,
  `node_engine_mismatch`, `enoent_node_modules`), computed automatically
  inside `StoreWriter.record_task_failure`.
- **A coarse live-terminal event hook** — `EventEmitter.on_emit`, wired into
  `cosmo run`/`cosmo run resume` so the attached terminal shows state
  transitions/pauses, not just per-tool-call chatter.
- **`quota.bypass_5h_with_credits`** — an opt-in flag to keep going past a
  confirmed 5-hour quota pause when usage credits are covering calls,
  gated by a config validator requiring a real cost ceiling.

Two migrations (7, 8), 8 new spec deviations (50-58 — see the cumulative
table in the state doc), and 465 tests passing (`./check.sh` green). Also
found and fixed, by hand, during a real crash-recovery smoke test (not part
of the plan's own scope, but directly affecting the same code):
`run.loop.run_queue`'s `disk_low`/DAG-cycle-at-startup abort paths used to
emit `RUN_STOPPED` twice for one stop — now exactly once, with the richer
`critical`-severity detail preserved.

**Not done, deliberately** (see the state doc's "v5 improvements plan —
Implemented" section for the full reasoning): part 5's Class 2 research
(auditing whether other session-management tools share the
`ScheduleWakeup`/`ToolSearch`/`TaskOutput` gap — that one instance was
already fixed *before* this v5 pass, as deviation 49, but a broader audit
was never this pass's job); and every real-invocation verification the
plan's own "Verification" section calls for (see next section) — those all
need a real Telegram bot, a real process to kill, or a real usage-credits
account, none of which exist in this dev sandbox.

## What still needs validating (this is now Phase 10's own scope, not new implementation)

All of the following are real-invocation checks against already-implemented
code — nothing here should require writing new production code unless one
of them surfaces an actual bug (in which case: fix it, record it as a new
deviation, keep going):

- **A real Telegram bot token/chat id** actually receiving a message end to
  end via `cosmo notify watch` (`TelegramSink.send`'s real HTTP call is
  unverified; its message *formatting* is unit-tested in
  `tests/test_notify_telegram.py`).
- **A real `kill -9`** of a `cosmo run` process mid-`IMPLEMENTING`/
  `VALIDATING` against a real target repo, confirming the *next*
  `cosmo run` picks the task back up via `reconcile_interrupted_tasks`.
  Proven so far only by seeding a crashed-looking DB state by hand
  (`tests/test_run_recovery.py`, plus a real CLI smoke test) — not by an
  actual process kill.
- **A real `cosmo run resume`** against a real paused run — directly
  applicable to the acceptance run below once its quota window clears (or
  whenever it's next found paused).
- **A real `bypass_5h_with_credits=true` run** against an account whose
  usage credits are actually covering calls past a confirmed 5-hour window,
  confirming `QUOTA_BYPASSED` fires and the run keeps going.
- **`cosmo notify watch`'s `stale_after_seconds=1800` default** and the
  severity/allowlist notification rules, confirmed or retuned against a
  real multi-hour run with a real sink attached.
- **`deploy/cosmo-notify.service` alongside `deploy/cosmo-run.service`**
  under the same real `systemctl --user` verification the item below calls
  for.

Plus the Phase 10 acceptance run's own pre-existing open items (unrelated
to v5, already open before this session):

- Install and actually exercise `deploy/cosmo-run.service` on this host —
  still not done; this is the gap that let the acceptance run's own
  `cosmo run` process die silently (see next section) with nothing to
  restart it.
- `use-local-storage-hook` sits `blocked` (`reason: cost`) — needs a
  `cosmo queue retry` once upstream tasks clear.
- Open Item 2 (§3.3 timeout retuning against real p95 data) — still open,
  not enough real `IMPLEMENTING`/`VALIDATING`/`REVIEWING` duration data
  exists yet.
- `REVIEWING` still has zero real-`claude -p` verification.

## The acceptance run itself: real, in progress, currently paused

A real `cosmo run` (run_id `bdf4ab101aee484b98c7a833c014714d`, started
2026-08-27T02:26:04Z) has been driven against `/home/dev/delta/cosmo-tests/
todo-frontend-app` — `cosmo run` invoked directly, not yet under systemd
(see the open item above). `scaffold-app` reached real `IMPLEMENTING`, hit
`error_max_turns` after a session spent polling a backgrounded `npm
install` instead of blocking on it (deviation 49's root cause — already
fixed at the harness-policy level, see `templates/harness/claude/
settings.json`'s `permissions.deny`), then the run correctly detected a
real, **confirmed** `quota_exhausted_5h` signal and paused
(`resume_delay_seconds` ~8716s). **The `cosmo run` process then died
silently during that pause's in-process `sleep()`** — SIGTERM, not a
crash, no OOM, no reboot, cause not conclusively identified (WSL2
memory-pressure signature is the best lead, not proven). See the state
doc's Phase 10 section for the full forensic trail.

**This is now directly actionable with what shipped this session**: the
*next* `cosmo run` (or, better, `cosmo run resume`) against this same
target repo will run `reconcile_interrupted_tasks` on startup and requeue
`scaffold-app` cleanly instead of leaving it stuck — confirm with the user
before resuming (per project memory, they asked to drive this resumption
themselves; don't do it unprompted). Once genuinely ready to resume: `cosmo
run resume` (not a fresh `cosmo run`) is now the correct tool — it reuses
the paused run's own `run_id`, so cost accounting and history stay
attached to the same run rather than starting a new one.

## Read these first, in this order

| Document | What it is | How to treat it |
|---|---|---|
| [v3-cosmo-autonomous-agent-spec.md](v3-cosmo-autonomous-agent-spec.md) | The authoritative specification | **Source of truth** for the original 0-10 plan. v1 and v2 are superseded — read them only for history |
| [v3-implementation-plan.md](v3-implementation-plan.md) | 11-phase build plan | The map for Phase 10 (its own section, near the end). **Do not edit** — it's the agreed scope; record decisions in `v3-implementation-state.md` instead |
| [v3-implementation-state.md](v3-implementation-state.md) | What actually exists, plus decisions and gotchas | Read the "Phase 10 — acceptance run (in progress)" and "v5 improvements plan — Implemented" sections in full before doing anything — both are load-bearing for what's left |
| [v4-changes-to-workflow-plan.md](v4-changes-to-workflow-plan.md) | The raw-spec-workflow feature design | Implemented — see its own Status line. Read it for *why* the `REVIEWING`/`FINISHING` states and `cosmo spec` commands are shaped the way they are; read the state doc's v4 section for what's actually real |
| [v5-improvements-plan.md](v5-improvements-plan.md) | Crash/pause resume, Telegram notifications, `--follow`, live-terminal observability, an opt-in usage-credits quota-bypass flag, and the harness failure-pattern research (§5) | **Implemented**, parts 1-4/6-7 plus part 5's Class 1 — see its own Status line. Part 5's Class 2 (the broader session-management-tool audit) remains open, exactly as the plan itself left it |

`v1-*` and `v2-*` in this folder are earlier spec drafts. v3 is a superset of
both. Do not implement from them.

Three more files in this folder are historical, already fully consumed —
don't re-read them looking for open work: [simple-template-handoff.md](simple-template-handoff.md)
scoped the `vite-react-local` template, now built; `old-agents-skills/` is
the user's pre-Cosmo Claude Code skill/agent files, mined once for ideas
that fit Cosmo's headless model; both are described further in the state
doc's older "Phase 10 prep" section if you need the history.

## Where things are

```
/home/dev/delta/cosmo/          # working branch: develop
├── docs/                       # the five documents above
├── deploy/                     # cosmo-run.service (Phase 9) + cosmo-notify.service (v5), README
├── templates/                  # harness + project templates (source of truth)
│   └── projects/{_blank,java-spring-react,vite-react-local}/
├── src/cosmo/
│   ├── checks.py, doctor.py, config/, harness/
│   ├── bootstrap/                # cosmo init: openspec/docs/symlinks/git-identity/git-branch
│   ├── watchdog.py                 # Phase 9: sd_notify, hand-rolled, no dependency
│   ├── retention.py                # Phase 9: paths.log_dir rotation
│   ├── git/{merge,worktree,secrets}.py
│   ├── gate/                     # Phase 6: the Docker validation gate
│   ├── task/                     # Phase 7/v4: the per-task state machine
│   ├── spec/                     # v4: *-task.md frontmatter parsing
│   ├── knowledge/                # Phase 7: spec 11's COMMITTING-step guardrails
│   ├── run/                      # Phase 8/9: run-level state machine, DAG, breaker, quota, cost
│   │   ├── loop.py                 # v5: run_queue is now a thin lock-acquiring wrapper around
│   │   │                             _run_queue_locked; reconcile_interrupted_tasks wired in;
│   │   │                             resume_run_id param; QUOTA_BYPASSED emission
│   │   ├── recovery.py             # v5: new -- acquire_run_lock/RunLock, reconcile_interrupted_tasks
│   │   └── quota.py                # v5: QuotaDecision gains bypassed: bool, RunStatus.RUNNING legal
│   ├── notify/                   # v5: new -- Sink protocol, TelegramSink, cosmo notify watch's loop
│   ├── store/                    # SQLite schema, StoreWriter, reader queries
│   │   ├── migrations.py            # 8 migrations now -- 7-8 are v5 (stop_reason gains 'crashed',
│   │   │                              task_failures gains failure_signature)
│   │   ├── failure_signature.py     # v5: new -- deterministic classifier, lives here (not
│   │   │                              cosmo.task) to avoid a real import cycle -- see deviation 51
│   │   └── enums.py                 # v5: StopReason.CRASHED
│   ├── events/                   # v5: EventEmitter gains an optional on_emit hook
│   ├── proc/                     # ManagedProcess, WallClockTimer/StallTimer/LivenessTimers, orphan sweep
│   └── cli/main.py               # v5: `run` is now a sub-app (adds `run resume`); `notify watch`;
│                                    `events tail --follow`; `report --follow`; _print_emit
├── tests/                       # 465 passing + 8 opt-in real-Docker/real-openspec
│   └── fixtures/gate_repo/        # real Spring Boot + Vite/React fixture, reusable for your own tests too
└── check.sh                     # ruff + format + mypy --strict + pytest
```

## Get oriented (2 minutes)

```bash
cd /home/dev/delta/cosmo
git log --oneline           # this session's v5 improvements plan commit should be at HEAD
git branch --show-current   # should say develop
./check.sh                  # must be green before you change anything
cosmo doctor                # core checks + harness checks in two tables
```

**Known, pre-existing environment noise on this host** (not something a
prior phase broke, don't chase it): `cosmo doctor` may show `disk space:
FAIL` — this WSL2 box runs close to the 10 GB floor at the *test* data path
it checks (`/tmp` is a small tmpfs on this box); the real filesystem has
hundreds of GB free. It may also show `event/state store: schema at version
N, this build expects 8` if this shell's own `XDG_DATA_HOME` sandbox
predates migrations 7-8 — harmless, self-resolving the next time any
command opens a `StoreWriter` (migrations are additive and applied
automatically). This box still has no *global* git identity (only this
repo's own local config has one); `cosmo init` against a real target repo
seeds one automatically (`bootstrap.git_identity`). `gitleaks` is on PATH,
`docker` works, and so is the real `openspec` CLI.

**This host's WSL2 genuinely has systemd enabled** (`/etc/wsl.conf`'s
`[boot] systemd=true` — `ps -p 1 -o comm=` reports `systemd`, `systemctl
--user` works). This is exactly what "run unattended overnight under
systemd" needs, and it's still not actually been exercised on this host
(see "What still needs validating" above) — the acceptance run's own
silent process death is the direct consequence of that gap. See
`deploy/README.md` before installing either unit.

**One real environment gotcha remains from early phases**: **`npm install`
can hang indefinitely on this host if a previous run was killed
mid-install** (fix: verified-clean `rm -rf node_modules package-lock.json`
first, not waiting longer). Docker containers writing bind-mounted build
artifacts as root is already handled — `git.worktree.remove_worktree` falls
back to a throwaway root container automatically.

One more: **this session's shell may have `XDG_DATA_HOME=/tmp/cosmo-test/data`
set** (sandboxing `cosmo`'s own runtime state away from the real home
directory, and separate from the acceptance run's own real store — see the
state doc's project-memory note on this). `uv run cosmo ...` is the more
reliable invocation for anything scripted. To inspect/drive the *real*
acceptance-run store, unset both `XDG_DATA_HOME` and `COSMO_CONFIG`
explicitly (`env -u XDG_DATA_HOME -u COSMO_CONFIG cosmo ...`) rather than
assuming the default env is already clean — verify which data path you're
actually hitting before trusting what you see.

**Worth knowing before touching the acceptance run or an overnight retry:**

- `cosmo events tail --payload`/`--follow`, `cosmo report --follow`, and
  `cosmo queue failures <task-id>` are your tools for post-run review — not
  raw sqlite queries.
- **`WatchdogSec` in the shipped unit is 10800s (3h), task-boundary
  granularity, not task-internal** — a single wedged attempt is only caught
  at the *next* task-boundary ping. If tighter detection is needed, that's
  a real Phase 10 finding to record.
- **The circuit breaker's tally and quota heuristic's consecutive-failure
  count still live in-memory inside one `run.loop.run_queue`/
  `_run_queue_locked` call** — a systemd-restarted or `cosmo run resume`d
  run starts these counters from zero again.
- **Quota heuristic and secondary-signal config values are still
  unverified guesses** (`quota.heuristic_consecutive_threshold`/
  `heuristic_max_duration_seconds`/`result_error_subtypes`) — an overnight
  run is specifically positioned to confirm or falsify these for real.
- **`review.enabled`/`timeouts.reviewing_wall` are equally unverified
  guesses** — no real `claude -p` review-call duration data exists yet.

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
  `retries.delay_min`/`delay_max` to `0` via `cfg.model_copy(...)`. Any test
  exercising `run.loop.run_queue`/`task.machine.run_task` for real must also
  override `disk.min_free_gb` down near zero **and `review.enabled=False`**
  unless it's specifically testing `REVIEWING` — see `_fast_config` in
  `test_run_loop.py`/`test_task_machine.py` for both.
- **Fake the external process, test the mechanics — except where "check by
  hand, then use the real thing" already proved out.** `FakeHarnessAdapter`
  and `FakeGate` are the two test doubles later phases should target
  directly. Real-process/real-Docker/real-`openspec` tests exist but are
  skip-guarded because they take real time or need a real binary on PATH.
  Phase 10's own overnight run — and now the v5-validation checklist above
  — is the largest instance of this pattern in the whole project: there is
  no way to fake your way through it.
- **Boundary tests are load-bearing, not optional.** `test_harness_boundary.py`,
  `test_store_boundary.py`, `test_git_boundary.py`, `test_gate_boundary.py`.
- **Run `./check.sh` before committing.** All four must pass.
- **When something fails, check with a real invocation before trusting a
  unit test's green.** This has found a real bug or made a real design
  decision correctly in every phase so far, including this v5 pass — a
  real crash-recovery smoke test caught a self-crash bug this same session
  introduced (deviation 58), which no fake/unit test alone would have hit
  until one was specifically written to check the row count.

## Phase 10 scope (unchanged from the original plan)

1. Point Cosmo at a real target repo initialized by `cosmo init`. Queue
   5-10 genuine units of work with real `depends_on` edges. Already done —
   see "The acceptance run itself" above.
2. Run unattended overnight under systemd (`deploy/cosmo-run.service`) with
   production config. **Not yet done** — the unit is still not installed on
   this host; the acceptance run so far has been driven by hand.
3. Post-run review against the spec's own claims: did anything reach `DONE`
   without a passing gate; did any test get weakened; were any orphan
   processes/containers left; did quota handling behave; are the p95 gate
   numbers consistent with §3.3's defaults; if `REVIEWING` ran for real, did
   it produce usable verdicts.

### Exit criteria (from the plan)

- A full night's run completes with a coherent `run.summary` and an event
  log sufficient to reconstruct every decision without reading a raw log.
- **Open Item 2** closed: §3.3 timeouts retuned against real p95 data (and,
  if `REVIEWING` ran for real, `timeouts.reviewing_wall` alongside them), or
  explicitly confirmed as-is with real data behind the confirmation.

## When you finish

1. `./check.sh` green (if any code changed at all).
2. Update `v3-implementation-state.md`: mark Phase 10 complete, record the
   overnight run's real findings (not a summary of what was *supposed* to
   happen — what actually did), and append any new spec deviation to the
   cumulative table (next number is 59).
3. Commit to `develop` with a message explaining *why*, in the style of the
   Phase 0-9/v4/v5 commits.
4. This is the last phase in the original plan — there is likely no further
   handoff to write once Phase 10's exit criteria are actually met. If real
   work remains beyond the checklist above, record it as an open item in
   the state doc rather than inventing a new phase number the plan never
   named.
