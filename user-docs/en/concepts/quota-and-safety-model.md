# Quota, cost, and the safety model

Everything that can stop a run, and everything that decides when it should.

An unattended run has no human to notice that it's spent $400, exhausted a
rate-limit window three hours ago, filled the disk, or spent the last two
hours failing every task for the same broken reason. Each of those needs its
own detector and its own response.

## The five things that stop or pause a run

| Cause | Outcome | Auto-resumes? |
| --- | --- | --- |
| Queue empty / all tasks done | `STOPPED` (`queue_empty` or `completed`) | n/a — exit 0 |
| Only blocked tasks remain | `STOPPED` (`blocked_remaining`) | no — exit 1 |
| Run wall clock expired | `STOPPED` (`max_time`) | no |
| Cost ceiling hit | `STOPPED` (`cost_limit_reached`) | no |
| Disk below floor | `STOPPED` (`disk_low`) | no |
| Circuit breaker tripped | `PAUSED` (`circuit_breaker`) | **no — needs a human** |
| Five-hour quota window exhausted | `PAUSED` (`quota_exhausted_5h`) | yes, after the window resets |
| Weekly quota exhausted | `PAUSED` or `STOPPED` (`quota_exhausted_weekly`) | no |

Only `completed` and `queue_empty` exit `0`. Everything else exits `1`,
which is what makes `Restart=on-failure` plus `RestartPreventExitStatus=1`
in the systemd unit do the right thing: a blind restart fixes none of these,
so systemd doesn't attempt one. A genuinely *hung* process never reaches
`sys.exit` at all — systemd's watchdog kill is a signal, not an exit status,
so that case still gets restarted.

---

## `blocked_remaining` — why "queue empty" isn't always success

There are two very different situations where the scheduler has nothing left
to run: the queue genuinely finished, and every remaining task is `BLOCKED`
with an un-actioned failure. Reporting both as `queue_empty` meant a run that
achieved nothing looked green and exited `0`, and nobody found out until they
went looking.

`blocked_remaining` is chosen whenever at least one task actually blocked
during the run. It gets yellow output and a nonzero exit code, so it shows up
in a notification and in systemd's status instead of quietly succeeding.

## The circuit breaker

Some failures are about one task. Others mean the environment is broken and
every subsequent task will fail the same way — burning quota and money to
learn nothing.

The breaker pauses the whole run when either threshold is met:

- **`consecutive_blocked_threshold`** (default 3) — that many *distinct
  tasks* landing `BLOCKED` in a row. A task reaching `DONE` resets the
  streak; "consecutive" is only meaningful relative to intervening successes.
- **`environment_error_threshold`** (default 3) — accumulated
  environment-error weight across distinct tasks. A process-reap failure
  contributes `reap_failure_weight` (default 2) instead of 1, because a
  leaked process pool poisons everything after it.

**`merge_conflict` and `flaky_unresolved` blocks are excluded entirely** —
they neither add to nor reset the streak. They signal queue contention over
shared files, not a broken environment, and letting them trip the breaker
would pause a perfectly healthy run.

A tripped breaker is `PAUSED`, not `STOPPED`, and **resuming requires a
human**. That's the point: the run stopped because something needs a person
to look at it, and auto-resuming would defeat the purpose. When you've
addressed it:

```bash
cosmo run resume            # the most recently paused run
cosmo run resume <run_id>
```

Note that a breaker pause stops the *whole* run, independent DAG branches
included. When the environment is suspect, "keep running the branches that
haven't failed yet" is a bet that the failure is local — which is exactly
what the breaker just concluded it isn't.

## Quota detection

Rate-limit windows are the constraint that actually bites a subscription-billed
overnight run. Cosmo detects exhaustion three ways, in descending order of
confidence.

**1. Primary — the harness's own structured signal.** The Claude adapter
extracts a rate-limit signal from the CLI's stream output, giving a window
(`five_hour` or `weekly`) and, when the wire carries one, a reset time.
Confirmed. Only actionable on a *failed* call: a rate-limit signal seen
mid-stream doesn't mean the call failed — the CLI's own internal retry often
absorbs one and the call succeeds anyway.

**2. Secondary — the terminal result's error subtype**, matched against
`quota.result_error_subtypes` (default `["error_rate_limit"]`). Also treated
as confirmed. This default has no verified capture behind it yet — it is
configurable precisely so it can be corrected the day a real one is observed,
rather than being hardcoded on a guess.

**3. Tertiary — a wall-clock heuristic.** `heuristic_consecutive_threshold`
distinct tasks (default 3) failing in under `heuristic_max_duration_seconds`
(default 5) with zero tool calls executed. Never reported as confirmed, and
never allowed to conclude `weekly` — there is no way to infer a weekly window
from timing alone, so an unconfirmed signal is always treated as the shorter,
safer five-hour case.

### What happens on a signal

A **five-hour** window pauses the run and schedules an auto-resume: at the
reported reset time, or `quota.default_5h_resume_delay_seconds` (default
18000, i.e. 5 hours) when the signal carries no reset time — the observed
wire shape often doesn't.

A **weekly** exhaustion pauses or stops depending on whether the run's
remaining budget could outlast it. A week is not something to sit and wait
through.

### Bypassing the five-hour pause

Some accounts have usage credits that keep calls succeeding past the included
subscription allowance. `quota.bypass_5h_with_credits = true` opts into
spending them: the run continues past a confirmed five-hour signal and emits
a `warning`-severity `quota.bypassed` event carrying the reset time and
spend-so-far.

