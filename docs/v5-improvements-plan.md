# Cosmo — v5 Improvements: crash resumption, pause/resume, and real-time observability

## Status

**Implemented**, parts 1-4, 6, and 7, plus part 5's Class 1 (the
`failure_signature` taxonomy). See
[v3-implementation-state.md](v3-implementation-state.md)'s "v5 improvements
plan — Implemented" section for what actually got built, every real
decision/ordering constraint found along the way (most notably: the
startup reconciliation sweep has to run *after* the run row exists, not
"immediately alongside `sweep_stale_worktrees`" as this document's own
prose originally suggested — a real foreign-key constraint on
`task_failures.run_id`/`task_transitions.run_id` makes the original
ordering fail), and every new spec deviation (50-57). This document is kept
as the original design record — read it for the *why* behind the shape of
the thing; read the state doc for what's real.

**Part 5's Class 2 remains exactly as open as it was written here** —
the session-management-tool audit beyond the one diagnosed
`ScheduleWakeup`/`ToolSearch`/`TaskOutput` instance (already resolved and
shipped *before* this implementation pass, as deviation 49) was never this
implementation pass's job and is not done. Real-invocation verification
this document's own "Verification" section calls for — a real Telegram
send, a real `kill -9` mid-task, a real `bypass_5h_with_credits` run against
an account with usage credits — is also not done; see the state doc's own
"Real invocations this session" for exactly what *was* checked by hand
instead, and why those three specifically were not.

Written the same way
[v4-changes-to-workflow-plan.md](v4-changes-to-workflow-plan.md) was before
its own build started: grounded in the real code as it exists today, with
file:line citations, not a restatement of the original spec's prose.

## Context

This isn't new scope invention. All four areas below are threads the
original spec always intended to come back to — it names them explicitly:

- **§12 Non-Goals (v1)**, item 1: *"Telegram or any real-time notification
  channel."*
- **§12 Non-Goals (v1)**, item 6: *"Resuming partial in-flight harness work
  after a crash."*
- **§12 "Recorded for later, deliberately deferred"**, item 3: *"Partial
  mid-state resumption. `--resume` with the persisted `session_id`,
  combined with OpenSpec's resume-from-first-unchecked-task behavior, would
  avoid restarting long applies from scratch. `session_id` is already
  captured (§2.2) so this needs no schema change."*
- **§3.1**: *"`PAUSED`: circuit breaker tripped, or quota window exhausted
  — process stays alive, resumes automatically (quota) or manually
  (circuit breaker) after review."*
- **§3.2 "Recovery"**: *"No mid-state resumption: if the process dies while
  a harness is running (`IMPLEMENTING`/`VALIDATING`), that state restarts
  from scratch. This remains the v1 posture. It is a known cost, not an
  inevitability."*

The last two are promises the current code does not actually keep — not
"not yet built," but silently different from what the spec says happens.
That gap, found by hand this session (see below), is the real trigger for
writing this now rather than later.

## What actually happens today, verified against the real code

### A. A task interrupted mid-flight is not "restarted from scratch" — it's lost forever

`run.dag.resolve_execution_order` only ever considers tasks whose
`status == "queued"` (`src/cosmo/run/dag.py:89`, `remaining = {t.task_id:
t for t in tasks if t.status == "queued"}`). Every other status —
`PROPOSING`, `PROPOSED`, `IMPLEMENTING`, `VALIDATING`, `REVIEWING`,
`COMMITTING`, `MERGING`, `FINISHING`, `FAILED_RETRY` — is invisible to the
scheduler, permanently, once written.

`git.worktree.sweep_stale_worktrees` (`src/cosmo/git/worktree.py:175-213`)
runs at the start of every `cosmo run` and does clean up the *worktree
directory* left behind by a crash — its own docstring says so explicitly:
*"a worktree left mid-task by a crash (spec 3.2: 'no mid-state resumption',
so that task restarts from a fresh worktree next time it runs) — is
pruned."* But it only ever calls `remove_worktree`; it never touches
`task_queue.status`. So the promise in its own comment — *"restarts from a
fresh worktree next time it runs"* — is currently false. The task doesn't
restart. It just silently stops existing, from the scheduler's point of
view, forever. Its row is still in `task_queue`, permanently stuck, and
nothing about `cosmo report`/`cosmo queue ls` distinguishes it from a task
that's healthily mid-flight in a run that's still going.

