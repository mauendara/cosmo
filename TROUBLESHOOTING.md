# Troubleshooting

🇬🇧 English | [🇪🇸 Español](TROUBLESHOOTING.es.md)

Start here for any failure. Then work down to the specific symptom.

## The first four commands

```bash
cosmo doctor                        # is the host still capable
cosmo report                        # how did the run end
cosmo queue ls --status blocked     # what's stuck
cosmo queue failures <task_id>      # the actual error text
```

`cosmo queue failures` is the one people miss. It's the only place the real
assertion messages and stack excerpts live — event payloads carry failing
test *names*, not their text, deliberately.

For everything in order, including payloads:

```bash
cosmo events tail --run <run_id> --payload
```

---

## Run-level problems

### The run exited 1 but says the queue is empty

Check the stop reason in `cosmo report`. Most likely `blocked_remaining`:
the scheduler had nothing left to run because everything remaining is
`BLOCKED`, not because the work finished. That's a distinct stop reason
precisely so it never reads as success.

```bash
cosmo queue ls --status blocked
cosmo queue failures <task_id>
```

### The run is PAUSED and won't continue on its own

Look at the pause reason:

- **`circuit_breaker`** — enough distinct tasks blocked (or enough
  environment-error weight accumulated) that Cosmo concluded something
  systemic is wrong. **This requires a human by design.** Fix the underlying
  cause, then `cosmo run resume`.
- **`quota_exhausted_5h`** — a rate-limit window. It resumes itself at the
  reported reset time, or after `quota.default_5h_resume_delay_seconds`.
  Nothing to do but wait.
- **`quota_exhausted_weekly`** — a week is not something to wait through in
  a run. Resume manually when the window has actually reset.

```bash
cosmo run resume              # most recently paused
cosmo run resume <run_id>
```

### The circuit breaker keeps tripping

It trips on `consecutive_blocked_threshold` distinct tasks blocking in a row,
or accumulated environment-error weight. Look at *why*:

```bash
cosmo events tail --type task.blocked --payload
```

If the blocked reasons are all the same, it's one cause, not three failures.
Common ones: Docker unavailable or out of disk, the harness CLI broken or
rate-limited, or a project template missing a constraint every task now
rediscovers.

`merge_conflict` and `flaky_unresolved` blocks never count toward the
consecutive tally, so if you're tripping despite those, something else is
wrong.

### The run stopped with `cost_limit_reached`

`cost.max_cost_per_run_usd` was hit. Raise it and start a new run. Note that
a new run starts every counter fresh — this is why the systemd unit
deliberately does *not* auto-restart on this exit code.

If individual tasks are blocking on `cost` instead, that's the per-task
ceiling. Raise `cost.max_cost_per_task_usd` and just run again: at every
startup, Cosmo re-evaluates cost-blocked tasks against the *current* config
and unblocks the ones no longer over the line, preserving their attempt count
and worktree. You'll see `task.cost_requeued` events.

### The run stopped with `disk_low` before doing anything

Below `disk.min_free_gb` (default 10 GB) at startup. This abort is
deliberate: a disk that fills mid-run fails every subsequent task with I/O
errors that read exactly like code errors, so the agent tries to "fix" them,
retries burn budget, and the circuit breaker trips for the wrong reason.

Reclaim space:

```bash
docker system prune -a                    # gate images are large
du -sh ~/.local/share/cosmo/work/*        # per-run worktrees
du -sh ~/.local/share/cosmo/logs
```

Worktrees for ended runs are swept at the next run's startup. `BLOCKED`
tasks' worktrees are kept for inspection — remove them by hand once you're
done, or resolve the tasks. Harness logs rotate on `log_retention.done_days`
(7) and `blocked_days` (30). Docker images are yours to prune.

### The run stopped with `max_time`

`timeouts.run_wall` expired (default 10 hours). Not an error — set it to the
window you actually have.

### The run stopped with `crashed`, or tasks came back as `task.interrupted`