**This requires a non-zero `cost.max_cost_per_run_usd`.** Cosmo refuses to
load a config with the bypass on and no spend ceiling — the bypass exists to
remove the thing that would otherwise stop the spending, so it must not ship
without the backstop that recreates it.

## Cost

Two independent ceilings, both defaulting to `0.0`, which means *no hard
stop* — the correct posture for a subscription-billed harness, where quota
windows govern instead of dollars.

- **`cost.max_cost_per_run_usd`** — the whole run. A `run.cost_warning` event
  fires at `cost.warn_at_fraction` (default 80%); hitting the ceiling stops
  the run with `cost_limit_reached`.
- **`cost.max_cost_per_task_usd`** — one task. Exceeding it blocks the task
  with `blocked_reason=cost` and moves on, rather than stopping the run.

Set both if you're on metered billing.

A cost-blocked task has a useful property: it can only ever legitimately
clear by a human raising the ceiling, since the recorded cost never goes
down. So at every run's startup, Cosmo re-evaluates every `cost`-blocked task
against the *current* config and unblocks the ones no longer over the line —
preserving their attempt count and worktree, because nothing about the task
itself failed. Each one emits `task.cost_requeued`.

## Disk

`disk.min_free_gb` (default 10) is checked once, at run startup. Below it,
the run aborts immediately with `disk_low` and `critical` severity.

The alternative is worse than it sounds: a disk that fills mid-run fails
every subsequent task with I/O errors that read exactly like code errors —
so the agent tries to "fix" them, retries burn budget, and the circuit
breaker eventually trips for entirely the wrong reason.

Worktrees, Docker images and harness logs are what fill it. Log retention is
automatic (`log_retention.done_days` / `blocked_days`); Docker images are
yours to prune.

## Timeouts and process kills

Every state has a wall clock. `IMPLEMENTING` and `VALIDATING` also have stall
timers, and configuration refuses to load if a stall timer is set longer than
its own wall clock — a stall timer that can never fire silently disables the
only protection against a hung harness.

A timeout kills the **entire process group**: SIGTERM, `timeouts.kill_grace`
seconds (default 20), SIGKILL. Then a sweep removes gate containers by their
`orchestrator.run_id` / `orchestrator.task_id` labels and checks for
processes still holding the worktree open.

Getting this wrong is expensive in a way that shows up hours later: a killed
parent whose Maven, Node, Chromium or Docker children survive leaves a host
slowly filling with memory-hungry orphans until every later task fails for
reasons unrelated to its own code.

`cosmo doctor` reports leaked gate containers as a core check, so a previous
run's mess is visible before the next one starts.

## Crash recovery

Cosmo is strictly serial and single-process, so a task found in any
non-terminal state at startup can only mean the process driving it died.

At every run's startup:

- Every mid-flight task is emitted as `task.interrupted` (`warning`) and
  requeued.
- A `run_state` row still marked `running` is closed out as `crashed`.
- Cost-blocked tasks are re-evaluated against the current ceiling.
- Stale worktrees from ended runs are swept.

## The permission model

Specific to the Claude Code adapter, though the posture generalizes.

- **`dontAsk` fails closed.** Only tool calls matching the allow-list
  execute. Nothing not explicitly allowed runs — the default is denial, not
  permission.
- **`bypassPermissions` is never used.** Not merely omitted:
  `--dangerously-skip-permissions` and `bypassPermissions` are asserted
  absent from the constructed argv, and a separate test checks it from the
  outside. The host holds real credentials; blast radius isn't zero.
- **Deny rules are absolute** and apply in every mode. Secret-shaped paths
  (`.env*`, `secrets/**`, `*.pem`, `id_rsa*`) and the
  schedule-and-resume tools are denied outright.
- **Only project settings are loaded** (`--setting-sources project`). The
  operator's global `~/.claude` — arbitrary personal hooks, plugins and MCP
  servers with unknown cost and side effects — is not sourced into an
  unattended run.
- **The allow-list is passed on the command line as well as in
  `settings.json`.** Claude Code has a workspace-trust gate: in a directory
  that never went through the interactive trust dialog — which a freshly
  created per-task worktree never can — it silently ignores every
  `permissions.allow` entry from `settings.json` and denies `Write`/`Edit`/
  `Bash`, without surfacing anything the adapter can see. Passing the same
  list as a CLI flag is unaffected by workspace trust.
- **`ANTHROPIC_API_KEY` is scrubbed** from the child process environment, and
  `cosmo doctor` fails outright if it's set on the host. Its presence
  silently switches billing from the subscription to per-token API rates —
  an expensive thing to discover after an unattended night.
- **Telemetry is on, content logging is explicitly off.**
  `OTEL_LOG_USER_PROMPTS=0` is set rather than trusted to default: prompts
  and file contents in a telemetry backend are a data-exfiltration path for a
  private codebase.

Full threat model: [SECURITY.md](../../../SECURITY.md).

## Notifications

Cosmo can tell you when any of the above happens, through Telegram today.

```bash
cosmo notify config    # interactive: token, chat id discovery, real test message
```

`cosmo notify watch` polls the events table and forwards anything at or above
`notify.min_severity` (default `warning`), plus `task.completed`
unconditionally.

It runs as its **own process**, never inline in `cosmo run`. That's not
architectural tidiness — a sink living inside the run loop cannot report the
run loop's own crash, because whatever would send that message dies with it.
The watcher also raises its own alert when the events table goes quiet for
`notify.stale_after_seconds` while the run isn't in a terminal status, which
is the only signal that catches a run process that died without saying
anything.