**This isn't hypothetical.** `deploy/cosmo-run.service` already has
`WatchdogSec=10800` paired with `Restart=on-failure` /
`RestartPreventExitStatus=1` (lines 38, 50-51) — a real, deployed,
documented self-healing mechanism: a hang gets systemd-killed (a signal,
not a clean exit, so the restart exclusion doesn't apply) and the unit
restarts automatically. Every single time that fires while a task is
mid-state, that task is the orphan described above. The exact mechanism
meant to make Cosmo resilient is what triggers this gap.

### B. There is no way to resume a `PAUSED` run — and one of the two pause paths doesn't even keep the process alive

Quota-triggered pause (`_handle_quota_pause_or_stop`,
`src/cosmo/run/loop.py:374-419`) does what §3.1 promises: it transitions to
`PAUSED`, blocks on `sleep(decision.resume_delay_seconds)` (line 413) —
which can be hours — then transitions back to `RUNNING` and continues, all
within the same process. This part works, with one caveat: it shares gap
A's exposure. A kill during that multi-hour sleep is a crash like any
other.

Circuit-breaker-triggered pause is different. `run_queue` sets
`RunStatus.PAUSED` and then `break`s out of its own loop entirely
(`src/cosmo/run/loop.py:283-293`); a few lines later, a comment confirms
this is deliberate: *"a breaker trip requires manual intervention (spec
6.5), so this loop simply ends rather than transitioning further"*
(lines 331-334). The whole `cosmo run` **process exits**. This directly
contradicts §3.1's "process stays alive... resumes... manually" — in the
real code, nothing stays alive, and there is no code path anywhere that
resumes a `PAUSED` run. `run_id = uuid.uuid4().hex` is generated fresh at
the top of every `run_queue()` call (`loop.py:89`); there's no concept of
continuing an existing one.

The circuit breaker itself already anticipates a restart, and already says
this is fine: it's deliberately in-memory, and its own module docstring
states *"a tripped breaker's `PAUSED` state is what a restart needs to see
(the persisted `run_state` row, spec 3.1) — resuming requires manual
intervention regardless, so losing this object's in-memory tally on a
restart costs nothing real"* (`src/cosmo/run/breaker.py:14-17`). The
missing piece is purely the CLI entry point and `run_queue`'s ability to
re-attach to an existing `run_id` instead of minting a new one.

### C. No notification channel exists at all

