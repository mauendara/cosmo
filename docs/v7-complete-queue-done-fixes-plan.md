# Cosmo — v7: closing the "queue_empty looks like done" gap found auditing the Phase 10 acceptance run

## Status

**Items 1, 2, and 3 implemented** (deviation 71,
[v3-implementation-state.md](v3-implementation-state.md)'s cumulative
table, plus a later same-session follow-up for item 2) —
`StopReason.BLOCKED_REMAINING` distinguishes a genuinely empty/finished
queue from every remaining task being stuck `BLOCKED` (no longer
green/exit-0 either way); `run.recovery.requeue_cost_blocked_tasks`
re-evaluates `blocked`/`cost` tasks against each fresh `cosmo run`
invocation's own config; and `cosmo notify watch` is now configured with a
real Telegram bot token/chat id (`~/.config/cosmo/config.toml`, outside the
repo) and running for real via `systemctl --user` (`cosmo-notify.service`),
verified with a real `"ok":true` delivery from the Telegram API, not just a
clean refusal. **Item 4 remains open**: whether future spec batches should
be authored with more parallel branches is a question for the *next* spec
batch's authoring, not code. Originally written after a dedicated research
pass (no code changed) over the real event/transition history of the
completed Phase 10 acceptance run against `todo-frontend-app`
(`~/.local/share/cosmo/cosmo.db`), specifically to answer: now that a whole
queue has completed for the first time, what made it take so long, and what
should change before the next one.

## Context: what the real data showed

`scaffold-app` — the root of this spec's dependency chain — took **19h37m
from first `queued` to final `done`**, across **10 separate attempt/run
cycles**. Breaking that elapsed time down by the state each minute was
actually spent in (computed from `task_transitions`, not estimated):

- **208 min (3h28m)** real agent work (`proposing`/`implementing`/
  `validating`/`reviewing`/`committing`/`merging`/`finishing`)
- **349 min (5h49m)** sitting `blocked`, waiting for a human to notice
- **615 min (10h15m)** sitting `queued`, waiting for someone to actually
  start a new `cosmo run`

