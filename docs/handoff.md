# Handoff — AI co-author trailers stripped from the entire commit history (all 3 branches); no source code changed this session

You are picking up Cosmo mid-build. **Phases 0-9, the v4 workflow-changes
feature, the v5 improvements plan, Phase 10's own acceptance-run, v7 items
1-3, and deviations 74-79 are all implemented, and all three real target
repos are fully `done`** — see the prior-session sections below for that
detail, unchanged.

**This session's own work was a one-off repo-hygiene task, not a build
session**, driven start-to-finish by a user-authored prompt at
`docs/ignored/prompts/remove-ai-attribution.md` (not by any of the `vN`
plans). Two-phase task, executed exactly as scoped, with an explicit
verify-then-confirm gate between phases:

**Phase 1 (read-only audit)**: scanned `git log --all` across every branch
for real `Co-Authored-By:`/`Co-authored-by:` trailer lines — anchored at
column 0, standard trailer syntax, not a substring grep (the repo has one
commit, `00dce1ceba`, that *discusses* the trailer in a prose sentence
without being one; the scan correctly didn't flag it, and correctly still
flagged that same commit for the real trailer elsewhere in its body).
Found **43 trailers total — 39 Claude (`noreply@anthropic.com`), 4 Cursor
(`cursoragent@cursor.com`)** — spread across all three branches
(`develop`: 39 reachable, `webapp`: 33 reachable including 4 Cursor-only
ones from its own independent commits, `master`: 1, its root commit). No
remote configured, no reflog/tracking-branch evidence of any prior push —
user confirmed this repo has never been pushed. `git-filter-repo` was not
yet installed.