The previous run's process died. Cosmo is strictly serial and
single-process, so a task found mid-flight at startup can only mean that.
Recovery is automatic: interrupted tasks are requeued and the stale
`run_state` row is closed out. Work in flight is lost.

Check `journalctl -u cosmo-run.service` for the actual cause — OOM kill,
`wsl --shutdown`, a watchdog timeout.

### A second `cosmo run` refuses to start

By design — one run per `data_dir`, enforced by a lock file
(`<data_dir>/cosmo-run.lock`) holding the owning PID:

```
another cosmo run (pid 4711) already holds /var/cosmo/cosmo-run.lock --
wait for it to finish, or remove the lock file if you've confirmed it's dead
```

A **stale** lock — one whose PID is no longer alive — is reclaimed
automatically, so you only see this when a process really is running. Check
with `systemctl status cosmo-run.service` or `ps -p <pid>`. Only remove the
file by hand if the named PID has been reused by something unrelated.

---

## Task-level problems

### A task is BLOCKED — what now?

```bash
cosmo queue show <task_id>       # status, attempts, last error, worktree path
cosmo queue failures <task_id>   # every attempt, with the real error detail
```

The worktree and branch are left on disk. Go look at them — the failing
state is exactly as the agent left it.

Once you've fixed the cause:

```bash
cosmo queue retry <task_id> --repo /path/to/repo
```

`retry` resets the attempt count. If the worktree still holds the commit
`PROPOSING` made, only the failed implementation is discarded and the valid
OpenSpec change survives, so the next run resumes at `IMPLEMENTING` without
paying for propose again.

### `cosmo queue retry` refuses

The repeat-block guard: this task has already blocked for the same reason
`retries.repeat_block_threshold` times before. Because `retry` resets the
attempt counter, nothing else remembers that, and you could hand it another
budget indefinitely without noticing.

Read `cosmo queue failures <task_id>` and address the recurring reason. Then
`--force`. Use it because a human fixed something, not to silence the
message.

### Blocked with `blocked_reason=environment`

Something outside the code failed: Docker unavailable, a stage timeout, the
harness process dying, a broken review call. Environment errors don't consume
the code retry budget, but they do get a bounded local retry.

```bash
cosmo doctor
docker ps -a
docker run --rm hello-world
```

### Blocked with `blocked_reason=merge_conflict`

The merge ladder tried to merge, hit a conflict, rebased and re-ran the full
gate, and still couldn't land it. The conflict is never handed back to the
agent to resolve blind.

Resolve it yourself in the task's worktree, or re-scope the task. These
blocks are excluded from the circuit-breaker tally — they mean queue
contention over shared files, not a broken environment. If you're getting a
lot of them, your tasks overlap too much; add `depends_on` edges to serialize
the ones touching the same files.

### Blocked with `blocked_reason=code_failure`

The gate genuinely failed, `max_attempts` times.

```bash
cosmo queue failures <task_id>
```

Look at `failure_stage` first:

- `build` — it doesn't compile.
- `unit_tests` / `e2e_tests` — real test failures, with the actual assertion
  text in `error_detail`.
- `test_integrity` — the diff gate rejected the change. See below.
- `secrets` — gitleaks found something in the diff.
- `adversarial_review` — the fresh reviewer rejected it; `error_detail`
  carries the reason.

Often the fix is the spec, not the code: the task was underspecified, or it
was too large to land in the attempt budget. Split it and re-queue.

### Blocked with `blocked_reason=flaky_unresolved`

A test failed, was rerun in isolation `gate.flaky_rerun_limit` times, and
failed every time — so it isn't flaky, or it's flaky in a way isolation
doesn't reproduce. Treat it as a real failure first. If it genuinely is
flaky, add it to `quarantine.yml` with an owner and an expiry.

### `test_integrity` — the diff gate rejected the change

One of: an existing test file was deleted, an existing test file was
**modified at all**, a skip annotation was introduced, the net assertion
count dropped, or a test file lost more than
`gate.diff_gate_loc_drop_threshold` net lines.

