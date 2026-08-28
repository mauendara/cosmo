# Handoff — three real cross-project/observability bugs fixed against a second real target repo (deviations 74-76); v6 still deferred

You are picking up Cosmo mid-build. **Phases 0-9, the v4 workflow-changes
feature, the v5 improvements plan, Phase 10's own acceptance-run, and v7
items 1+3 (the prior handoff) are all implemented.** **This session's own
work: three real bugs (deviations 74-76), all found and fixed live driving
a real `cosmo run` against `habits-frontend-app`** — the first time this
store has ever had *two* real target repos in it at once (the existing
`todo-frontend-app` acceptance-run repo, plus this new one) — read
[v3-implementation-state.md](v3-implementation-state.md)'s cumulative
deviations table, entries **74-76**, before doing anything else; this
document summarizes them, the table has the precise file:line-grounded
detail.

**Deviation 74 — cross-project `task_id` collision.** `task_queue.task_id`
is a single global primary key shared by *every* project's `cosmo.db`, but
`templates/harness/claude/skills/spec-enrichment/SKILL.md` only ever
promised a task_id "unique within this spec." `habits-frontend-app`'s
`habit-tracker` spec batch picked `task_id: scaffold-app` for its scaffold
task — the exact id `todo-frontend-app`'s batch had already used and
finished. Two real failures resulted: `habit-date-lib`/`habit-types-and-
persistence`'s `depends_on: [scaffold-app]` looked satisfied by the *other*
project's `done` row, even though `habits-frontend-app` was never
scaffolded; and `cli.main.spec_queue`'s batch-insert loop hard-exited on
the first collision it hit, silently dropping every task alphabetically
after it — confirmed live across three separate `cosmo spec queue`
invocations before the cause was found. Fixed: `spec_queue` now namespaces
every task_id/`depends_on` edge in a batch (`f"{name}-{task_id}"`) before
the cycle check and insert, `_render_spec_preview` shows the namespaced
ids so the preview matches what actually gets queued, and a rerun on an
already-(partially-)queued batch is now a clean no-op instead of a hard
exit.

**Deviation 75 — `VALIDATING` printed nothing to the live terminal, pass
or fail.** Same class of gap as deviation 68 (`TASK_STATE_CHANGED`), found
the same way: a user watching a real `cosmo run` saw the gate run for tens
of seconds with zero visible output. A *passing* `task.validation_result`
is `severity=info` and wasn't in `_print_emit`'s allowlist at all (dropped
silently); a *failing* one cleared the severity filter but had no `detail`
case of its own, printing as a bare `>> task.validation_result`. Fixed:
added to the allowlist, plus a new `_validation_result_detail` summarizing
`passed=…, unit=pass/FAIL (Np/Nf/Ns), e2e=pass/FAIL (…)` and pointing at
`cosmo queue failures <task_id>` on failure (the real `error_summary`/
`error_detail` deliberately stay out of this event's payload per spec 9.2).

**Deviation 76 — `openspec archive` failed on every single task in the
batch.** `task.machine._do_finishing`'s own docstring already documented
the assumption that a propose session names its `openspec new change`
`Path(spec_path).stem` — but nothing ever told the propose session that;
`openspec-workflow/SKILL.md` only said "use a short kebab-case name."
Confirmed live: every task in the real `habit-tracker` batch fired
`TASK_FINISHING_FAILED` (`Change 'scaffold-app-task' not found. Available
changes: scaffold-app`, same shape for every task after it) because the
propose session reasonably stripped the task file's own `-task` suffix
instead of matching the assumed convention verbatim. Fixed at the actual
source of the mismatch, not by trying to recover the real name after the
fact: `_do_proposing` now threads `spec_id` into `adapter.propose(...)`'s
context, and `ClaudeCodeAdapter.propose` pins the exact required change
name into the prompt.

**All three found and fixed in one continuous live session**, not from a
design doc — `habits-frontend-app`'s real `habit-tracker` spec batch (9
tasks) was driven through a real `cosmo run` end to end while these were
found; by the end of this session 4 of its 9 tasks were `done` (`habit-
tracker-scaffold-app`, `habit-date-lib`, `habit-types-and-persistence`,
`habit-streak-lib`), with `use-habits-hook` deliberately left `blocked`
(reason `environment`, no real failure — a human asked for the run to stop
cleanly there) so a *fresh* `cosmo run` would actually pick up deviations
75-76 for the remaining 5 tasks (this run's own long-lived process had
already imported the old code before the fix landed on disk, confirmed by
comparing the process start time against the file mtimes — editable
installs only help a *new* process/import, not one already running).
`docs/handoff.md`/`v3-implementation-state.md` are this repo's own; nothing
in `habits-frontend-app` was touched except its own repo-local `docs/
specs/habit-tracker-spec/tasks/*.md` (renamed the colliding task_id) and
manually re-running `openspec archive` for the three tasks that finished
`PROPOSING` before deviation 76 landed — both real, necessary interventions
in that repo, not part of this repo's own change.

