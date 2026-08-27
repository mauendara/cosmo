# Cosmo — v5 Improvements: crash resumption, pause/resume, and real-time observability

## Status

**Design record, not yet implemented — every open decision resolved
(see "Decisions" below), ready to build against.** Written the same way
[v4-changes-to-workflow-plan.md](v4-changes-to-workflow-plan.md) was before
its own build started: grounded in the real code as it exists today, with
file:line citations, not a restatement of the original spec's prose. Read
[v3-implementation-state.md](v3-implementation-state.md) for what's
actually real before implementing any of this — this document may already
be stale in small ways by the time work starts.

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

**Decided:** a resumed run gets a **fresh** `timeouts.run_wall_clock_hours`
budget starting from the moment of resume, not an accounting of time spent
paused — matches "the operator explicitly chose to continue this session
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