The second one catches people out. Adding a *new* test file is fine — that's
what a well-behaved agent should do. Touching an *existing* one is a
violation regardless of whether the change was honest, because distinguishing
the two is exactly the judgment an unsupervised agent can't make on its own
behalf.

`cosmo queue failures` names which. Then decide honestly:

- **The agent gamed the tests.** Working as intended. Improve the spec so the
  task is achievable without weakening tests, or split it.
- **The change legitimately removes tests** (deleting a feature, refactoring
  a suite). Set `allow_test_edits` on that task — `cosmo queue add
  --allow-test-edits`, or the frontmatter key.

### The agent produced an empty implementation and the review rejected it

Almost always the test-path guard doing its job on a task whose entire
deliverable lives under a guarded path — an `e2e/` suite, `src/test/**`, a
`*.test.tsx`. The agent correctly refused to write anything and submitted
nothing.

Set `allow_test_edits: true` in the task file's frontmatter and re-queue.

### The e2e stage reports "playwright produced no report"

Your `playwright.config.ts` isn't writing where the gate parses. This is
indistinguishable from the suite never running, which is why it fails.

```ts
reporter: [["json", { outputFile: "playwright-report/results.json" }]],
```

### E2E fails with "Executable doesn't exist at .../chrome-headless-shell"

`@playwright/test` is unpinned or newer than the gate's image. The gate runs
`mcr.microsoft.com/playwright:v1.49.0-noble`, which has only that version's
browser binaries; a newer package resolves to a browser build the container
doesn't have. It works fine on your machine, where browsers are installed
locally.

```bash
npm install -D @playwright/test@1.49.0     # match gate.playwright_npm_version
```

### E2E runs but nothing loads

`playwright.config.ts` is hardcoding a localhost port. The gate starts the
built app as a container on a private Docker network and passes `BASE_URL`
pointing at that container's hostname.

```ts
use: { baseURL: process.env.BASE_URL ?? "http://localhost:4173" }
```

### The gate skipped a stage entirely

Stage selection is directory-driven. No `gate.backend_dir` → backend stages
skipped. No `gate.frontend_dir` → e2e skipped. If your layout differs from
`backend/` and `frontend/`, set those keys.

A backend-less repo does **not** skip e2e — Playwright runs against the
frontend alone. That's on purpose: silently passing e2e with zero tests run
would be indistinguishable from a repo with no suite.

### A task is stuck in `implementing` for hours

Check whether it's actually working:

```bash
cosmo events tail --task <task_id> --payload
```

`task.progress` events show subtasks completing. `task.heartbeat` shows
liveness. Both flowing means it's working, just slowly.

Neither flowing means the stall timer (`timeouts.implementing_stall`, default
20 minutes) should fire and kill it. If it doesn't, the wall clock
(`implementing_wall`, default 90 minutes) will.

A classic cause of "alive but making no progress": the session backgrounded a
long command and is polling it instead of working. The
`background_task_guard` hook blocks `run_in_background: true` on `Bash` for
exactly this. If you see it anyway, check that `cosmo init` actually
installed the hooks — `ls .agent/claude/hooks/` in the target repo.

### A task never starts

```bash
cosmo run --dry-run     # is it in the resolved order at all?
cosmo queue show <task_id>
```

If it's not in the order, an unmet `depends_on` is holding it. The run
summary's `stalled_queued_tasks` lists exactly these. Check that the
dependency id is spelled the way it's actually queued — remember `spec queue`
namespaces ids with the spec name.

---

## Environment problems

### `cosmo doctor` fails on `subscription billing`

`ANTHROPIC_API_KEY` is set. Unset it. Its presence silently switches billing
from your subscription to per-token API rates. Check shell profiles and the
systemd unit's own `Environment=` lines.

### `cosmo doctor` fails on `docker`

`docker` isn't on `PATH`, or the run user isn't in the `docker` group. After
`usermod -aG docker <user>`, the group change needs a new login session (or
`newgrp docker`) to take effect.