**Phase 2A (forward-looking fix), done**: added `~/.claude/CLAUDE.md`
(user-level, global — didn't exist before) with the literal no-AI-trailer
rule from the prompt, and this repo's own root `CLAUDE.md` (also new)
pointing at `CONTRIBUTING.md`'s existing "Commits and AI attribution"
section rather than duplicating its text — that section already documented
the same policy in prose before this session.

**Phase 2B (history rewrite), done**: `git-filter-repo` installed via `uv
tool install git-filter-repo` (unset `XDG_DATA_HOME`/`COSMO_CONFIG` first —
same gotcha as the `cosmo` tool itself, see the environment-gotchas section
below). Before rewriting: found `webapp` was checked out in a **separate
worktree** (`/home/dev/delta/cosmo-webapp`) — `git filter-repo` refuses to
run with another worktree checked out, so it was removed (`git worktree
remove`, worktree was clean; some gitignored `.venv`/`.mypy_cache`/
`.ruff_cache` leftovers stayed on disk at the old path, harmless, not
git-tracked). A message-callback (anchored-trailer removal, same regex as
the Phase 1 scan, plus a `Generated with Claude Code`-style footer pattern
per the prompt even though none were found) rewrote every commit's message
across `develop`/`master`/`webapp` — **no tags exist, no file content
touched, verified by re-running the Phase 1 scan against the new history:
zero remaining matches**, plus a clean `git fsck --full`. Commit counts are
unchanged per branch (44/1/40) — only hashes and messages changed.

**One real mistake this session, disclosed to the user rather than glossed
over**: three `backup-*` branches were created before the rewrite as a
safety net, but `git filter-repo` processes *all* refs by default — the
backups got rewritten too, and filter-repo's own cleanup (`reflog
expire`+`gc --prune=now`) then pruned the original objects entirely
(`git cat-file` on the old hashes now fails, `git fsck --unreachable` finds
nothing). No actual content was lost — the callback only ever touched
commit messages, trees/blobs are identical, and `.git/filter-repo/
commit-map` still records every old-hash→new-hash pairing — but the
`backup-*` branches themselves are **not real backups**: they currently
point at the exact same commits as `develop`/`master`/`webapp`, so don't
mistake their existence for a rollback path. **The user has their own
directory-level copy of the whole project from before this session**,
which is the actual backup of record; the mis-executed branches were left
in place (not deleted) rather than have this session take another
destructive action without being asked — worth deleting them (`git branch
-D backup-before-ai-attribution-cleanup backup-master-before-ai-
attribution-cleanup backup-webapp-before-ai-attribution-cleanup`) once
confirmed no longer needed, since as they stand they're just confusing
aliases.

**Phase 2C (push) was explicitly declined by the user this session — not
done, not attempted.** No remote is configured. If a remote gets added and
a push is ever wanted, it needs `--force-with-lease` (not plain `--force`)
and its own separate explicit confirmation, per the original prompt's own
ground rules — that hasn't changed.

One commit landed this session (root `CLAUDE.md`, on top of the rewritten
history) — see `git log -1`.

## What happened in the prior session (the public user documentation set: README + `user-docs/` + four root docs; no code changed)

Kept as-is below for its own detail — the summary above already covers
*this* session's own work (the AI-attribution audit and history rewrite).
Note that **every commit hash referenced by name below has changed** as a
result of this session's `git filter-repo` rewrite; treat hashes in the
sections below as historical pointers to *what was true when written*, not
as `git show`-able references any more — the subjects/content are
unaffected and still accurate.

**This session's own work was documentation-only, not code**, and it was
*outward*-facing documentation for the first time: everything in `docs/`
until now has been internal (specs, plans, state, handoffs). This session
produced the **public, open-source user documentation** the repo would ship
with, per the brief in
`docs/ignored/prompts/user-doc-completion.md` — sixteen files in a Diátaxis
layout:

```
README.md          rewritten in place (not discarded -- kept what was accurate)
user-docs/
  tutorial.md
  how-to/          setup-vps, setup-wsl2, configure-quotas,
                   add-project-template, write-a-new-adapter
  reference/       cli, config-schema, event-schema
  concepts/        architecture-overview, validation-gate-and-guardrails,
                   quota-and-safety-model
FAQ.md  TROUBLESHOOTING.md  CONTRIBUTING.md  SECURITY.md
```

**Everything in them is grounded against the code, not the specs.** The
brief was explicit that the internal `vN` specs are background research only
— a source of facts to translate, never text to copy — and that no command,
flag, config key or metric may be invented. So: every command and flag came
from real `cosmo <cmd> --help` output; every config key from
`config/model.py` plus `defaults.toml`; every event payload from its actual
emit site; and the terminal transcripts in the README and tutorial from real
`cosmo init`/`doctor`/`queue`/`run --dry-run`/`events tail` invocations
against a throwaway repo. Internal spec language (`must-fix`, `decided`,
phase numbers, deviation numbers, changelog tables) does not appear in any
user-facing file.

**Six discrepancies between the brief and the code are recorded in
[v10-user-docs-discrepancies.md](v10-user-docs-discrepancies.md)** — the
brief required flagging a mismatch rather than silently reconciling it.
Read that document before touching any of the six. Two of them are genuine
loose ends worth a decision (`task.guardrail_tripped` is declared in
`events/envelope.py` but emitted by nothing; gate stage *commands* are
hardcoded even though images and directories are configurable — which is
v6's own territory); the other four are simply reality being narrower than
the brief and need no code change.

The one most likely to bite a *user*: **the diff gate rejects any
modification to an existing test file**, not merely a weakening one (a newly
*added* test file is exempt). That is deliberate — see `diffgate.py`'s own
comment — but it is much blunter than "blocks weakened tests," so it is
stated in three separate user-facing places on purpose.

Nothing about the system itself changed. `./check.sh` was **not** re-run
because no code was touched. **The next step from here is not yet decided** —
this session didn't pick one.

Two small notes for whoever picks this up:

- **No `LICENSE` file exists yet.** The brief put it out of scope, so no
  license is named or implied anywhere; three places say "to be added"
  (`README.md`, `CONTRIBUTING.md`, `SECURITY.md`'s disclosure section). Fix
  all three in the same commit as the license file.
- **Capturing real output inserted two throwaway tasks** (`add-login`,
  `login-tests`) into the scratch store this box's `XDG_DATA_HOME` points at
  (`/tmp/cosmo-test/data/cosmo`). Harmless and ephemeral, but they will show
  in a `cosmo queue ls` run against that store. There is no `queue rm`.

## What happened in the prior session (v8/v9: validation gaps and out-of-scope items extracted into their own tracking docs)

Also documentation-only. The handoff's own "What still needs validating"
section and the various out-of-scope/deferred/open-decision notes scattered
across the spec, `v3-implementation-state.md`, and the later `vN`-plan docs
were pulled into two standalone tracking documents —
[v8-validations-for-later.md](v8-validations-for-later.md) (real-invocation
gaps still owed) and
[v9-out-of-scope-desirables.md](v9-out-of-scope-desirables.md) (everything
declared out of scope, deferred, or still an open design decision). Neither
should be read as a task list to start working through opportunistically.

## What happened in the prior session (deviations 77-79: Telegram notify overhaul, two template gaps closed)

Kept as-is below for its own detail — the summary above already covers
*this* session's own work (the v8/v9 doc extraction). This session also
watched a real `cosmo run` through to completion against the third real
target repo (`pomodoro-frontend-app`) and confirmed `habits-frontend-app`'s
previously in-flight batch finished cleanly too — **all three real target
repos in this store are fully `done`**, no backlog against any of them.

**Deviation 77 — `docs/specs/` stayed absent until the first `spec add`.**
Deviation 72 (prior handoff) only fixed the *lazy* creation path inside
`spec_add`'s own error branch; `cosmo init` itself still never created the
directory. The user re-hit the same empty-directory symptom against a fresh
`vite-react-local` init and confirmed the actual want was proactive creation
at init time. Fixed: `bootstrap.docs.copy_project_docs` now `mkdir`s
`docs/specs/` unconditionally at the end of its own copy loop, for every
project template.

**Deviation 78 — the e2e gate silently wasted a full attempt on two of
`pomodoro-frontend-app`'s five real tasks.** Watching that real `cosmo run`
live: `scaffold-app` and `timer-ui` each burned one full failed attempt on
the *same* class of e2e-stage gate failure before the agent self-corrected
on retry — an unpinned `@playwright/test` resolving newer than the gate's
pinned `v1.49.0-noble` Docker image has browsers for, and a Playwright
reporter that never wrote `playwright-report/results.json` in the first
place (gate: `"playwright produced no report"`, indistinguishable from the
suite never running). Both are template-level gaps, not task-level bugs —
every future `vite-react-local` project would rediscover both by trial and
error on its own first e2e task, the same way `todo-frontend-app`'s Phase 10
`crypto.randomUUID()` workaround worked around a gap nobody had documented
yet. Fixed in `templates/projects/vite-react-local/docs/testing.md`'s E2E
section, alongside the existing `BASE_URL` rule.

**Deviation 79 — Telegram notifications were a bare `json.dumps(payload)`
dump, and setup was entirely manual.** Requested directly by the user after
walking through when notifications actually fire (a genuinely useful
exercise: `task.completed` turned out to be silent at the default
`warning` threshold — only the final `run.summary` ever pinged). New
`events.format.event_detail` is one human-readable-phrase builder per event
type, shared by `cli.main._print_emit` (the live terminal) and
`notify.telegram.format_event` (Telegram) instead of two slowly-drifting
copies; `task.completed` is now promoted to always-notify; new `cosmo
notify config` is a one-shot interactive wizard (prompts for a bot token,
discovers the chat id via `getUpdates`, writes `[notify]` via new
`config.loader.write_user_config_table`, sends one real test message before
declaring success). Rolled out live, not just committed: the real
`~/.config/cosmo/config.toml` now has `min_severity = "info"` per the
user's explicit choice, the `cosmo` uv tool was reinstalled from this
checkout, and `cosmo-notify.service` was restarted onto the new build —
see the environment-gotchas section below for a real snag hit doing that
(`uv tool install` also honors `XDG_DATA_HOME`).

## What happened in the prior session (deviations 74-76: cross-project bugs against a second real target repo)

Kept as-is below for its own detail — the summary above already covers
*this* session's own work (deviations 77-79).

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

**The other two real projects are also fully `done` now**, confirmed against
the real store this session: `habits-frontend-app`'s `habit-tracker` batch
(all 9 tasks, including the 5 that were still pending as of the prior
handoff — deviations 75-76 held up cleanly for all of them) and
`pomodoro-frontend-app`'s `pomodoro-timer` batch (5/5 tasks, watched
through a real `cosmo run` end to end this session, including a real
`quota_exhausted_5h` pause that auto-resumed in-process on schedule with no
manual intervention — see `run.loop._handle_quota_pause_or_stop`'s own
docstring). `cosmo queue ls` against the real store shows all 20 tasks
across all three projects `done`, zero `blocked`. Same caveat as above: any
*new* spec batch queued against either is new work, not a continuation.

## What still needs validating

Moved out to its own tracking document this session:
[v8-validations-for-later.md](v8-validations-for-later.md). Same content
that used to live in this section (system-wide systemd install,
`REVIEWING`/`VALIDATING` timeout retuning, the notify wizard's own
interactive flow, `cosmo run resume` against a real circuit-breaker trip,
a real `bypass_5h_with_credits` run) — extracted so it survives this
document's own session-to-session rewrites and can be updated in place as
each item actually gets a real run. Update v8 directly when one of these
gets validated; don't re-accumulate the list here.

## Out of scope, deferred, and open design decisions

Also moved out this session, to
[v9-out-of-scope-desirables.md](v9-out-of-scope-desirables.md): the spec's
own §12 non-goals (with the one exception — Telegram — that's since shipped
anyway), its "recorded for later" and "open items for follow-up specs"
lists, v6's and v7-item-4's own still-open status, and the real
implementation-time decisions (`HeartbeatSource.STREAM`, container cache
mounts, one-project-per-run) that were previously scattered across
`v3-implementation-state.md`. Read it before assuming something is
missing by oversight rather than by design.

## Read these first, in this order

| Document | What it is | How to treat it |
|---|---|---|
| [v3-cosmo-autonomous-agent-spec.md](v3-cosmo-autonomous-agent-spec.md) | The authoritative specification | **Source of truth** for the original 0-10 plan. v1 and v2 are superseded — read them only for history |
| [v3-implementation-plan.md](v3-implementation-plan.md) | 11-phase build plan | The map for Phase 10 (its own section, near the end). **Do not edit** — it's the agreed scope; record decisions in `v3-implementation-state.md` instead |
| [v3-implementation-state.md](v3-implementation-state.md) | What actually exists, plus decisions and gotchas | Read the cumulative deviations table's entries **77-79** in full before doing anything — the most recent real findings (from the last session that changed code) |
| [v4-changes-to-workflow-plan.md](v4-changes-to-workflow-plan.md) | The raw-spec-workflow feature design | Implemented — see its own Status line |
| [v5-improvements-plan.md](v5-improvements-plan.md) | Crash/pause resume, Telegram notifications, `--follow`, live-terminal observability, the quota-bypass flag, harness failure-pattern research (§5) | Implemented, parts 1-4/6-7 plus part 5's Class 1 — see its own Status line |
| [v6-project-template-aware-stuff-plan.md](v6-project-template-aware-stuff-plan.md) | Making the gate/failure-classifier project-template-aware, for stacks beyond Java+Spring/Vite+React | **Not started — design record only.** Needs a real second stack before it's buildable; the user is doing that testing themselves before this gets picked up again — don't start it opportunistically |
| [v7-complete-queue-done-fixes-plan.md](v7-complete-queue-done-fixes-plan.md) | Closing the "queue_empty looks like done" gap found auditing the Phase 10 acceptance run's own timing data | **Items 1, 2, and 3 done this session** (deviation 71 + a same-session Telegram follow-up) — see its own Status line. Only item 4 (a spec-authoring question, not code) remains open |
| [v8-validations-for-later.md](v8-validations-for-later.md) | Real-invocation validations still owed (system-wide systemd install, timeout retuning, notify wizard, `run resume` vs. a circuit-breaker trip, a real `bypass_5h_with_credits` run) | **Tracking document, not a plan.** Update an entry in place when it gets a real run; this is where "what still needs validating" lives now, not in this handoff |
| [v9-out-of-scope-desirables.md](v9-out-of-scope-desirables.md) | Everything declared out of scope, deferred, or still an open design decision — spec §12's non-goals, later plans' own open items, real implementation-time decisions | **Tracking document, not a plan.** Read before assuming a gap is an oversight rather than a deliberate non-goal |
| [v10-user-docs-discrepancies.md](v10-user-docs-discrepancies.md) | The six places the user-doc brief (or the specs behind it) described Cosmo differently from what the code does, and where each is now flagged in the public docs | **Tracking document, not a plan.** Read before touching `task.guardrail_tripped`, the diff gate's `test_path_modified` rule, or anything that assumes gate stage commands are configurable |

The internal `vN` documents above are **not** the user-facing ones. Public
documentation lives in `README.md`, `user-docs/`, and the four root docs
(`FAQ`, `TROUBLESHOOTING`, `CONTRIBUTING`, `SECURITY`) — written for a
developer who has never seen the project, grounded in the code rather than
these specs. Keep the two sets separate: internal design deliberation
(alternatives considered, rejected options, version-to-version changelogs)
must not leak into the user docs, and a user-doc change that contradicts the
code is a bug in the same way a wrong docstring is.

`v1-*` and `v2-*` in this folder are earlier spec drafts, fully superseded.
`simple-template-handoff.md`/`old-agents-skills/` are historical, already
fully consumed.

## Where things are

```
/home/dev/delta/cosmo/          # working branch: develop
├── README.md                   # REWRITTEN in place -- problem-first opening (what breaks in a
│                                  naive overnight-agent setup), a real terminal transcript,
│                                  4 differentiator bullets, doc index, naming note last
├── FAQ.md                      # new -- real questions, including the ones the code answers
│                                  differently from what a reader would assume
├── TROUBLESHOOTING.md          # new -- organized by symptom, from the failure classification
│                                  and quota logic, translated out of the internal enum names
├── CONTRIBUTING.md             # new -- setup, ./check.sh, the four test-enforced boundaries,
│                                  and the AI-attribution rule (disclose in prose, never as a
│                                  Co-Authored-By/Assisted-by trailer naming a model)
├── SECURITY.md                 # new -- a real threat model, including an explicit
│                                  "what Cosmo does NOT defend against" section
├── user-docs/                  # new tree, 12 files, plain markdown (no MkDocs/Docusaurus
│   ├── tutorial.md                 until there's real adoption to justify it)
│   ├── how-to/                 # setup-vps, setup-wsl2, configure-quotas,
│   │                               add-project-template, write-a-new-adapter
│   ├── reference/              # cli, config-schema, event-schema -- exhaustive and dry,
│   │                               every command/flag/key/event payload
│   └── concepts/               # architecture-overview, validation-gate-and-guardrails
│                                   (the differentiator doc), quota-and-safety-model
├── docs/
│   ├── handoff.md                  # this file, rewritten for this session
│   └── v10-user-docs-discrepancies.md   # NEW -- the six brief-vs-code mismatches
├── deploy/, templates/         # unchanged this session
├── src/cosmo/                  # ENTIRELY unchanged this session -- read for ground truth,
│                                  never edited
├── tests/                      # unchanged, 555 passing / 9 skipped as of the prior session
└── check.sh                    # NOT re-run -- no code was touched
```

**No dependency, config, or schema change this session.** `pyproject.toml`
and `uv.lock` are untouched.

## Get oriented (2 minutes)

```bash
cd /home/dev/delta/cosmo
git log --oneline           # this session's "Add root CLAUDE.md..." commit should be at HEAD --
                             # note the AI-attribution-removal session rewrote every commit hash
                             # via git filter-repo, so don't expect hashes quoted in older parts
                             # of this document to `git show` any more
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
units, `systemd 259`). Both `cosmo-run.service` and `cosmo-notify.service`
are `enabled`. As of this session's end: `cosmo-run.service` is `inactive`
(its last real run finished `queue_empty` against `pomodoro-frontend-app`
this session — nothing re-triggers it until the next login/boot or a manual
`systemctl --user start`, there is no timer). `cosmo-notify.service` is
`active (running)`, real Telegram credentials configured, restarted this
session onto the deviation-79 build (new PID, confirmed clean in the
journal). `acquire_run_lock` is **one `cosmo run` at a time per `data_dir`,
not per project** — a single lock file
(`~/.local/share/cosmo/cosmo-run.lock`) shared by *every* target repo; with
three real projects now in this store, a `cosmo-run.service` auto-start
against one at the wrong moment would refuse (or be refused by) a manual
`cosmo run` against another with `RunLockHeldError` — check `systemctl
--user status cosmo-run.service` before assuming a lock conflict is
anything else.

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

