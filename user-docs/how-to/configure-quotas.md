# How to configure quotas, cost ceilings, and retry budgets

Everything here goes in your user config file — `$COSMO_CONFIG`, or
`~/.config/cosmo/config.toml`. Every key is a deep-merged override, so you
only write the ones you change.

```bash
cosmo config show          # what's actually in effect
cosmo config show --paths  # which file it's reading
```

Config is validated at load time. A bad value fails the command with exit
code `2` rather than mid-run.

---

## If you're on a subscription (the default posture)

Leave the cost ceilings at `0.0`. They mean "no hard stop," which is correct
when dollars aren't the binding constraint — rate-limit windows are.

What you actually want is for the run to pause when a window is exhausted and
resume itself when it resets, which is the default behavior. Verify the
fallback resume delay matches your plan's window:

```toml
[quota]
default_5h_resume_delay_seconds = 18000    # 5 hours; used when the signal carries no reset time
```

The primary signal usually carries a reset time and this delay isn't used.
The observed wire shape sometimes doesn't, which is what this covers.

## If you're on metered billing

Set both ceilings. They're independent.

```toml
[cost]
max_cost_per_run_usd  = 40.0   # hitting this STOPS the run
max_cost_per_task_usd = 5.0    # hitting this BLOCKS the task, run continues
warn_at_fraction      = 0.75   # run.cost_warning event at 75% of the run limit
```

- The **run** ceiling stops everything with `cost_limit_reached`. Size it as
  what you're willing to lose overnight, not what you expect to spend.
- The **task** ceiling blocks the offending task and lets the queue continue.
  This is the more useful of the two day to day: one pathological task
  looping on a hard problem doesn't consume the whole night's budget.

A cost-blocked task can only clear by a human raising the ceiling (the
recorded cost never goes down), so at the next run's startup Cosmo
re-evaluates every one of them against the *current* config and unblocks the
ones no longer over the line — keeping their attempt count and worktree,
since nothing about the task itself failed. You'll see `task.cost_requeued`
events.

So the recovery from "I set it too low" is just: raise it and run again.

## Spending usage credits past the subscription window

Some accounts have credits that keep calls succeeding past the included
allowance. To use them instead of pausing:

```toml
[quota]
bypass_5h_with_credits = true

[cost]
max_cost_per_run_usd = 50.0    # REQUIRED
```

Cosmo **refuses to start** with the bypass on and no spend ceiling. The
bypass removes the thing that would otherwise stop the spending, so it
doesn't ship without the backstop that recreates it.

Each bypassed signal emits a `warning`-severity `quota.bypassed` event with
the window's reset time and spend-so-far. Watch for those.

Weekly exhaustion is never bypassed.

## Tuning quota detection

Three detectors, in descending confidence. You rarely need to touch the first
two.

```toml
[quota]
result_error_subtypes           = ["error_rate_limit"]   # secondary
heuristic_consecutive_threshold = 3                       # tertiary
heuristic_max_duration_seconds  = 5.0                     # tertiary
```

The tertiary heuristic fires when that many *distinct tasks* fail in under
that many seconds with zero tool calls executed — the shape of a hard
rate-limit rejection. It is never reported as confirmed and never allowed to
conclude a weekly window, since timing alone can't distinguish one.

If you see spurious quota pauses, raise `heuristic_consecutive_threshold`.
If real exhaustion is going undetected and burning the night on instant
failures, lower it.

`result_error_subtypes` has no verified capture behind its default. It's
configurable specifically so it can be corrected the day you observe a real
one — check `cosmo events tail --payload` after a genuine exhaustion.

## Retry budgets

```toml
[retries]
max_attempts           = 2
delay_min              = 30
delay_max              = 60
repeat_block_threshold = 2
```

`max_attempts = 2` means the **third** code-level failure blocks the task.
Only genuine code-level judgments count: a gate verdict of `code_error` or
`test_integrity`, or an `IMPLEMENTING` timeout. An `environment_error` never
consumes an attempt, and neither does a failure confirmed `flaky` by rerun.