Every other task in this spec (`todo-data-model`, `use-local-storage-hook`,
`use-todos-hook`, `todo-ui`, `todo-e2e`) depends on `scaffold-app` directly
or transitively, so all five sat `queued` from the moment the batch was
submitted (22:45:19 Aug 26) until `scaffold-app` finally cleared — this
spec's task graph is a straight-line chain with no parallel branches, and
`run_queue` is strictly serial by design (`run/loop.py:1-11`'s own
docstring: *"calls `task.machine.run_task` once per DAG-eligible task, in
strictly serial order (spec 5)"*) — so there was never a second runnable
task to fall back on while `scaffold-app` was stuck. Total queue wall time
end to end: 22h21m, of which well under half was any kind of real work by
any task.

**Two of the ten cycles were genuine, now-fixed Cosmo bugs** (deviation 69,
[v3-implementation-state.md](v3-implementation-state.md)): `_do_finishing`
not committing `openspec archive`'s own output, and `cosmo init` not
committing its bootstrap output — both tripped `MERGING`'s "uncommitted
changes, refusing to merge" guard. Those are closed and out of scope here.
**The other eight cycles were legitimate, working-as-designed blocks**
(failed builds, gitleaks hits, a real 5h quota exhaustion, adversarial
review catching an empty implementation, two `todo-e2e` `VALIDATING` cycles
lost to a Docker-gate `crypto.randomUUID()`/secure-context quirk documented
in the target repo's own `docs/frontend/architecture.md`, and a cost-ceiling
preemptive block on `use-local-storage-hook` that sat unreviewed for ~20
hours). **This document is not about reducing how often tasks legitimately
block or fail** — it's about the time between a legitimate block and a
human finding out, which was the dominant cost in every single one of those
eight cycles.

## The core mechanism, with file:line evidence

**`StopReason.QUEUE_EMPTY` is used for three different situations, and
nothing downstream distinguishes the one that matters.**

`run/dag.py:89`: `resolve_execution_order`'s `remaining` dict is built only
from tasks with `status == "queued"` — a `BLOCKED` task is invisible to it,
not retried, not represented in `order` at all.

`run/loop.py:260-271` (`if not order:`) handles every way `order` can come
back empty identically: genuinely nothing left to do, *or* the only
remaining tasks are `blocked`. The one exception already built is narrower
than it sounds: `summary.stalled_queued_tasks` (set at `loop.py:266-268`,
surfaced to the CLI at `cli/main.py:727-730` and to report payloads at
`cli/main.py:1882`) only covers tasks that are **still `queued`** with an
unmet `depends_on` edge — the comment at `loop.py:263-266` says so
explicitly ("Surfaced separately from `blocked_by_reason` since these tasks
are still `queued`, not `blocked`"). It is empty in exactly the scenario
that mattered in this run: every remaining task's status was `blocked`, not
`queued`. In that case `final_status, stop_reason = RunStatus.STOPPED,
StopReason.QUEUE_EMPTY` (`loop.py:271`) fires with no signal at all that a
block is what ended the run.

`cli/main.py:650` (`_RUN_SUCCESSFUL_STOP_REASONS = frozenset({StopReason.
COMPLETED, StopReason.QUEUE_EMPTY})`) and `cli/main.py:719` both then treat
that exit as successful — green in `cosmo run`'s own console output
(`cli/main.py:719-723`) and in `cosmo report`'s stop-reason coloring
(`cli/main.py:1821-1823`, `"completed"`/`"queue_empty"` both render green).
**Note this is despite `run/loop.py` already having computed
`summary.blocked_by_reason` by this point** (`loop.py:351`, incremented
every time a task actually blocks this run) — the data needed to tell these
two cases apart already exists in the `RunSummary` object at the exact
moment `stop_reason` is chosen; it's just not consulted there.

`deploy/cosmo-run.service:57-58` (`Restart=on-failure` /
`RestartPreventExitStatus=1`) never even reaches the failure-vs-not
decision for this case, because `QUEUE_EMPTY` exits with code 0
(`cli/main.py:719-723`), not a failure code — confirmed intentional by
`deploy/README.md:58-70`: only a true watchdog-timeout hang restarts; a
clean stop (which this looks exactly like) needs an operator.

`notify/watch.py:32` (`_ALWAYS_NOTIFY_TYPES = frozenset({RUN_SUMMARY,
RUN_STOPPED})`) — a plain `RUN_STOPPED` with `stop_reason=queue_empty` *is*
always-notify-eligible by type, so if `cosmo notify watch` had been
configured and running, a message would have gone out every time. It
wasn't configured for this run (no Telegram token), so in practice nothing
fired. Separately, `notify/watch.py:1-16`'s own docstring describes a
staleness detector (events table silent for `stale_after_seconds` while the
run hasn't reached a terminal `run_state`) — that's a real, related safety
net, but it doesn't cover this case either: a `QUEUE_EMPTY` stop *is* a
terminal `run_state`, so the watcher would see a normally-closed run, not a
hang.

`task/machine.py` retries are bounded within one `run_task` call; once
exhausted the task becomes `BLOCKED` terminally. `cli/main.py`'s
`queue_retry` command and `writer.queue_retry` are the only code that ever
clears a `BLOCKED` task back to `queued` — both are explicit, human-invoked
CLI actions. `watchdog.py` is pure `sd_notify` liveness-ping plumbing with
no task-state logic. **There is no code path anywhere that automatically
re-queues a `BLOCKED` task.**

## What a real fix looks like (sketch, not a committed design)

Four independent, separately-shippable pieces — do not bundle them into one
change:

1. **Give "stuck on BLOCKED" its own stop reason, distinct from
   `QUEUE_EMPTY`.** The data already exists (`summary.blocked_by_reason`)
   at the point `loop.py:271` decides `stop_reason` — this is a matter of
   checking `if summary.blocked_by_reason: stop_reason =
   StopReason.BLOCKED_REMAINING else StopReason.QUEUE_EMPTY` (naming
   aside), then **not** including the new reason in
   `_RUN_SUCCESSFUL_STOP_REASONS` and giving it its own (red/yellow, not
   green) styling at `cli/main.py:719-723` and `:1821-1823`. Open question:
   should this also change the process exit code (currently 0) so
   `deploy/cosmo-run.service`'s existing `Restart=on-failure` /
   `RestartPreventExitStatus=1` plumbing picks it up for free, or is an
   automatic systemd restart the wrong response to a block that a restart
   can't fix on its own (see item 3)? Exit-code semantics and systemd
   behavior need to be decided together, not this bullet in isolation.
2. **Get `cosmo notify watch` actually configured and running before the
   next unattended batch.** The signal already exists and is
   always-notify-eligible (`RUN_STOPPED`, any `stop_reason`) — this acceptance
   run simply never had a Telegram token configured, so nothing could have
   fired regardless of item 1. This is pure deployment, not new code, and
   the highest-payoff-for-lowest-risk item here: it alone would have turned
   most of this run's multi-hour gaps into minutes.
3. **Consider a bounded, explicit auto-requeue for specific block
   categories** — most plausibly the cost-ceiling case
   (`use-local-storage-hook` sat `blocked` for ~20 hours with
   `blocked_reason=cost` and no failure record at all, never re-evaluated
   once the run's cost picture changed). Open questions: does this live in
   `run/loop.py` (re-check cost-blocked tasks at the start of every new
   `cosmo run` invocation, not mid-run) or in `run/breaker.py`/`run/cost.py`
   directly? Should it be scoped to cost-ceiling blocks only (a
   re-evaluable condition) and deliberately exclude failure-exhausted
   blocks (which need a human judgment call about whether retrying the same
   way makes sense at all — the existing repeat-block guard exists
   precisely because blind auto-retry of a failure is usually wrong)?
4. **Open question, not a recommendation**: should future spec batches be
   authored with more parallel branches so one stuck root task doesn't
   stall literally everything? This spec's chain (data model → hooks → UI →
   e2e) may be inherently sequential — worth asking on the *next* spec
   batch whether independent branches exist before assuming serial-by-default
   is always fine, not a mandate to restructure this one after the fact.

**Cross-reference, not part of this document's own scope**: the handoff's
existing open item on `REVIEWING`/`VALIDATING` timeout retuning
(§3.3, "Open Item 2") now has two more real data points from this run — two
`todo-e2e` `VALIDATING` cycles at ~23-25 real minutes each, lost entirely to
the `crypto.randomUUID()` gate quirk above — supporting a closer look, not
proof the current wall is wrong. That retuning decision stays a human call,
unchanged from how the prior handoff already framed it.

## Non-goals for this document

- Not a mandate to rewrite `run/loop.py`'s stop-reason logic or
  `deploy/cosmo-run.service` opportunistically alongside unrelated work —
  same posture as v6.
- Not about the two already-fixed `MERGING`-refusal bugs (deviation 69) —
  those are closed; this document is about the *response time* to a block,
  not about making blocks rarer.
- Not about re-litigating the deliberate manual-resume posture for quota
  pauses or circuit-breaker trips (spec 6.5) — item 3 above is scoped to
  re-evaluable conditions like a cost ceiling, not to reopening that
  decision.
- Not a claim that this spec's serial dependency chain was a mistake — item
  4 is an open question for the *next* spec batch, not a retroactive
  critique of this one.