Named explicitly as a v1 non-goal (§12, item 1). No module exists today.
The closest real precedent in the codebase is `watchdog.py`'s `sd_notify`
integration (55 lines total): stdlib-only (no dependency added "for one
socket write"), silent no-op when unconfigured, and explicitly best-effort
— its own docstring: *"A stale/misconfigured `$NOTIFY_SOCKET` must never
take the run down with it — this is a best-effort liveness signal, not a
correctness dependency"* (`watchdog.py:48-52`). Any notification mechanism
should hold itself to the same standard.

The full event taxonomy this would key off already exists
(`src/cosmo/events/envelope.py:22-49`) — `RUN_PAUSED`, `RUN_STOPPED`,
`RUN_SUMMARY`, `RUN_COST_WARNING`, `TASK_BLOCKED`, `TASK_FAILED`, etc. No
new event types are needed for a first cut except the one this plan adds
in part 1 (`TASK_INTERRUPTED`).

### D. Real-time monitoring: one good piece exists (as of tonight), and it's deliberately narrow

The most recent commit on this branch (`b923c00`, same evening as this
plan) added a live `on_activity` callback: one short line per tool call,
threaded from the harness adapter through `task.machine`/`run.loop` to
`cli.main._print_activity`, which prints it to the terminal `cosmo run` is
attached to. Its own docstring is explicit about the boundary: *"Purely a
live foreground terminal cue, never written to the events DB (spec 4's
event log stays as sparse as it already is)"* (`src/cosmo/cli/main.py:93-99`).
That's a deliberate, reasoned decision — this plan should extend around it,
not fight it, by keeping the durable/remote view at the coarser
state-transition granularity the `events` table already uses, not the
fine-grained tool-call chatter.

Beyond that: `cosmo events tail` exists but is a one-shot snapshot
(`--limit N`, `src/cosmo/cli/main.py:1303`), no follow mode. There is no
way to see what Cosmo is doing right now unless you are physically watching
the one terminal that started `cosmo run` — a second terminal, an `ssh`
session from a phone, or a notification channel all currently see nothing
until the run ends.

## What changes

### 1. Startup reconciliation for interrupted tasks (`run.recovery`)

New module `src/cosmo/run/recovery.py`, new function
`reconcile_interrupted_tasks(*, db_path, writer, emitter, run_id)`, called
from `run_queue()` immediately alongside the existing
`sweep_stale_worktrees` call (`run/loop.py:107-112`) — same *"nothing is
running at startup, by definition"* reasoning that call already documents
in its own docstring, extended from worktree directories to `task_queue`
rows.

- Finds every task whose status is one of `PROPOSING`, `PROPOSED`,
  `IMPLEMENTING`, `VALIDATING`, `REVIEWING`, `COMMITTING`, `MERGING`,
  `FINISHING`, `FAILED_RETRY` (every `TaskStatus` value except `QUEUED`,
  `DONE`, `BLOCKED` — `src/cosmo/store/enums.py:14-32`).
- For each: record a `task_failures` row classified `FailureType.
  ENVIRONMENT_ERROR`, `error_summary = "process crashed or was killed
  while <state>"`. This mirrors an already-established rule elsewhere in
  this codebase (state doc deviation 31): a crash/timeout/malformed
  outcome is `environment_error` and must never consume the code-level
  retry budget the way a genuine code failure does. Bounded the same way
  `VALIDATING` timeouts already are (deviation 19) — via
  `retries.max_attempts` through the existing environment-error-weight
  machinery already in `run/loop.py`, not a new ceiling.
- Clears `worktree_path` (the directory is already gone via the existing
  sweep) and resets `status` back to `QUEUED` via the existing
  `queue_transition` — the same *"in-flight task returns to `QUEUED`"*
  behavior §3.3's own timeout table already promises for a clean
  `max_time` stop, now also applied to an unclean crash.
- New `EventType.TASK_INTERRUPTED` (`task.interrupted`), severity
  `WARNING`, emitted once per reconciled task — visible immediately to
  `cosmo events tail`, feeds the Telegram sink in part 3 for free, and
  gives `cosmo report` something concrete to show ("N task(s) recovered
  from an interrupted run").
- Also reconciles the run level: any `run_state` row still `RUNNING` at
  startup is — under Cosmo's own strictly-serial, single-process design
  (spec 5) — only possible if its owning process died. Transition it to
  `STOPPED` with a new `StopReason.CRASHED`, so `cosmo report`'s history
  doesn't carry a run that looks eternally in-progress.

**Decided: a simple pidfile lock ships as part of this same pass, not a
separate follow-up.** Nothing today prevents two `cosmo run` invocations
racing against the same repo/queue concurrently. It's cheap, directly
related to this part's own "what does startup safety mean" theme, and low
risk to add alongside the reconciliation sweep rather than deferring —
deferring it would mean shipping crash-recovery machinery in the same
release that leaves its own precondition (only one `run_queue()` active at
a time) unenforced. Shape: `config.paths.data_dir / "cosmo-run.lock"`,
written with the PID at the very start of `run_queue()` (and by
`cosmo run resume`, part 2) and removed on clean exit; a stale lock (PID no
longer alive, checked via `os.kill(pid, 0)`) is reclaimed automatically —
same "stale is not sacred" posture `sweep_stale_worktrees` already applies
to worktrees — a live lock refuses to start with a clear error rather than
silently racing.

### 2. `cosmo run resume [run_id]`

New Typer command, built entirely from existing primitives. **Decided:**
`run_id` is optional — bare `cosmo run resume` resolves to the most
recently started `PAUSED` run, the same default convention `cosmo report`'s
own `--run` option already uses; passing `run_id` explicitly stays
available and is required only when more than one run is genuinely
`PAUSED` at once (rare under the single-lock design in part 1, but not
impossible if a run was paused, a lock reclaim happened, and a second run
was separately paused before the first was resumed).

- Resolve the target run: the given `run_id`, or (when omitted) the most
  recent row in `run_state` with `status = 'paused'`, ordered by
  `updated_at`; error clearly if none exists. Look it up via
  `get_run(db_path, run_id)` (`src/cosmo/store/reader.py:261`); refuse with
  a clear message if its status isn't `PAUSED`.
- Render the same pause-reason/cost/blocked-task context `cosmo report`
  already builds — reuse that rendering, don't reimplement it — so the
  human has the "review" §3.1 already promises before resuming.
- `typer.confirm` before proceeding by default, skippable via `--yes` (for
  a scripted or, eventually, Telegram-reply-triggered resume) — same
  interactive-by-default pattern already established for `cosmo init`'s
  git-identity prompt and this session's own `cosmo spec add` idempotency
  fix.
- `run_queue()` gains an optional `resume_run_id: str | None = None`
  parameter: when given, skip minting a fresh `uuid.uuid4().hex`
  (`loop.py:89`) and reuse it instead. `get_run_cost(db_path, run_id)`
  (`store/reader.py:301`) already sums by `run_id` from persisted state,
  so cost-ceiling accounting picks back up correctly with no extra
  bookkeeping — this falls out of the existing schema for free.
- Runs part 1's reconciliation sweep on the way in regardless of *why* the
  run paused — cheap, idempotent, and also defends against the process
  having been killed while paused-and-sleeping (gap A applies there too).
- `run_transition(run_id, RunStatus.RUNNING)`
  (`store/writer.py:332-363`), then proceeds exactly like a normal
  `run_queue()` invocation.

**Decided:** a resumed run gets a **fresh** `timeouts.run_wall` budget
(seconds, despite the name — `defaults.toml`'s `run_wall = 36000`,
`loop.py:139`) starting from the moment of resume, not an accounting of
time spent paused — matches "the operator explicitly chose to continue this session
now," and avoids extra bookkeeping (would paused/sleeping time count
against it too?) for limited real benefit.

### 3. Notifications (`cosmo.notify`)

**Decided:** built behind a generic `Sink` protocol from the start, not
Telegram hardcoded — mirrors this codebase's own established
harness-adapter/gate-stage shape (a small ABC/protocol plus exactly one
real implementation today), cheap to keep generic now, avoids a reshape
later if a second channel (a webhook, Slack) shows up.

New package `src/cosmo/notify/`, shaped like `harness/`: a `Sink` protocol
(`send(event: Event) -> None`) plus one concrete implementation,
`notify/telegram.py`'s `TelegramSink`, using the Bot API's `sendMessage`
over stdlib `urllib` — no new dependency, same "not worth a dependency for
one HTTP call" reasoning `watchdog.py` already uses for `sd_notify`.

- New config section `[notify]` (`config/model.py` + `defaults.toml`):
  `enabled: bool = false`, `telegram_bot_token: str | None`,
  `telegram_chat_id: str | None`, `min_severity: Severity = WARNING`. Only
  `WARNING`+ events reach a phone by default (`RUN_PAUSED`, `TASK_BLOCKED`,
  `TASK_INTERRUPTED` from part 1, `RUN_COST_WARNING`), plus `RUN_SUMMARY`/
  `RUN_STOPPED` explicitly allow-listed regardless of severity — a run
  ending is always notification-worthy even though it's emitted at `INFO`
  today.
- **Delivery must not live inside the run-loop process — this is the one
  substantive design call this plan makes, not just a description of a
  gap.** A Telegram send inline in `EventEmitter.emit()` is the obvious
  first idea, and it's wrong: it cannot notify about the run loop's *own*
  crash, since whatever would send that message dies with the process. So
  instead: a small, separate, always-on watcher — a new CLI command
  `cosmo notify watch`. **Decided:** ships its own systemd unit,
  `deploy/cosmo-notify.service`, in this same pass (mirrors
  `cosmo-run.service`; near-zero extra cost since the watcher code is
  being built anyway, and it keeps `deploy/` ready for real overnight use
  immediately rather than needing a second pass later). The watcher polls
  the `events` table (`list_events`, the same read `cosmo events tail` already
  does) from its own connection. Safe under the schema's already-standing
  `PRAGMA journal_mode = WAL` (`src/cosmo/store/connection.py:22`) for a
  second concurrent reader. The watcher's own view of "no new run activity
  for an unexpectedly long time" is itself a real crash-detection signal
  that part 1's reconciliation can't give you until the *next* `cosmo run`
  starts — the watcher can raise it immediately.
- **Decided — the staleness threshold itself:** new `[notify]` field
  `stale_after_seconds: int = 1800`. Chosen against the real cadence
  already observed in the live `events` table this session — `task.
  heartbeat` rows land far more often than every 30 minutes whenever a task
  is genuinely active (source `mtime`, tied to `[progress].
  poll_interval_seconds = 7`), so 1800s of total silence in a run that
  hasn't reached a terminal `run_state` status is already anomalous, not a
  guess tuned to nothing. This has to be a message the watcher constructs
  itself, not a row read from `events` — if the run process is truly dead,
  nothing new is ever written for the watcher to find, so "the table has
  been silent for `stale_after_seconds`" is the signal, not a payload field
  on some event that will never arrive.
- This decoupled design also directly provides the polling primitive part
  4 needs — one mechanism serves both, not two.

### 4. `cosmo events tail --follow`, and durable real-time monitoring generally

- Extends the existing `events_tail` command (`cli/main.py:1303`) with a
  `--follow` flag: after the initial `--limit` rows, keep polling for rows
  past the last-seen `seq` and print each as it lands, `tail -f`-style —
  same safe-concurrent-WAL-read part 3's watcher already needs.
- Deliberately the coarse, persisted event stream (state transitions,
  pauses, blocks) — **not** a duplicate of the fine-grained, ephemeral
  `on_activity` per-tool-call feed, which stays exactly as ephemeral and
  single-terminal as it was designed to be (see part D above — that
  boundary is deliberate, this plan should not blur it).
- Gives a second terminal, a remote `ssh` session, or (later, outside this
  plan) a thin viewer a live picture of a run in progress without needing
  to be the one terminal that started it. A web dashboard itself stays a
  named non-goal (§12) — not proposed here.
- `cosmo report --follow` (poll until the run reaches a terminal status)
  is a natural, cheap analog worth adding alongside.

### 5. Harness failure-pattern research — new, added after real diagnoses

Not part of the original four problem areas above. While debugging the
crash-resumption/pause gaps for real (a live `scaffold-app` task, one
target repo, across several real runs the same night), two *classes* of
recurring failure surfaced by hand, each with enough repetition already to
be a pattern rather than a one-off. Both got a point fix already (deviations
44-48 in `docs/v3-implementation-state.md`), but neither fix is provably
*complete* — this section is the research/hardening work worth doing next,
not a re-description of what already shipped.

**Class 1: `error_summary` is too coarse to tell failures apart without
reading raw npm/build output by hand every time.** The real `task_failures`
history for this one task (5 rows total, queried directly from the live DB)
has exactly two `error_summary` values ever recorded for a build failure:
`"frontend build failed"` (4 of 5 rows) and `"error_max_turns"` (1 row, an
`environment_error`, unrelated). Of those 4 build failures, **3 were the
identical missing-`package-lock.json` `npm ci` error** (attempts 0 and 1 in
the first real run, *and* attempt 3 in a later run — recurring even after
`CLAUDE.md`'s "commit the real lockfile" guidance was already in place, see
deviation 42/48) and 1 was the Node/Vite version mismatch (deviation 41,
since fixed). Nothing in `error_summary` — the only field `cosmo events
tail`/`cosmo report` show without a targeted `cosmo queue failures <id>`
lookup — distinguishes these. A human (or Cosmo itself) has no way to ask
"how many of our build failures are actually the same root cause" without
reading every `error_detail` blob by hand, which is exactly how both of
tonight's real root causes got found. Worth doing next: a lightweight,
structural (not prose-parsed — spec 4's rule applies) sub-classification of
common `code_error @ build` signatures — "no package-lock.json", the
`EBADENGINE`/native-binding shape deviation 41 hit, `ENOENT` on
`node_modules`, etc. — surfaced as a real field on the failure record or
event payload, not left buried in free-text `error_detail`.

**Decided — the schema shape:** a new nullable `task_failures.
failure_signature: str | None` column (migration 7), populated by a small,
deterministic classifier — string/substring matching against
`error_detail`, no model call, no prose-parsing (matches spec 4's own
posture of preferring structured signals — `session_id`, `total_cost_usd`,
the `system/api_retry` shape — over parsing free text). Starting taxonomy,
deliberately small rather than exhaustive: `missing_lockfile` (the
`npm ci`/no-`package-lock.json` shape hit 3 of tonight's 5 real rows),
`node_engine_mismatch` (deviation 41's shape), `enoent_node_modules`; anything
unmatched stays `None` rather than forcing a guess. Surfaced everywhere
`error_summary` already is — `cosmo events tail --payload`, `cosmo report`,
`cosmo queue failures <id>` — so "how many of our failures are actually the
same root cause" becomes a real query instead of a manual `error_detail`
read.

**Class 2: a harness session that starts real background work, then ends
its own turn assuming a resumption that a one-shot `claude -p` call never
provides.** Deviation 48's fix (a new CLAUDE.md section) targets the one
diagnosed instance — `ScheduleWakeup` — by naming it explicitly and
explaining why it does nothing here. That's necessary but not sufficient:
prose guidance has already been shown not to reliably prevent a recurring
mistake once in this exact session (Class 1's own history — the lockfile
guidance was added, then the identical failure recurred on the very next
real attempt, for the *unrelated* reason of the session ending mid-install
rather than forgetting to commit).

**A permission-based block is not the settled fix this section originally
assumed — checked for real this session, not shipped on the obvious
guess.** The originally proposed fix was denying `ScheduleWakeup` via
`permissions.deny`. Two things now argue against committing to that as-is:

- **Direct evidence from tonight's own failed attempt 3.** Its actual
  invocation carried `--allowedTools Write Edit Bash` — an *allow-list*
  restricted to exactly three tools — yet its own activity trace shows it
  calling `ScheduleWakeup`, `ToolSearch`, and `TaskOutput` anyway, none of
  which are in that list. So `--allowedTools` did not gate those calls at
  all in the one real case we have.
- **Research into Claude Code's own documented behavior** (not fully
  authoritative — based on community-reported issues, not the primary
  permissions docs, so treat the specifics as directional rather than
  certain) points the same way: `--allowedTools` is additive on top of a
  built-in default tool set that `ScheduleWakeup`/`ToolSearch`/`TaskOutput`
  and similar session-management tools belong to, and there is no
  documented, supported flag to make an allow-list exclusive. Whether
  `permissions.deny` (settings.json) shares this gap or enforces
  differently is explicitly *not* settled by the docs either way.

**Decided:** don't adopt `permissions.deny` as the fix on the strength of a
plausible mental model alone — this codebase's own rule is "check by hand,
then trust it," and this is exactly a case where the obvious guess already
failed once (`--allowedTools`, same shape of mechanism). Before writing this
into a real change: run a cheap, real, throwaway `claude -p` session with
`permissions.deny: ["ScheduleWakeup"]` set and something that would trigger
it, and observe whether the call is actually blocked. If it is, add it
alongside the existing CLAUDE.md prose as defense-in-depth, not instead of
it. If it isn't, stop trying to prevent the call and say so explicitly in
`CLAUDE.md` rather than continuing to imply a fix exists that doesn't.

**Decided — the actual proven safety net stays primary, and gets watched,
not re-guessed.** What genuinely recovered from tonight's real instance of
this failure was `implementing_stall`'s existing 1200s wall-clock timer —
it worked correctly, the first time it was ever tested for real, costing
about 20 real minutes before the task was auto-requeued. That number isn't
being changed here on the strength of a single data point; it's exactly the
kind of thing Phase 10's overnight acceptance run (this document's own
motivating context) is positioned to confirm or retune with more real
samples — folding into the plan's pre-existing Open Item 2 (§3.3 timeout
retuning) rather than inventing a second, competing timeout-tuning thread.

### 6. `cosmo run`'s own live terminal shows tool-call chatter but no state transitions — new, added after a real gap this session

Not one of the original four problem areas either, and distinct from part D/4's
gap (a *second*, unattached terminal/session seeing nothing until it polls).
This is about the *one* terminal already running `cosmo run` and being
watched live by a human — it turns out that terminal doesn't show the thing
that actually matters most, either.

Real reproduction, same night, same `scaffold-app` task: while a live `cosmo
run` sat in the operator's terminal, two real, consequential things happened
that produced *zero* visible output there. (1) `implementing_stall`'s 1200s
timer correctly killed a wedged attempt and auto-requeued the task
(`task_transitions` ids 38-39: `implementing → failed_retry → queued`). (2)
Minutes later the same run hit a real, `confirmed: true` `quota_exhausted_5h`
pause (`events` row #226: `resume_delay_seconds: 8716.04`) and is, right now,
sitting in a plain `time.sleep()` inside `_handle_quota_pause_or_stop`
(`run/loop.py:410`) until it wakes itself. Both are exactly the kind of thing
an operator watching the live feed needs to know immediately — and both had
to be reconstructed after the fact from `cosmo events tail --payload` and raw
`task_transitions`/`task_failures` reads, including hand-computing the
resume wall-clock time from a raw seconds float, because nothing printed it.

The reason is simple once traced: the live terminal's only feed is the
`on_activity` callback added in `b923c00` (`cli.main._print_activity`,
`cli/main.py:99-104`), which by its own docstring is deliberately narrow —
one dim line per harness tool call, "never written to the events DB." That
boundary (part D above) is correct and should stay — but it means the
terminal a human is actually staring at subscribes to *none* of the coarse,
already-persisted lifecycle events (`TASK_STATE_CHANGED`, `RUN_PAUSED`,
`RUN_RESUMED`, `TASK_BLOCKED`, `RUN_STOPPED`, `RUN_SUMMARY`) that `cosmo
events tail` can already show — it just never automatically does, in the one
place an operator is already looking.

**Minimal fix, no new infrastructure:** every one of those events already
flows through the single `EventEmitter.emit()` chokepoint
(`events/emitter.py:30`), bound for the run's whole lifetime to one
`StoreWriter`. Give it an optional `on_emit: Callable[[Event], None] | None`
hook, called after the DB insert succeeds (mirrors `on_activity`'s own
"presentation is a CLI concern, the emitter itself stays ignorant of it"
shape). `cli.main`'s `run`/`queue run` commands wire a sibling to
`_print_activity` that prints one line per `on_emit` call — filtered to
`WARNING`+ severity plus the lifecycle-shaped `INFO` events (`RUN_STARTED`,
`RUN_RESUMED`, `RUN_SUMMARY`), the same severity-based judgment part 3's
`min_severity` already had to make, not a second, differently-drawn line.
`TASK_STATE_CHANGED` only fires on an actual transition, never on
`task.heartbeat` (a separate, much chattier event type already excluded by
filtering on `event_type`), so this doesn't reintroduce the noise `on_
activity` deliberately avoids.

Two presentation details worth getting right, both directly motivated by
this session's real confusion:
- Style these lines visually distinct from `on_activity`'s dim per-tool-call
  chatter (bold, severity-colored) — interleaved in the same stream, not a
  second pane or command, so a human skimming a long scrollback can tell "the
  state actually changed" from "yet another Bash call" at a glance.
- Render `RUN_PAUSED`'s `resume_delay_seconds` as a human wall-clock ETA
  (`resume at 2026-08-27T05:10Z`), computed once at print time from `wall_
  clock_now() + resume_delay_seconds` — not the raw float this session had to
  convert by hand to answer "when will it come back."

This composes with, and doesn't duplicate, parts 3/4: the Telegram sink and
`cosmo events tail --follow` exist for *unattended* or remote observability
(no one physically watching); this is specifically about the attached
terminal a human already has open having the coarse signal too, at the cost
of one new emitter hook and no new table, command, or process.

### 7. An opt-in flag to keep going past a confirmed 5h quota pause via usage credits — new, added after a real test this session

Discovered live during this same night's Phase 10 acceptance run, not one of
the original four problem areas. While `bdf4ab101aee...` sat correctly
`PAUSED` on a real, `confirmed: true` `quota_exhausted_5h` signal (part 6's
own event #226), a manual side-channel `claude -p "hi"` succeeded
immediately — proof the account's Anthropic **usage credits** (an
account-level pay-as-you-go top-up past the subscription plan's included
allowance; distinct from the console/API-key billing `adapter.py` already
scrubs, part-C/`BILLING_ENV_VAR` above) were covering calls despite the
5-hour window still being nominally active. Nothing in `quota.decide()`
today can take advantage of that: a confirmed `five_hour` signal always
means `PAUSED` + a full `sleep(resume_delay_seconds)`, unconditionally,
whether or not the account could actually keep going.

**Why this isn't just "skip the pause in `decide()`":** usage credits
convert what has always been, architecturally, a flat-rate/cost-free
subscription allowance into real, metered per-token spend once the
included allowance runs out — and that is *exactly* the situation this
codebase already has a guard for, just never turned on. `CostConfig`'s own
docstring: *"A ceiling of 0.0 means 'no hard stop' — the posture for a
subscription-billed harness, where section 7.1 usage windows govern
instead"* (`config/model.py:82-84`); `run/cost.py`'s own module docstring:
*"Inert for the v1 subscription-billed Claude adapter... implemented in
full anyway so a future per-token adapter needs no new mechanism, only
non-zero config"* (`run/cost.py:1-7`). In other words: the cost ceiling was
built and tested specifically for the day subscription billing stops being
free, and left disabled because until tonight nothing made that true. A
flag that bypasses the 5h pause recreates precisely the scenario that
ceiling exists for — so it must not ship without also addressing the
ceiling, or the one guard already built for this would stay silently off.

The good news: no new cost-tracking plumbing is needed. The Claude adapter
already reports real `total_cost_usd` on every terminal result regardless
of billing mode (`reports_native_cost=True`, `adapter.py:70,352`), and
`get_run_cost`/`check_run_cost` (`run/cost.py:26-33`) already sum and gate
on it — built and unit-tested since it was written, just never exercised
for real because subscription-only billing reports ~$0 until credits start
covering calls. The missing piece is genuinely only the `decide()` branch
and its config gate, not new accounting.

Proposed shape:

- New `QuotaConfig` field `bypass_5h_with_credits: bool = false` — off by
  default, an explicit opt-in, matching this codebase's consistent posture
  on anything that changes unattended-spend behavior (same posture as
  part 3's `[notify]` `enabled = false` default).
- **Decided — the one substantive design call here:** enabling it
  *requires* `cost.max_cost_per_run_usd > 0` (and ideally
  `max_cost_per_task_usd > 0`) at the same time, validated at config-load
  time in `config/model.py`'s existing `_Strict` validators, refusing to
  start with a clear error otherwise. Don't let the bypass exist without
  its own backstop turned on — the same "don't ship the risky half without
  its safety half" instinct part 3 already applied to keeping Telegram
  delivery out of the crash-prone run process.
- `quota.decide()`: when `signal.window == "five_hour"` and
  `config.bypass_5h_with_credits` is true, skip the `PAUSED` branch
  entirely and return a decision that lets `run_queue` `continue`
  immediately instead of sleeping — no new `RunStatus`/`PauseReason`
  needed. `weekly` is deliberately untouched by this flag: nothing tonight
  tested whether credits ride through a *weekly* cap the same way, and
  `decide()`'s existing "don't guess, actually stop" posture for `weekly`
  shouldn't be loosened without its own real confirmation later.
- Still emit a visible signal even though the run doesn't pause — new
  `EventType.QUOTA_BYPASSED` (`quota.bypassed`), `WARNING` severity,
  payload `{resets_at, run_cost_so_far_usd}` — so part 6's live-terminal
  hook, `cosmo events tail`, and part 3's Telegram sink all still surface
  "spending real money past the included allowance right now." Proceeding
  with zero visible signal would be strictly worse than today's silent
  pause.

**Explicitly not attempted:** detecting whether credits are actually
enabled or have remaining balance. Nothing in the `claude` CLI's
`stream-json` output exposes that (confirmed via the same research that
surfaced the usage-credits docs above) — this stays a human's informed
opt-in ("I have credits, let it ride"), never something cosmo infers. If
credits run out mid-run, calls should start failing for real and ordinary
retry/circuit-breaker/cost-ceiling handling takes over — this itself needs
real-world confirmation once the flag exists, not assumed correct on paper,
matching this whole document's own "check by hand, then trust it" rule.

## What does not change

Mirroring v4's own "what does not need to change" framing: the DAG
scheduler, circuit breaker, and quota logic are untouched (the breaker's
own docstring already says a fresh instance on restart is fine by design);
the gate is untouched; no `HarnessAdapter` ABC changes are needed at all —
`on_activity` and `session_id` already exist and already carry everything
this plan's later, optional session-resume stretch goal would need;
`task.machine.run_task`'s state functions are untouched — reconciliation
acts only at startup, before any `task_queue` row is ever picked up, never
mid-`run_task`.

## Explicitly out of scope for this plan

- **True session-level resume** (§12's own deferred item 3: actually
  resuming a killed `IMPLEMENTING`/`VALIDATING` call via Claude Code's
  `--resume <session_id>` instead of restarting the whole task from
  scratch). `session_id` is already captured and persisted, so nothing
  blocks building this later, but part 1's fresh-restart-via-`QUEUED` is
  the honest v1 fix for "a task is lost forever," which is the more urgent
  correctness bug; true session resume is a quota/cost optimization on top
  of that, not a prerequisite.
- A web dashboard (§12 non-goal, unchanged).
- Parallel task execution (§12 non-goal, unrelated to this plan).

## Decisions (all resolved before implementation)

Every judgment call this plan raised has been made explicitly, not left
implicit in whichever way was easiest to code:

1. **`cosmo run resume` defaults to the most recent `PAUSED` run** when no
   `run_id` is given, matching `cosmo report`'s own `--run` default
   convention; an explicit `run_id` stays available and is required only
   when more than one run is genuinely `PAUSED` at once.
2. **A resumed run gets a fresh wall-clock budget** from the moment of
   resume, not an accounting of time spent paused.
3. **Notifications are built behind a generic `Sink` protocol from day
   one**, with Telegram as the only concrete implementation shipped —
   matches this codebase's own harness-adapter/gate-stage pattern.
4. **`deploy/cosmo-notify.service` ships in this same pass**, alongside
   `cosmo-run.service`, rather than staying a manually-started process.
5. **A simple pidfile lock ships as part of part 1**, not a separate
   follow-up — see part 1's own "Decided" note above for the shape.
6. **The live `cosmo run` terminal gets a second, coarse-grained print hook
   (`on_emit`) alongside the existing fine-grained `on_activity`**, rather
   than either replacing `on_activity` or requiring a second terminal/
   `--follow` session to see state transitions and pauses — see part 6.
7. **`bypass_5h_with_credits` requires a non-zero `cost.max_cost_per_run_usd`
   at config-load time, refused otherwise** — the bypass must not exist
   without the spend ceiling it recreates the need for; see part 7.
8. **`cosmo notify watch`'s staleness threshold is `[notify].
   stale_after_seconds = 1800`**, self-constructed by the watcher rather
   than read from an `events` row — see part 3's own "Decided" note.

## Verification (once implemented)

Same discipline every prior phase in this project has followed — fake/unit
coverage is necessary but not sufficient:

- Fake-adapter, fake-clock unit tests for the mechanics: script a
  `FakeHarnessAdapter` task left mid-`IMPLEMENTING` in the DB (no real
  process crash needed to set this up), assert `reconcile_interrupted_
  tasks` requeues it correctly, emits `TASK_INTERRUPTED`, and doesn't
  touch the code-level retry budget.
- **Real invocations, not just mocked green**, before calling any of this
  done: actually `kill -9` a real `cosmo run` process mid-task against a
  real target repo and confirm the *next* `cosmo run` picks the task back
  up instead of losing it; a real `cosmo run resume` against a real
  circuit-breaker-tripped `PAUSED` run; a real Telegram bot token/chat id
  receiving a real message end to end, including one sent by `cosmo
  notify watch` *after* the run-loop process it's watching was killed.
- **Part 6, real invocation:** watch a real `cosmo run` terminal through an
  actual `implementing_stall`/circuit-breaker/quota-pause event and confirm
  the coarse line actually prints, is visually distinct from `on_activity`
  chatter, and (for `RUN_PAUSED`) renders a correct human wall-clock ETA —
  not just a unit test asserting `on_emit` was called with the right `Event`.
- **Part 7, real invocation:** with `bypass_5h_with_credits=true` and a real
  usage-credits-covered account, confirm a real confirmed `five_hour` signal
  does *not* pause the run, `quota.bypassed` is emitted and visible in both
  `cosmo events tail` and part 6's live terminal, and — separately — confirm
  config load actually refuses to start when the flag is `true` but
  `cost.max_cost_per_run_usd` is left at `0`.
- **Part 5 has no verification step here on purpose** — it isn't a finished
  spec yet (see "Status" above); verification gets written alongside
  whatever concrete design comes out of its own follow-up decision pass.
