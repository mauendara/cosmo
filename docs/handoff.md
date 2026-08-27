# Handoff — Phase 10 acceptance run complete; three more real bugs found and fixed closing out the open-items list

You are picking up Cosmo mid-build. **Phases 0-9, the v4 workflow-changes
feature, the v5 improvements plan, and Phase 10's own acceptance-run prep
(deviations 59-67, the prior handoff) are all implemented.** **This
session's own work: the Phase 10 acceptance run against
`/home/dev/delta/cosmo-tests/todo-frontend-app` reached full completion for
the first time** (all six queued tasks — `scaffold-app`, `todo-data-model`,
`use-local-storage-hook`, `use-todos-hook`, `todo-ui`, `todo-e2e` — reached
`done`), and then the user asked to deliberately work through the previous
handoff's own "what still needs validating" list item by item. Doing both
found and fixed three more real bugs (deviations 68-70) — read
[v3-implementation-state.md](v3-implementation-state.md)'s cumulative
deviations table, entries **68-70**, before doing anything else; this
document summarizes them, the table has the precise file:line-grounded
detail. The table's own "Phase 10 — acceptance run" section has been
reconciled with this session's work already — that housekeeping step is
done, not left for you.

## What actually happened this session

**Part 1 — a live gap reported by the user.** The user started a `cosmo
run` and reported, watching it live: no visible task id, no visible task
state, no visible timestamp of the last state change — only harness
tool-call chatter. Root cause (deviation 68): the v5 plan's own live-
terminal feature (`cli.main._print_emit`) was supposed to surface
`TASK_STATE_CHANGED` in the one terminal an operator already has open, but
its `_EMIT_LIFECYCLE_INFO_TYPES` allowlist never actually included it —
implemented in name only. Fixed, and fixing it live caught a second real
bug: the first patch interpolated `[task_id]` unescaped into a Rich-markup
string, which Rich silently swallows as a bogus style tag (confirmed by
hand: the task id vanished from the printed line). Fixed with
`rich.markup.escape`.

**Part 2 — driving the acceptance run to completion, finding two real
bugs along the way (deviation 69):**

1. `task.machine._do_finishing`'s `openspec archive` step mutates
   `repo_path`'s working tree but never committed the result — every
   completed task left the base repo permanently dirty, which blocked the
   *next* task's `MERGING` immediately (`todo-data-model` blocked this way
   right after `scaffold-app` finished). Fixed: `_do_finishing` now commits
   the archive's own output.