**New this session: `uv tool install` respects `XDG_DATA_HOME` too, not
just `cosmo` itself.** Reinstalling the `cosmo` uv tool from this checkout
with the sandboxed env still set silently installed it to
`/tmp/cosmo-test/data/uv/tools/cosmo` instead of the real
`~/.local/share/uv/tools/cosmo` the installed `cosmo-run.service`/
`cosmo-notify.service` units actually invoke — no error, just the wrong
target, caught by checking the installed binary's mtime before trusting the
install had done anything real. `env -u XDG_DATA_HOME -u COSMO_CONFIG uv
tool install --force <path>` is the reliable form; the same caution applies
to any other `uv tool` invocation against the real installed tool, not just
`cosmo` commands themselves.

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
2. Record any new deviation in the cumulative table (next number is **80**).
3. All three real target repos (`todo-frontend-app`, `habits-frontend-app`,
   `pomodoro-frontend-app`) are fully `done` as of this session's end — 20
   tasks total, zero `blocked`, confirmed against the real store. None of
   that is this repo's own backlog; a fresh spec batch queued against any
   of them is new work, not a continuation. Worth a quick `cosmo queue ls`
   against the real store before trusting this if much time has passed —
   state this specific can drift the moment anyone queues something new.
4. Commit to `develop` with a message explaining *why*, in the style of the
   existing commit history.
5. Keep [v8-validations-for-later.md](v8-validations-for-later.md),
   [v9-out-of-scope-desirables.md](v9-out-of-scope-desirables.md), and
   [v10-user-docs-discrepancies.md](v10-user-docs-discrepancies.md) current
   going forward instead of letting this "What still needs validating" /
   "Out of scope" material re-accumulate directly in this handoff — update
   an entry in place when it's validated or shipped, add new entries to the
   right doc as they're found.
6. **If you changed behavior, check whether the public docs still describe
   it correctly.** `README.md`, `user-docs/`, `FAQ.md`, `TROUBLESHOOTING.md`,
   `CONTRIBUTING.md` and `SECURITY.md` are now part of the repo's contract
   with a reader, and every command, flag, config key and event payload in
   them was verified against the code at the time of writing. A new CLI flag
   needs a row in `user-docs/reference/cli.md`; a new config key needs one in
   `config-schema.md` (and a default, and a validator if a bad value is
   dangerous); a new event needs a payload table in `event-schema.md`. That
   list is also written into `CONTRIBUTING.md` for outside contributors.
7. If one of v10's six discrepancies gets resolved, update its entry there
   *and* the user-facing pages it names — the whole point of that document
   is that the flag and the fix stay connected.