### `cosmo doctor` warns about `work dir filesystem`

Your `work_dir` is on `/mnt/...` — a Windows drive mount under WSL2. Builds
there go through the 9p bridge and are slow enough to distort every timeout
in your configuration. Move it inside the WSL2 filesystem. See
[setup-wsl2](user-docs/en/how-to/setup-wsl2.md).

### `cosmo doctor` reports leaked gate containers

A previous run's containers survived. Clean up before starting:

```bash
docker ps -a --filter label=orchestrator.run_id
docker rm -f $(docker ps -aq --filter label=orchestrator.run_id)
```

If it keeps happening, the process-group kill isn't completing — check for
`task.failed` events with `circuit_breaker_weight` in the payload, which is
the reap-failure signal.

### Templates not found

```
Cosmo's templates/ directory was not found at .../lib/python3.14/templates.
This requires an editable install (`uv tool install --editable .`) from a
full checkout of Cosmo's own repository.
```

Exactly what it says. Templates live in the repository, not in the installed
wheel:

```bash
cd /path/to/cosmo/checkout
uv tool install --editable .
```

### Config fails to load (exit code 2)

The error names the key and the constraint. Recurring ones:

- **A stall timer at or above its wall clock.** Refused, because a stall
  timer that can never fire silently disables the only protection against a
  hung harness.
- **`playwright_image` unpinned.** `:latest` or a bare image name is
  rejected; a silent upstream update turns a green suite red overnight and
  surfaces as a phantom regression.
- **`bypass_5h_with_credits` with no `max_cost_per_run_usd`.** The bypass
  removes the thing that would otherwise stop the spending; it doesn't ship
  without the ceiling that recreates it.
- **An unknown key.** Extras are forbidden, so a typo is an error rather than
  a silent no-op. `cosmo config show` prints what's actually in effect.

### The quarantine file breaks the gate

```
entry 'com.example.FooTest#flakyUnderLoad' (owner 'x@y.com') expired on
2026-01-31 -- renew or remove it
```

Working as designed. An expired entry raises rather than being ignored — a
stale quarantine silently protecting a dead test is the failure mode the
whole mechanism exists to prevent. Renew the expiry with a new owner
decision, or remove the entry and fix the test.

### The task branch commits fail on a missing git identity

`cosmo init` configures a local identity in the target repo when none exists.
If you registered the project without a full `init`, set one:

```bash
git -C /path/to/repo config user.name "Cosmo"
git -C /path/to/repo config user.email "cosmo@yourdomain"
```

### Commits fail with "gitleaks not found on PATH"

The pre-commit hook fails closed: no `gitleaks` means no commit, rather than
a silently skipped secret scan. Install gitleaks. `cosmo doctor` checks for
it so this is a preflight-visible problem rather than a mid-run surprise.

---

## Notification problems

### `cosmo notify watch` refuses to start

`notify.enabled` is false, or the bot token or chat id is missing. Run
`cosmo notify config` — it writes the table and sends a real test message
before declaring success.

### Notifications stopped, and I don't know if the run is alive

That's what `watch.stale` is for: no events for `notify.stale_after_seconds`
(default 30 minutes) while the run isn't in a terminal status is itself
alerted on. If you're not getting *that* either, the watcher process is down:

```bash
systemctl status cosmo-notify.service
```

It's a separate unit from `cosmo-run.service` with no ordering dependency
between them, precisely so it can report a run that never started or died
early.

### I'm not hearing anything from a healthy run

Default `notify.min_severity` is `warning`, which a clean run may never
reach. Set `min_severity = "info"` if you want the play-by-play.
`task.completed` is always notified regardless of the threshold.

---

## Still stuck

Gather this before opening an issue:

```bash
cosmo --version
cosmo doctor
cosmo config show
cosmo report --run <run_id>
cosmo queue failures <task_id>
cosmo events tail --run <run_id> --payload --limit 200
```

**Redact before posting.** Event payloads and failure detail can contain
paths, branch names, error text from your source, and file contents.
