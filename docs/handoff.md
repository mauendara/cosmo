# Handoff — Phase 10 acceptance run: real harness/gate/retry bugs found and fixed by hand; `scaffold-app` queued and ready, not yet re-run

You are picking up Cosmo mid-build. **Phases 0-9 of the original plan, the
v4 workflow-changes feature, and the v5 improvements plan are all
implemented** (see the previous handoff/state-doc sections for that
history). **This session's own work is Phase 10 itself** — not new
features, but real bugs found and fixed by driving the Phase 10 acceptance
run's own `scaffold-app` task through several real failure cycles, plus one
small, deliberately-scoped new capability (deviation 66) that came directly
out of diagnosing one of those cycles. Read
[v3-implementation-state.md](v3-implementation-state.md)'s cumulative
deviations table, entries **59-67**, before doing anything else — this
document summarizes them, but the table has the precise file:line-grounded
detail. The table's own "Phase 10 — acceptance run (in progress)" section
below entry 58 is **not yet updated** with this session's findings — that's
real follow-up work the next session should do, not assume already done.

## What actually happened this session

Nine real, verified fixes, found by driving one real task
(`scaffold-app`, in `/home/dev/delta/cosmo-tests/todo-frontend-app`)
through repeated real `cosmo run` cycles against a real Docker daemon —
not implemented speculatively. In the order they were found:

1. **A real migration bug** (deviation 59): `store.migrations.migrate`'s
   recreate-copy-swap migrations (3/4/5/7) raised
   `sqlite3.IntegrityError: FOREIGN KEY constraint failed` against the
   real, populated acceptance-run database — invisible in every existing
   test and on a fresh DB, because both only insert a referencing row
   *after* migrating. Fixed by toggling `PRAGMA foreign_keys` off around
   each migration's own transaction, plus a `PRAGMA foreign_key_check`
   afterward to still catch a migration that leaves a real dangling
   reference.
2. **A third variant of the npm-install-backgrounding failure** (deviation
   60, following deviations 48/49): the harness backgrounded `npm install`
   a different way — `Bash`'s own `run_in_background: true`, which
   `permissions.deny`'s `ScheduleWakeup`/`ToolSearch`/`TaskOutput` denials
   never covered — then polled the PID with ordinary already-allowed shell
   commands for a whole `IMPLEMENTING` attempt, made zero `tasks.md`
   progress, and was killed by Cosmo's own stall timer. Fixed with a new
   `PreToolUse` hook, `background_task_guard.py`, denying that parameter
   directly.