## What happened in the prior session (v7 items 1+3 implemented; v6 deliberately deferred)

Kept as-is below for its own detail — the summary above already covers
*this* session's own work (deviations 74-76).

**What changed, in one paragraph**: `run.loop.run_queue` used to report
`StopReason.QUEUE_EMPTY` (green output, exit code 0, treated as success)
for two different situations — a genuinely finished/empty queue, and every
remaining task being stuck `BLOCKED` with a real, un-actioned failure. The
Phase 10 acceptance run's own timing data showed this was the dominant cost
in that run (`scaffold-app` alone spent 10h15m of its 19h37m total sitting
`queued`/`blocked` with nobody noticing) — see v7's own "Context" section
for the full breakdown. Item 1 fixes the observability gap: a new
`StopReason.BLOCKED_REMAINING` (migration 10) is chosen instead whenever
`summary.blocked_by_reason` is non-empty, which — with no further CLI
change needed — already yields yellow styling and a nonzero exit code
(`cli.main._RUN_SUCCESSFUL_STOP_REASONS` simply excludes it). Item 3 closes
one specific, bounded case of the underlying stuck-ness: a task blocked on
`blocked_reason=cost` can now only ever legitimately clear by a human
raising `max_cost_per_task_usd` between runs (the stored cost never goes
down) — `run.recovery.requeue_cost_blocked_tasks`, called unconditionally
at `run_queue` startup alongside the existing `reconcile_interrupted_tasks`,
re-evaluates every such task against the *current* config and clears the
ones no longer over ceiling, preserving `attempt_count`/`worktree_path`
since nothing about the task itself failed.