2. Reproducing last session's still-unexplained finding #7 (`.agent/
   claude/CLAUDE.md` found uncommitted) in a fresh scratch repo turned up
   something more fundamental: **`cosmo init` never committed anything it
   wrote, ever** — `openspec/`, `docs/`, `.agent/<harness>/`, and every
   root symlink sat untracked from the moment `cosmo init` returned. The
   very first task ever queued against a freshly-initialized repo hit the
   exact same `MERGING` refusal, before any task-level bug had a chance to
   dirty anything. Fixed: `cli.main.init` now commits its own bootstrap
   output after `_ensure_git_identity`, skipped only when the tree was
   already dirty *before* Cosmo touched it. A background investigation
   separately traced one real recurrence of the original finding-#7
   instance to `run_init`'s unconditional `sync_harness_assets` re-sync on
   an already-registered repo after the template moved on — this fix
   closes both mechanisms at once, since both leave real, committable
   diffs in the same working tree it now scans.

Both fixes were confirmed clean across 7 more real task completions this
session (5 in the main acceptance run, 2 in scratch-repo verification).

**Part 3 — the user asked to work through the remaining open items one by
one** (repeat-block guard, the finding-#7 mystery — covered above, process-
kill + `run resume`, installing the systemd services for real):

- **Repeat-block guard**: confirmed for real. Seeded 3 realistic
  `error_max_turns`-shaped `task_failures` rows (replaying `scaffold-app`'s
  own real historical pattern, since no task in this session's real queue
  happened to repeat-block on its own) against a throwaway task, then ran
  the real `cosmo queue retry` CLI: refused with the exact formatted
  history, `--force` correctly overrode it. No code changed — this was
  pure validation.
- **Process-kill + `run resume`**: confirmed for the queue-driving `cosmo
  run` path, and found a real, previously-unknown gap in `cosmo run
  --task` (deviation 70) — that path never acquired the process lock or
  ran startup crash reconciliation at all. A real `kill -9` left a task
  stuck outside `queued` forever, with the *next* `cosmo run --task
  <same-id>` refusing outright ("not queued") — a genuine dead end.
  Fixed, and fixing it surfaced a further bug on the very next real
  re-run: reconciliation alone nulls the DB's `worktree_path` but doesn't
  remove the crashed attempt's actual git worktree/branch, so the fresh
  retry collided with the still-existing `task/<spec_id>` branch. Fixed
  with `git.worktree.sweep_stale_worktrees`, called *before*
  reconciliation (ordering matters — sweep reads each task's current,
  still-non-`queued` status). Verified against two consecutive real
  `kill -9`s in a scratch repo.
- **Systemd services installed for real**: true system-wide install needs
  `sudo`, unavailable interactively in this session — installed as
  `systemctl --user` units instead (same real shipped files, real systemd
  259 on this host). `cosmo-run.service` worked correctly (`Type=notify`'s
  `sd_notify` STATUS= string visible in `systemctl status`, correct
  no-restart on a clean `queue_empty` exit). `cosmo-notify.service`
  refused to start exactly as documented (no Telegram config). Along the
  way, `journalctl` caught a real bug in **both** shipped `.service`
  files: `StartLimitIntervalSec`/`StartLimitBurst` were under `[Service]`,
  which systemd 259 silently rejects — they belong in `[Unit]`. Fixed in
  `deploy/cosmo-run.service` and `deploy/cosmo-notify.service`.

**506 tests passing (up from 466 at the start of Phase 10), `./check.sh`
green.** No deviation above required a compromise anywhere in the existing
suite. Every fix above has at least one new regression test; several also
have direct real-invocation confirmation beyond the test suite (see each
deviation's own entry for exactly what was checked by hand).

## Where the acceptance run actually stands right now

**Done.** `cosmo queue ls` against the real store shows all six
`todo-frontend-app` tasks `done`: `scaffold-app` (1/2 attempts),
`todo-data-model` (1/2), `use-local-storage-hook` (2/2 — one real
adversarial-review rejection caught two genuine bugs, a wrong error type
and a state-update-before-persistence-succeeds race), `use-todos-hook`
(1/2), `todo-ui` (1/2), `todo-e2e` (3/2 — its first two attempts submitted
literally no implementation at all, twice, because writing anything under
its own `frontend/e2e/` path was guardrailed and the harness correctly
refused rather than working around it; its final, successful attempt also
found and worked around a real `crypto.randomUUID()` secure-context bug in
the Docker gate's e2e host, documented in the target repo's own
`docs/frontend/architecture.md`). The target repo's git tree is clean —
confirmed by hand, not assumed.

**There is no more Phase 10 acceptance-run backlog against
`todo-frontend-app`.** If a new spec batch or new tasks get queued against
it, they're new work, not a continuation of this phase's own exit
criterion.

## What still needs validating

Real, honest gaps — not fixed this session, and not fixable casually:

- **A real system-wide (`sudo cp .../etc/systemd/system/`) install** of
  both services, as `deploy/README.md` actually documents for production.
  This session's install was `systemctl --user` only, for lack of `sudo`
  access in this environment — a future session (or the user, by hand)
  should confirm the real production path still works, though nothing
  found this session suggests it wouldn't (the unit files themselves are
  now fixed).
- **`REVIEWING`/`VALIDATING` timeout retuning (Open Item 2, §3.3) has real
  data now but hasn't been formally decided.** 8 real `REVIEWING` passes
  this session: 33s-161s, comfortable under the 900s wall. `todo-e2e`'s
  two failing real `VALIDATING` attempts: ~24-25 real minutes each, over
  half the 2700s wall — the first real signal this value might deserve a
  closer look, not proof it's wrong. Retuning is a decision for a human,
  not something to change opportunistically.
- **Telegram delivery is still completely unverified end to end** — no
  bot token/chat id available this session. `cosmo-notify.service`'s
  *refusal* to start without one was confirmed for real instead.
- **A real `cosmo run resume` against a real circuit-breaker-tripped or
  quota-paused run** was never exercised — nothing in this session's real
  queue tripped either condition (contrast with `reconcile_interrupted_
  tasks`, which a real `kill -9` did exercise, twice).
- **A real `bypass_5h_with_credits=true` run** needs a real, deliberate
  5-hour quota exhaustion window to test against — real spend, real
  waiting, not something to force casually.

## Read these first, in this order

| Document | What it is | How to treat it |
|---|---|---|
| [v3-cosmo-autonomous-agent-spec.md](v3-cosmo-autonomous-agent-spec.md) | The authoritative specification | **Source of truth** for the original 0-10 plan. v1 and v2 are superseded — read them only for history |
| [v3-implementation-plan.md](v3-implementation-plan.md) | 11-phase build plan | The map for Phase 10 (its own section, near the end). **Do not edit** — it's the agreed scope; record decisions in `v3-implementation-state.md` instead |
| [v3-implementation-state.md](v3-implementation-state.md) | What actually exists, plus decisions and gotchas | Read the cumulative deviations table's entries **68-70** in full before doing anything — this session's own real findings. The "Phase 10 — acceptance run" section has already been reconciled with this session's completion; no stale narrative left there |
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
├── deploy/                     # cosmo-run.service + cosmo-notify.service, README
│   │                              StartLimitIntervalSec/StartLimitBurst moved
│   │                              [Service] -> [Unit] this session (deviation 69)
├── templates/                  # harness + project templates (source of truth)
│   └── harness/claude/skills/spec-enrichment/SKILL.md  # unchanged this session
├── src/cosmo/
│   ├── checks.py, doctor.py, config/, harness/
│   ├── bootstrap/                # cosmo init: openspec/docs/symlinks/git-identity/git-branch
│   │   ├── git_branch.py           # new `commit_bootstrap_output` this session (deviation 69)
│   │   └── init.py                 # `run_init` itself unchanged -- the commit call lives in
│   │                                  `cli.main.init`, after `_ensure_git_identity`
│   ├── watchdog.py, retention.py
│   ├── git/{merge,worktree,secrets}.py
│   ├── gate/                     # Phase 6: the Docker validation gate (unchanged this session,
│   │                                confirmed clean across many more real runs)
│   ├── task/                     # Phase 7/v4: the per-task state machine
│   │   └── machine.py              # `_do_finishing` gains `_git_commit_archive` this session
│   │                                  (deviation 69); commits `openspec archive`'s own output
│   ├── run/                      # Phase 8/9: run-level state machine, DAG, breaker, quota, cost
│   │   └── recovery.py             # `reconcile_interrupted_tasks`'s `run_id` param is now
│   │                                  `str | None` this session (deviation 70)
│   ├── spec/                     # v4: *-task.md frontmatter parsing (unchanged this session)
│   ├── store/                    # SQLite schema, StoreWriter, reader queries (unchanged)
│   └── cli/main.py               # `_print_emit` gains `TASK_STATE_CHANGED` + escaping this
│                                    session (deviation 68); `run_cmd`'s single-`--task` path
│                                    gains lock acquisition + worktree sweep + reconciliation
│                                    before its own status check (deviation 70)
├── tests/                       # 506 passing + 9 opt-in real-Docker/real-openspec
│   ├── test_cli.py                 # `_print_emit` regression tests (deviation 68)
│   ├── test_task_reviewing.py      # `_do_finishing` archive-commit regression test (69)
│   ├── test_bootstrap_git_branch.py  # new file: `commit_bootstrap_output` unit tests (69)
│   ├── test_cli_init.py            # `cosmo init` leaves a clean commit; SKIPPED_DIRTY guard (69)
│   └── test_cli_run.py             # crash-reconciliation-before-status-check, held-lock
│                                      error (70)
└── check.sh                     # ruff + format + mypy --strict + pytest
```