3. **Two worktree-retry gaps** (deviations 61, 62): `queue retry`'s
   kept-worktree path never re-synced `.agent/<harness>/` (so fix #2 above
   wouldn't even have reached a retried attempt), and `cosmo run --task`'s
   single-task path never reused an existing worktree at all, colliding
   with its own already-checked-out branch on retry. Both fixed.
4. **The real root cause of #2's underlying symptom** (deviation 63): gate
   Docker containers run as root by default, so build/e2e output written
   into the bind-mounted worktree came back root-owned — the unprivileged
   harness session then had no way to ever clean it up itself (`rm`,
   `sudo`, cross-filesystem `mv` all fail `Permission denied`, confirmed
   live). Fixed with `--user "{uid}:{gid}"` + `HOME=/tmp` on every gate
   container, verified by hand against the real `node`, `maven`, and
   `mcr.microsoft.com/playwright` images (including a real non-root
   headless Chromium launch).
5. **Defense in depth for the same class of problem** (deviation 64):
   `reset_worktree_to_commit` now force-removes anything a dry-run
   `git clean -fdxn` still lists after the real `git clean -fdx` ran,
   reusing `remove_worktree`'s existing throwaway-root-container trick —
   covers any *other* future source of root-owned cruft, not just #4's.
6. **Cross-run repeat-failure learning** (deviation 65, user-requested):
   `store.failure_signature.detect_repeat_block` plus two new taxonomy
   entries; `queue retry` now refuses (reports every prior occurrence,
   requires `--force`) once a task's most recent block repeats a prior
   one's reason past `retries.repeat_block_threshold` (default 2) — real
   motivation: `scaffold-app`'s own `error_max_turns` block recurred 3
   times across 3 separate runs before this existed, silently handed 2
   more attempts each time.
7. **A real target-repo hygiene bug, found live**: `MERGING` blocked on
   `/home/dev/delta/cosmo-tests/todo-frontend-app` having an uncommitted
   `.agent/claude/CLAUDE.md` — leftover from `cosmo init`, unrelated to any
   task, never committed. Fixed by committing it; **how it went
   uncommitted in the first place is still not known** — nothing in
   Cosmo's own code writes to a *base repo's* `.agent/` outside the
   one-time `cosmo init` sync, so if this recurs after a future `cosmo
   run`, treat it as a real bug worth investigating, not assume it's a
   one-off.
8. **Resume-in-place for `COMMITTING`/`MERGING`** (deviation 66,
   user-requested and generalized correctly beyond the original single
   report): finding #7 above meant a real, fully `IMPLEMENTING`+
   `VALIDATING`+`REVIEWING`-passed `scaffold-app` implementation got
   discarded by `queue retry`'s old "reset to the `PROPOSING` commit"
   behavior, just to redo it identically after the actual (target-repo)
   problem was already fixed. New migration 9
   (`task_queue.resume_at_stage`) plus `task.machine.run_task(resume_at=
   TaskStatus.COMMITTING | MERGING)` let `queue retry` resume directly at
   whichever of those two stages actually failed with an `environment_
   error` — the only two stages with no in-run retry at all — without
   touching the worktree or `attempt_count`. Verified with `adapter.calls
   == []`/`gate.calls == []` assertions (zero harness/gate invocations,
   not just fewer), **not yet exercised through a real `cosmo run`** since
   it shipped after the one real `MERGING` block this session hit.
9. **A version-pin correction** (deviation 67), **not a durable fix by
   itself**: `gate.playwright_image`/`playwright_npm_version` moved from
   `v1.50.0-noble` down to `v1.49.0-noble` to match what `scaffold-app`'s
   `frontend/package.json` happened to have pinned; the target repo's own
   `docs/testing.md` now names `1.49.0` explicitly too, closing the actual
   gap (nothing previously told a fresh scaffold attempt which version to
   converge on). See [v6-project-template-aware-stuff-plan.md]
   (v6-project-template-aware-stuff-plan.md) below for why chasing this
   value in Cosmo's own global config is the wrong axis long-term.

Also written this session, **not implemented, a design record only**:
[v6-project-template-aware-stuff-plan.md](v6-project-template-aware-stuff-plan.md)
— prompted by a user question about whether Cosmo's harness-agnostic
architecture (a hard, boundary-tested line — `tests/test_harness_boundary.
py`) has an equivalent for being *stack*-agnostic (it doesn't:
`GateConfig`'s images/commands and `failure_signature`'s matchers are both
currently coupled to one fixed Java+Spring/Vite+React stack, one global
config, no per-project-template mechanism). Explicitly scoped as backlog,
not to be built opportunistically — needs a real second stack to prove any
abstraction against, the same way multi-harness support needs a real
second harness adapter.

**466 → 493 tests, all passing, `./check.sh` green.** No deviation above
required a compromise anywhere in the existing suite.

## Where the acceptance run actually stands right now

`scaffold-app` is **`queued`, `0/2` attempts, worktree confirmed clean**
(the real root-owned `node_modules_old`/`dist_old`/etc. from before fix #4
above are gone — checked by hand: `find ... -user root` returns nothing).
It has **not been run since deviations 63-67 all landed together** — the
one real run that got `scaffold-app` all the way through `VALIDATING`
(build/unit/e2e all passed — first time ever in this task's history) and
`REVIEWING` happened *before* the `resume_at` feature (deviation 66)
existed, so that implementation was discarded by the old-style `queue
retry` after fix #7 above. The next `cosmo run` starts `scaffold-app`
completely fresh at `IMPLEMENTING`.

Real per-task history worth knowing before touching `scaffold-app` again:
`cosmo queue failures scaffold-app` shows every real failure across every
run — reading it beats re-deriving from raw events. As of this session, no
single failure reason has recurred past `retries.repeat_block_threshold`
(2), so a plain `cosmo queue retry scaffold-app` (or letting a blocked
state resolve itself via the mechanisms above) will not hit the new
repeat-block guard yet.

The rest of the queue is unchanged from before this session:
`todo-data-model`/`use-todos-hook`/`todo-ui`/`todo-e2e` are `queued` but
blocked on `scaffold-app`'s own dependency edge; `use-local-storage-hook`
is `blocked` (`reason: cost`) and needs its own `cosmo queue retry` once
upstream tasks clear.

## What still needs validating

Everything the last several handoffs already listed under this heading is
still open (Telegram delivery, a real process kill + `run resume`, a real
`bypass_5h_with_credits` run, `deploy/cosmo-run.service`/`cosmo-notify.
service` installed for real, Open Item 2's timeout retuning, `REVIEWING`'s
timeout/duration data) — nothing this session touched any of that. Add to
the list, specific to this session's own work:

- **`resume_at=COMMITTING`/`MERGING` has never been exercised through a
  real `cosmo run`** — only against `FakeHarnessAdapter`/`FakeGate`. The
  next time a task blocks at either stage for real, confirm `queue retry`
  actually resumes in place (watch for `resuming directly at
  <stage> -- ...` in its output, and confirm no new harness session starts).
- **The repeat-block guard has never actually refused a real retry** —
  only synthetic `task_failures` rows in tests. Worth deliberately
  observing the first time it fires for real (does the reported history
  read clearly enough to act on, or does it need adjusting).
- **The Docker `--user` fix has been verified by hand against each image
  individually, not through one full real gate run (build+unit+e2e
  together) with the new flags** — the one real run that reached
  `VALIDATING` this session predates the fix. Worth confirming the next
  real `VALIDATING` pass produces zero root-owned files anywhere under the
  worktree, not just that each image works in isolation.
- **How `/home/dev/delta/cosmo-tests/todo-frontend-app`'s `.agent/claude/
  CLAUDE.md` went uncommitted** (finding #7 above) is still genuinely
  unknown. If a *base target repo* (not a task worktree) turns up dirty
  again after a `cosmo run`, that's a real bug to chase, not something to
  paper over with another one-off commit.

## Read these first, in this order

| Document | What it is | How to treat it |
|---|---|---|
| [v3-cosmo-autonomous-agent-spec.md](v3-cosmo-autonomous-agent-spec.md) | The authoritative specification | **Source of truth** for the original 0-10 plan. v1 and v2 are superseded — read them only for history |
| [v3-implementation-plan.md](v3-implementation-plan.md) | 11-phase build plan | The map for Phase 10 (its own section, near the end). **Do not edit** — it's the agreed scope; record decisions in `v3-implementation-state.md` instead |
| [v3-implementation-state.md](v3-implementation-state.md) | What actually exists, plus decisions and gotchas | Read the cumulative deviations table's entries **59-67** in full before doing anything — this session's own real findings. The "Phase 10 — acceptance run (in progress)" prose section below the table predates this session and has not been reconciled with it yet |
| [v4-changes-to-workflow-plan.md](v4-changes-to-workflow-plan.md) | The raw-spec-workflow feature design | Implemented — see its own Status line |
| [v5-improvements-plan.md](v5-improvements-plan.md) | Crash/pause resume, Telegram notifications, `--follow`, live-terminal observability, the quota-bypass flag, harness failure-pattern research (§5) | Implemented, parts 1-4/6-7 plus part 5's Class 1 — see its own Status line |
| [v6-project-template-aware-stuff-plan.md](v6-project-template-aware-stuff-plan.md) | Making the gate/failure-classifier project-template-aware, for stacks beyond Java+Spring/Vite+React | **Not started — design record only.** Needs a real second stack before it's buildable, not opportunistic generalization |

`v1-*` and `v2-*` in this folder are earlier spec drafts, fully superseded.
`simple-template-handoff.md`/`old-agents-skills/` are historical, already
fully consumed.

## Where things are

```
/home/dev/delta/cosmo/          # working branch: develop
├── docs/                       # the six documents above
├── deploy/                     # cosmo-run.service (Phase 9) + cosmo-notify.service (v5), README
├── templates/                  # harness + project templates (source of truth)
│   ├── harness/claude/hooks/     # background_task_guard.py is new this session (deviation 60)
│   └── projects/{_blank,java-spring-react,vite-react-local}/
├── src/cosmo/
│   ├── checks.py, doctor.py, config/, harness/
│   ├── bootstrap/                # cosmo init: openspec/docs/symlinks/git-identity/git-branch
│   ├── watchdog.py, retention.py
│   ├── git/{merge,worktree,secrets}.py
│   │   └── worktree.py             # reset_worktree_to_commit gains a docker_bin param + real
│   │                                cleanup fallback this session (deviation 64)
│   ├── gate/                     # Phase 6: the Docker validation gate
│   │   └── docker_runner.py        # container_flags gains --user/HOME this session (deviation 63)
│   ├── task/                     # Phase 7/v4: the per-task state machine
│   │   └── machine.py              # run_task gains resume_at this session (deviation 66)
│   ├── spec/                     # v4: *-task.md frontmatter parsing
│   ├── knowledge/                # Phase 7: spec 11's COMMITTING-step guardrails
│   ├── run/                      # Phase 8/9: run-level state machine, DAG, breaker, quota, cost
│   │   ├── loop.py                 # threads resume_at from task.resume_at_stage into run_task
│   │   ├── recovery.py             # v5: acquire_run_lock/RunLock, reconcile_interrupted_tasks
│   │   └── quota.py                # v5: QuotaDecision gains bypassed: bool
│   ├── notify/                   # v5: Sink protocol, TelegramSink, cosmo notify watch's loop
│   ├── store/                    # SQLite schema, StoreWriter, reader queries
│   │   ├── migrations.py            # 9 migrations now; migrate() toggles PRAGMA foreign_keys
│   │   │                              around each one this session (deviation 59)
│   │   ├── failure_signature.py     # gains detect_repeat_block/RepeatBlock + 2 signatures
│   │   │                              this session (deviation 65)
│   │   ├── writer.py                # gains queue_resume_at; queue_transition now clears
│   │   │                              resume_at_stage unconditionally (deviation 66)
│   │   └── enums.py                 # v5: StopReason.CRASHED
│   ├── events/                   # v5: EventEmitter gains an optional on_emit hook
│   ├── proc/                     # ManagedProcess, WallClockTimer/StallTimer/LivenessTimers, orphan sweep
│   └── cli/main.py               # queue_retry gains --force + the repeat-block guard + the
│                                    resume_at_stage branch (deviations 65/66); run_cmd's
│                                    single-task path reuses an existing worktree (deviation 62)
├── tests/                       # 493 passing + 9 opt-in real-Docker/real-openspec
│   └── fixtures/gate_repo/        # real Spring Boot + Vite/React fixture -- its own frontend/
│                                    package.json still pins Playwright 1.50.0, now mismatched
│                                    with the new default (deviation 67); flagged, not fixed --
│                                    only affects the opt-in real-Docker suite
└── check.sh                     # ruff + format + mypy --strict + pytest
```

## Get oriented (2 minutes)

```bash
cd /home/dev/delta/cosmo
git log --oneline           # this session's Phase 10 fix-up commit should be at HEAD
git branch --show-current   # should say develop
./check.sh                  # must be green before you change anything
cosmo doctor                # core checks + harness checks in two tables
```

**Known, pre-existing environment noise on this host** (not something a
prior phase broke, don't chase it): `cosmo doctor` may show `disk space:
FAIL` — this WSL2 box runs close to the 10 GB floor at the *test* data path
it checks (`/tmp` is a small tmpfs on this box); the real filesystem has
hundreds of GB free. It may also show `event/state store: schema at version
N, this build expects 9` if this shell's own `XDG_DATA_HOME` sandbox
predates migration 9 — harmless, self-resolving the next time any command
opens a `StoreWriter` (migrations are additive and applied automatically;
this is exactly the class of bug deviation 59 above fixed for a *real,
populated* database specifically). This box still has no *global* git
identity (only this repo's own local config has one); `cosmo init` against
a real target repo seeds one automatically. `gitleaks` is on PATH, `docker`
works, and so is the real `openspec` CLI.

**This host's WSL2 genuinely has systemd enabled** and it's still not
actually been exercised on this host — unchanged from prior handoffs, see
`deploy/README.md` before installing either unit.

**One real environment gotcha remains from early phases**: **`npm install`
can hang indefinitely on this host if a previous run was killed
mid-install** (fix: verified-clean `rm -rf node_modules package-lock.json`
first, not waiting longer) — this session's deviation 63 (gate containers
running as root) turned out to be a major contributor to *why* stale
`node_modules` kept accumulating in the first place, but the underlying
"npm install can be slow/flaky on this host" observation still stands on
its own.

One more: **this session's shell may have `XDG_DATA_HOME=/tmp/cosmo-test/data`
set**, sandboxing `cosmo`'s own runtime state away from the real home
directory and from the acceptance run's own real store. `uv run cosmo ...`
is the more reliable invocation for anything scripted. To inspect/drive the
*real* acceptance-run store, unset both `XDG_DATA_HOME` and `COSMO_CONFIG`
explicitly (`env -u XDG_DATA_HOME -u COSMO_CONFIG cosmo ...`) rather than
assuming the default env is already clean — verify which data path you're
actually hitting before trusting what you see.

**Worth knowing before touching the acceptance run or an overnight retry:**

- `cosmo events tail --payload`/`--follow`, `cosmo report --follow`, and
  `cosmo queue failures <task-id>` are your tools for post-run review — not
  raw sqlite queries. `cosmo report` only ever shows the *last run with a
  `run_state` row* — a single-task `cosmo run --task <id>` invocation has
  `run_id=None` by design (Phase 7's "no run tracking" posture) and never
  gets one, so after driving a task through `cosmo run --task`, query
  `events`/`task_failures` directly filtered by `task_id` and a recent
  timestamp instead of trusting `cosmo report`'s output — found by hand
  this session, more than once.
- **`WatchdogSec` in the shipped unit is 10800s (3h), task-boundary
  granularity, not task-internal** — unchanged from prior handoffs.
- **The circuit breaker's tally and quota heuristic's consecutive-failure
  count still live in-memory inside one `run.loop.run_queue`/
  `_run_queue_locked` call** — unchanged.
- **Quota heuristic and secondary-signal config values are still
  unverified guesses** — unchanged.
- **`review.enabled`/`timeouts.reviewing_wall` are equally unverified
  guesses** — mostly still true, though this session's one real
  `VALIDATING`-passing run also passed `REVIEWING` for real (fast, well
  under `reviewing_wall`) before the implementation was later discarded by
  the pre-`resume_at` retry path (finding #7/#8 above) — not enough data
  points to retune from, but the mechanism itself is now confirmed to work
  end-to-end at least once.

## Conventions this codebase follows

- **Python 3.12+, `uv`-managed.** Add dependencies with `uv add`, not by hand.
- **`mypy --strict` passes.** Annotate everything, including test helpers.
- **Comments explain *why*, never *what*.** Existing comments cite the spec
  section that forced the decision. Match that.
- **Config over constants.** Every tunable goes in `config/model.py` and
  `config/defaults.toml`, annotated with its spec section. No magic numbers.
  This session's `retries.repeat_block_threshold` follows the same rule.
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
  hand, then use the real thing already proved out" already proved out.**
  `FakeHarnessAdapter` and `FakeGate` are the two test doubles later phases
  should target directly — this session's `resume_at` tests
  (`test_task_machine.py`) assert `adapter.calls == []`/`gate.calls == []`
  as the proof that a resumed stage genuinely skipped the harness/gate, a
  pattern worth reusing. Real-process/real-Docker/real-`openspec` tests
  exist but are skip-guarded (`COSMO_GATE_DOCKER_E2E=1`) because they take
  real time or need a real binary on PATH.
- **Boundary tests are load-bearing, not optional.** `test_harness_boundary.py`,
  `test_store_boundary.py`, `test_git_boundary.py`, `test_gate_boundary.py`.
- **Run `./check.sh` before committing.** All four must pass.
- **When something fails, check with a real invocation before trusting a
  unit test's green.** Every single deviation in this session's own list
  above was found this way, not by code review or by writing a test first
  — the tests came after, to lock the real finding in.

## When you finish (whatever "finish" means for the next session)

1. `./check.sh` green (if any code changed at all).
2. If Phase 10's own acceptance run genuinely completes end to end (a full
   night's run with a coherent `run.summary`, Open Item 2's timeout
   retuning closed with real data), update
   `v3-implementation-state.md`'s "Phase 10 — acceptance run (in progress)"
   section for real — it still describes the state from *before* this
   session's fixes, not after. If it's still in progress, at least
   reconcile that section with entries 59-67 so a future reader isn't
   working from a stale narrative.
3. Record any new deviation in the cumulative table (next number is **68**).
4. Commit to `develop` with a message explaining *why*, in the style of the
   existing commit history.