**v7 item 2 is now also done, later in this same session** — the user
supplied a real Telegram bot token (`@CosmoNotifyTelegramBot`) and, once
they messaged it once (bots can't message first), a real chat id was
pulled from `getUpdates`. Both now live in `~/.config/cosmo/config.toml`
(`chmod 600`, outside the repo, never committed) under `[notify]`. Verified
for real, not just configured: a `TelegramSink.send` call got a real
`"ok":true` back from the Telegram API; `cosmo notify watch` starts clean
against the real store with no refusal; the installed
`~/.config/systemd/user/cosmo-notify.service` (stale from the prior
session, predating deviation 69's `[Service]`→`[Unit]` fix for
`StartLimitIntervalSec`/`StartLimitBurst`) was patched to match the repo's
own `deploy/cosmo-notify.service` and is now `enabled`+`active (running)`
via `systemctl --user`. Only item 4 remains open — a spec-authoring
question for the *next* batch, not code, and now partly answered: the
scheduler (`run.dag.resolve_execution_order` + `run.loop.run_queue`'s main
loop) already interleaves independent branches correctly when a task
blocks, since it recomputes the full eligible set every iteration, not just
one task ahead — `todo-frontend-app`'s own spec batch never exercised this
for real only because its chain had no independent branch to begin with.
The one real exception (still open, not settled): a circuit-breaker trip
pauses the *whole* run, independent branches included, by design (spec
6.5) — see v7's own item 4 note for the full reasoning.

**One more real fix this session (deviation 72), found by the user
hand-testing a project template for v6 prep**: `cli.main.spec_add`'s
"no raw spec, no `--from`" error branch now creates `docs/specs/` before
telling the user to write a file there — it didn't before, so "write it
there directly" pointed at a directory that didn't exist. True of every
project template equally (`docs/specs/` is deliberately not part of any
template's own `docs/` — it's spec-batch content, not stack boilerplate),
not specific to the template the user happened to be testing.

**A second real fix this session (deviation 73)**: `cosmo spec add`
printed `harness: ...` then went completely silent until it finished, timed
out, or failed — no visibility into what the harness was actually doing.
`HarnessAdapter.probe`'s own `on_activity` hook already exists for exactly
this (the same mechanism `cosmo run`'s live terminal already uses
elsewhere), `spec_add` just never passed it. `cosmo harness probe` had the
identical gap (same copy-pasted probe+timeout pattern) — fixed both, now
both pass `on_activity=cli.main._print_activity`.

**v6 ([v6-project-template-aware-stuff-plan.md](v6-project-template-aware-stuff-plan.md))
was explicitly asked about this session and deliberately not started** —
its own Status line already says it needs a real second stack (a
Python/FastAPI or plain Node/Express backend, or similar) to prove the
abstraction before it's buildable, and the user confirmed: they'll do that
second-stack testing themselves, then come back to it. Don't start v6
opportunistically; wait for that.

## What happened in the prior session (Phase 10 acceptance run)

Kept as-is below for its own detail — the summary above already covers
*this* session's own work (v7 items 1+3). This section, "Where the
acceptance run actually stands right now", and "What still needs
validating" all describe state as of the *end of the acceptance-run
session*, one session before this handoff's own top summary.

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
| [v3-implementation-state.md](v3-implementation-state.md) | What actually exists, plus decisions and gotchas | Read the cumulative deviations table's entries **74-76** in full before doing anything — this session's own real findings, driving a real `cosmo run` against a second real target repo (`habits-frontend-app`) |
| [v4-changes-to-workflow-plan.md](v4-changes-to-workflow-plan.md) | The raw-spec-workflow feature design | Implemented — see its own Status line |
| [v5-improvements-plan.md](v5-improvements-plan.md) | Crash/pause resume, Telegram notifications, `--follow`, live-terminal observability, the quota-bypass flag, harness failure-pattern research (§5) | Implemented, parts 1-4/6-7 plus part 5's Class 1 — see its own Status line |
| [v6-project-template-aware-stuff-plan.md](v6-project-template-aware-stuff-plan.md) | Making the gate/failure-classifier project-template-aware, for stacks beyond Java+Spring/Vite+React | **Not started — design record only.** Needs a real second stack before it's buildable; the user is doing that testing themselves before this gets picked up again — don't start it opportunistically |
| [v7-complete-queue-done-fixes-plan.md](v7-complete-queue-done-fixes-plan.md) | Closing the "queue_empty looks like done" gap found auditing the Phase 10 acceptance run's own timing data | **Items 1, 2, and 3 done this session** (deviation 71 + a same-session Telegram follow-up) — see its own Status line. Only item 4 (a spec-authoring question, not code) remains open |

`v1-*` and `v2-*` in this folder are earlier spec drafts, fully superseded.
`simple-template-handoff.md`/`old-agents-skills/` are historical, already
fully consumed.

## Where things are

```
/home/dev/delta/cosmo/          # working branch: develop
├── docs/                       # handoff.md + v3-implementation-state.md updated this session;
│                                  no new plan doc added
├── deploy/                     # unchanged this session
├── templates/
│   └── harness/claude/skills/
│       ├── spec-enrichment/SKILL.md       # unchanged -- still only promises task_id unique
│       │                                     *within* a spec; deviation 74 fixes this at the
│       │                                     insertion layer instead, deliberately (an
│       │                                     enrichment-prompt convention isn't enforceable)
│       └── openspec-workflow/SKILL.md     # unchanged -- still just says "short kebab-case
│                                             name"; deviation 76 fixes the mismatch at the
│                                             propose-prompt layer instead, same reasoning
├── src/cosmo/
│   ├── checks.py, doctor.py, config/, bootstrap/, watchdog.py, retention.py, git/, gate/,
│   │   spec/, run/, store/, events/                    # all unchanged this session
│   ├── task/machine.py              # `_do_proposing` now threads `spec_id` into
│   │                                    `adapter.propose(...)`'s context dict (76)
│   ├── harness/claude/adapter.py     # `propose()` reads `context["spec_id"]` and pins the
│   │                                    exact required `openspec new change` name into the
│   │                                    prompt, falling back to `spec_path.stem` (76)
│   └── cli/main.py                  # new `_namespace_batch` (74); `spec_queue` namespaces
│                                        every id/`depends_on` edge before the cycle check and
│                                        insert, and is now idempotent against its own prior
│                                        partial runs (74); `_render_spec_preview` shows
│                                        namespaced ids (74); `_print_emit` gains
│                                        `TASK_VALIDATION_RESULT` in its allowlist plus a new
│                                        `_validation_result_detail` (75)
├── tests/                       # 524 passing (up from 514), 9 skipped
│   ├── test_cli_spec.py             # 4 existing tests' assertions updated (they'd coincided
│   │                                   with already-namespaced-looking ids by chance, not by
│   │                                   any real rule); 3 new tests for deviation 74
│   │                                   (cross-project reuse, external depends_on left bare,
│   │                                   rerun-is-a-no-op)
│   ├── test_cli.py                  # 2 new `_print_emit` tests for deviation 75 (passing
│   │                                   and failing validation results)
│   └── test_harness_claude_adapter.py  # 2 new `propose()` prompt tests for deviation 76
│                                          (pinned name, and the fallback with no `spec_id`)
└── check.sh                     # ruff + format + mypy --strict + pytest -- all green
```

## Get oriented (2 minutes)

```bash
cd /home/dev/delta/cosmo
git log --oneline           # this session's deviations 74-76 commit should be at HEAD
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

**This host's WSL2 genuinely has systemd enabled** (real `systemctl --user`
units, `systemd 259`). A `cosmo-run.service` user unit is still `enabled`
(last real run: exited cleanly, `queue_empty`, against `todo-frontend-app`,
4.5h before this session started — no active timer re-triggering it found
this session, but it will start again on the next login/boot). Re-verified
this session: `acquire_run_lock` is **one `cosmo run` at a time per
`data_dir`, not per project** — a single lock file
(`~/.local/share/cosmo/cosmo-run.lock`) shared by *every* target repo. Now
that a second real project (`habits-frontend-app`) exists in this store,
that service starting against `todo-frontend-app` at the wrong moment would
refuse (or be refused by) a manual `cosmo run` against `habits-frontend-app`
with `RunLockHeldError` — not a bug, just something to actually check
(`systemctl --user status cosmo-run.service`) before assuming a lock
conflict is anything else. `cosmo-notify.service` remains deliberately
stopped and disabled (restart-loops forever without Telegram config, which
this host doesn't have) — leave it disabled until real credentials exist.

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
2. Record any new deviation in the cumulative table (next number is **77**).
3. If Phase 10's own acceptance run against `todo-frontend-app` is still
   fully `done` and nothing regressed it, there is no more Phase 10
   backlog left to reconcile — a fresh spec batch queued against it is new
   work, not a continuation. `habits-frontend-app`'s `habit-tracker` batch
   is a separate, real, still-in-flight run (4/9 tasks done, `use-habits-
   hook` deliberately `blocked` pending a fresh `cosmo run`) — not this
   repo's own backlog, but worth checking `cosmo queue ls`/`cosmo report`
   against the real store if you want to know whether deviations 75-76
   actually held up cleanly for its remaining 5 tasks.
4. Commit to `develop` with a message explaining *why*, in the style of the
   existing commit history.