## Get oriented (2 minutes)

```bash
cd /home/dev/delta/cosmo
git log --oneline           # this session's Phase 10 completion commit should be at HEAD
git branch --show-current   # should say develop
./check.sh                  # must be green before you change anything
cosmo doctor                # core checks + harness checks in two tables
```

**Known, pre-existing environment noise on this host** (not something a
prior phase broke, don't chase it): `cosmo doctor` may show `disk space:
FAIL` — this WSL2 box runs close to the 10 GB floor at the *test* data path
it checks (`/tmp` is a small tmpfs on this box); the real filesystem has
hundreds of GB free. This box still has no *global* git identity (only this
repo's own local config has one); `cosmo init` against a real target repo
seeds one automatically. `gitleaks` is on PATH, `docker` works, and so is
the real `openspec` CLI (`1.6.0` this session).

**This host's WSL2 genuinely has systemd enabled**, confirmed working
again this session (real `systemctl --user` units, `systemd 259`). A
`cosmo-run.service` user unit was left `enabled` (harmless — it only runs
`cosmo run` against `todo-frontend-app`, which has nothing queued right
now, so it starts, finds `queue_empty`, and exits cleanly); check `systemctl
--user status cosmo-run.service` if curious. `cosmo-notify.service` was
deliberately stopped and disabled this session (it restart-loops forever
without Telegram config, which this host doesn't have) — leave it disabled
until real credentials exist.

**One real environment gotcha remains from early phases**: **`npm install`
can hang indefinitely on this host if a previous run was killed
mid-install** (fix: verified-clean `rm -rf node_modules package-lock.json`
first, not waiting longer).

**This session's shell may have `XDG_DATA_HOME=/tmp/cosmo-test/data`
set**, sandboxing `cosmo`'s own runtime state away from the real home
directory and from the acceptance run's own real store. `uv run cosmo ...`
is the more reliable invocation for anything scripted. To inspect/drive the
*real* acceptance-run store, unset both `XDG_DATA_HOME` and `COSMO_CONFIG`
explicitly (`env -u XDG_DATA_HOME -u COSMO_CONFIG cosmo ...`) rather than
assuming the default env is already clean — verify which data path you're
actually hitting before trusting what you see. Confirmed again this
session, more than once.

**Worth knowing before touching the real store or queueing new work:**

- `cosmo events tail --payload`/`--follow`, `cosmo report --follow`, and
  `cosmo queue failures <task-id>` are your tools for post-run review — not
  raw sqlite queries. `cosmo report` only ever shows the *last run with a
  `run_state` row* — a single-task `cosmo run --task <id>` invocation has
  `run_id=None` by design (Phase 7's "no run tracking" posture, still true
  after deviation 70's fix -- that fix added crash recovery, not run
  tracking) and never gets one, so after driving a task through `cosmo run
  --task`, query `events`/`task_failures` directly filtered by `task_id`
  instead of trusting `cosmo report`'s output.
- **When seeding or removing rows directly against the real store for a
  real-code-path validation (not a unit test)**, `task_queue` has real
  foreign-key dependents: `task_failures`, `task_transitions`, `events`
  (via `task_failures.event_id`, not `events.task_id` itself),
  `task_progress`, `task_heartbeat`, `task_cost`. Delete in that order
  (`task_failures` before `events`, everything before `task_queue` itself)
  and `commit()` once at the end inside one script -- a raw `sqlite3`
  `DELETE` outside of a full, committed transaction rolls back silently on
  any mid-script `IntegrityError`, which looks like success (`rowcount`
  reports correctly per-statement) until you check again and find nothing
  actually changed. Found by hand, twice, this session.
- **`WatchdogSec` in the shipped unit is 10800s (3h), task-boundary
  granularity, not task-internal** — unchanged from prior handoffs.
- **The circuit breaker's tally and quota heuristic's consecutive-failure
  count still live in-memory inside one `run.loop.run_queue`/
  `_run_queue_locked` call** — unchanged.
- **Quota heuristic and secondary-signal config values are still
  unverified guesses** — unchanged.

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
  repo or a real target repo.
- **Fake the external process, test the mechanics — except where "check by
  hand, then use the real thing already proved out" already proved out.**
  `FakeHarnessAdapter` and `FakeGate` are the two test doubles later phases
  should target directly. Real-process/real-Docker/real-`openspec` tests
  exist, most gated behind `which openspec`/`COSMO_GATE_DOCKER_E2E=1`
  skipif guards; this session added several more (`test_bootstrap_git_
  branch.py`, `test_cli_init.py`'s new tests) following the same pattern.
- **Boundary tests are load-bearing, not optional.** `test_harness_boundary.py`,
  `test_store_boundary.py`, `test_git_boundary.py`, `test_gate_boundary.py`.
- **Run `./check.sh` before committing.** All four must pass.
- **When something fails, check with a real invocation before trusting a
  unit test's green.** Every deviation in this session's list above was
  found this way — including two (the worktree-collision bug in deviation
  70, the `[Service]`/`[Unit]` systemd bug in deviation 69) that a first,
  real attempt at validating something *else* surfaced by accident. Real
  invocations don't just confirm what you already suspect; they find
  things you didn't know to test for.
- **When a real invocation needs a throwaway task/repo to exercise a code
  path safely**, build it in a scratch directory (this session used
  `/tmp/.../scratchpad/`), never the real acceptance-run target repo — and
  clean up afterward: remove the git worktree/branch, delete the seeded DB
  rows (see the foreign-key ordering note above), remove the scratch repo
  itself. Verify the real queue/repo are untouched before reporting done.
- **Raw SQL against the live store is a real, deliberate action, not a
  shortcut** — this session's own auto-mode classifier blocked one
  unprompted attempt at it. When there's no CLI-supported way to do
  something (there is currently no `cosmo queue remove <task_id>`), name
  the gap explicitly and ask before reaching for direct DB access, rather
  than treating it as equivalent to a normal CLI command.

## When you finish (whatever "finish" means for the next session)

1. `./check.sh` green (if any code changed at all).
2. Record any new deviation in the cumulative table (next number is **71**).
3. If Phase 10's own acceptance run against `todo-frontend-app` is still
   fully `done` and nothing regressed it, there is no more Phase 10
   backlog left to reconcile — a fresh spec batch queued against it is new
   work, not a continuation.
4. Commit to `develop` with a message explaining *why*, in the style of the
   existing commit history.