Per-task overrides at enqueue time:

```bash
cosmo queue add openspec/changes/hard-thing --task-id hard-thing --max-attempts 4
```

`repeat_block_threshold` governs `cosmo queue retry`. Because a retry resets
`attempt_count` to zero, nothing otherwise remembers that a task has already
blocked for the same reason across earlier runs — you can hand it another
budget indefinitely without noticing. Once its latest block matches this many
prior blocks for the same reason, `retry` refuses and tells you instead.
`--force` overrides; use it after a human has addressed the recurring cause,
not to make the message go away.

## The circuit breaker

```toml
[circuit_breaker]
consecutive_blocked_threshold = 3
environment_error_threshold   = 3
reap_failure_weight           = 2
```

Raise `consecutive_blocked_threshold` if you're deliberately running a batch
where several tasks are expected to need human attention and you'd rather the
run push through the rest. Lower it if you'd rather find out early that
something is systemically wrong.

`merge_conflict` and `flaky_unresolved` blocks never count toward the
consecutive tally. Don't try to compensate for them here.

A tripped breaker `PAUSED`s the run and **requires a human** — that's the
point. Resume with `cosmo run resume` once you've addressed the cause.

## Timeouts

```toml
[timeouts]
proposing_wall     = 900
implementing_wall  = 5400
implementing_stall = 1200
validating_wall    = 2700
validating_stall   = 600
reviewing_wall     = 900
committing_wall    = 300
merging_wall       = 300
run_wall           = 36000
kill_grace         = 20
```

Two rules:

1. **Every stall timer must be less than its wall clock.** Config load
   refuses otherwise — a stall timer that can never fire silently disables
   the only protection against a hung harness.
2. **If you raise `implementing_wall` or `validating_wall`, raise
   `WatchdogSec` in the systemd unit too.** The watchdog is sized against
   the worst-case healthy task; leave it behind and systemd will kill
   perfectly healthy long-running tasks.

`run_wall` is the whole-run clock. Expiry stops with `max_time`. Set it to
the window you actually have — if you're running 22:00 to 07:00, that's
32400, not the default 36000.

`kill_grace` is the SIGTERM-to-SIGKILL interval on the process group.
Raising it gives a harness longer to shut down cleanly; lowering it reclaims
a wedged host faster.

## Gate stage budgets

```toml
[gate]
stage_timeout_seconds = 1800
```

Per Docker stage (build, unit, e2e), not the whole gate. Raise it for a slow
monorepo. A stage timeout is classified `environment_error`, so it does not
consume the task's retry budget.

## Disk

```toml
[disk]
min_free_gb = 20.0
```

Checked once at run startup; below it the run aborts with `disk_low` before
doing anything. Size it above what a single task's worktree plus Docker
layers actually consume, with headroom — the failure mode this prevents (a
disk filling mid-run, failing every task with errors that read like code
errors) is much worse than an aborted start.

```toml
[log_retention]
done_days    = 7
blocked_days = 30
```

Raw harness logs, keyed off the task's current terminal status.

## Turning off the adversarial review

```toml
[review]
enabled = false
```

This removes an entire harness call per task — meaningful for both time and
spend. It also removes the only check that reads the diff with no memory of
how it was written. Turn it off knowingly.

The review's own budget is `retries.max_attempts`, shared with gate failures,
not a separate ceiling.

## A worked example: overnight, subscription, cautious

```toml
[timeouts]
run_wall = 30600            # 8.5 hours: 22:15 to 06:45

[retries]
max_attempts = 3            # one more shot before blocking

[circuit_breaker]
consecutive_blocked_threshold = 2   # find out early if the night is going badly

[disk]
min_free_gb = 25.0

[notify]
enabled = true
min_severity = "info"       # the default 'warning' can be silent all night
telegram_bot_token = "..."
telegram_chat_id = "..."
```

```bash
chmod 600 ~/.config/cosmo/config.toml
cosmo config show           # confirm it merged the way you expect
```
