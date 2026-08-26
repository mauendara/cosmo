# Cosmo

Cosmo is an unattended orchestrator that works through a queue of
spec-driven development tasks against one of your repos — proposing a
change, implementing it, validating it in Docker, adversarially reviewing
it, and merging it — without you sitting in the loop. Point it at a rough,
hand-written spec (or an already-written [OpenSpec](https://github.com/Fission-AI/OpenSpec)
change, if you prefer to author one directly), queue the resulting work up,
and run Cosmo overnight or while you do something else.

**Cosmo is harness-agnostic by design.** It never talks to a coding-agent
CLI directly — every invocation goes through a common adapter interface
(`cosmo.harness`), and core orchestration code never branches on which
harness is configured. **Claude Code is the only adapter implemented so
far** — a starting point, not the intended ceiling. Adding another harness
(a different CLI, a different model provider) means writing one more
adapter against that same interface, not reworking the orchestrator.

**The validation gate is the only source of truth about correctness.**
Nothing reaches `DONE` on the strength of the agent's own claim to have
finished — every task is built in an isolated git worktree, run through a
real Docker build + unit-test + e2e pipeline, and only merged if that gate
passes.

Cosmo is its own project, separate from whatever codebase(s) it operates
on ("target repos").

## How it works

1. You give Cosmo a **target repo** — a real git project you want work
   done on — and bootstrap it once with `cosmo init`.
2. You get work into the queue one of two ways (see
   [Two ways to queue work](#two-ways-to-queue-work)): write a rough spec
   and let Cosmo enrich and decompose it into tasks (`cosmo spec add` /
   `cosmo spec queue`), or hand-author an [OpenSpec](https://github.com/Fission-AI/OpenSpec)
   change yourself and queue it directly (`cosmo queue add`). Either way you
   end up with **tasks** in the queue, optionally depending on each other.
3. `cosmo run` drains the queue: for each task, in dependency order, it
   creates a fresh git worktree, drives the configured harness through
   propose → implement, runs the real validation gate (build, unit tests,
   e2e via Playwright, secret scanning) in Docker, runs a fresh,
   session-less adversarial review of the diff, and — once all of that
   passes — merges to your base branch and archives the OpenSpec change.
4. A task that fails (a gate failure, or a rejected review) gets retried a
   bounded number of times with an informed retry prompt (the real failure
   detail fed back in); one that can't be fixed automatically lands
   `BLOCKED` for you to look at, and the queue moves on to the next task.
   Enough distinct failures trip a circuit breaker and pause the whole run
   for a human.
5. Everything — every state transition, every failure, every decision —
   is recorded to a local SQLite database and an append-only event log,
   so you can reconstruct what happened after the fact without reading
   raw logs.

## Prerequisites

- **Python 3.12+** and [`uv`](https://docs.astral.sh/uv/)
- **git**
- **Docker** (the validation gate always runs in containers)
- **[OpenSpec](https://github.com/Fission-AI/OpenSpec) CLI** (`openspec`) on `PATH` — what the underlying `propose`/`apply`/`archive` flow is built on, whether you author a change by hand or let `cosmo run` create one from a `cosmo spec add`-produced task
- **[`gitleaks`](https://github.com/gitleaks/gitleaks)** on `PATH` — the pre-commit secret-scanning guardrail
- **A configured harness on `PATH`** — today that means **[Claude Code](https://claude.com/claude-code)** (`claude`), with a subscription/API access; see the harness-agnostic note above — this is the one adapter that exists, not a permanent requirement of the design
- On Windows: WSL2, with the working repo kept inside the WSL2 filesystem (not `/mnt/c/...` — much slower I/O and periodically flaky with Docker)

Run `cosmo doctor` any time to check all of this at once.

## Install

```bash
git clone <this repo> cosmo
cd cosmo
uv sync
uv run cosmo --version
```

For a `cosmo` command on your `PATH` without `uv run` in front of it:

```bash
uv tool install --editable .
```

## Quick start

### 1. Point Cosmo at a target repo

```bash
cosmo init /path/to/your-project
```

This bootstraps the target repo: creates `openspec/` (if not already
present), seeds `docs/` with a starter template, injects the configured
harness's own operating policy under `.agent/<harness>/` (with Claude Code
as the resolved harness today, that's `.agent/claude/` — `CLAUDE.md`,
agent/skill definitions, guardrail hooks), and symlinks whatever that
harness expects at the repo root (for Claude Code: `CLAUDE.md`, `.claude/`)
into it. It also registers the project so its harness can be resolved
automatically from its path later.

Run `cosmo doctor --project-path /path/to/your-project` to confirm
everything's ready.

### 2. Get work into the queue

See [Two ways to queue work](#two-ways-to-queue-work) below for the full
picture. The short version, starting from a rough idea:

```bash
cosmo spec add add-login --repo /path/to/your-project --from ./my-rough-idea.md
# review/edit the printed preview, then:
cosmo spec queue add-login --repo /path/to/your-project
```

```bash
cosmo queue ls          # see everything queued, however it got there
```

### 3. Preview the order, then run

```bash
cosmo run --repo /path/to/your-project --dry-run    # prints the resolved execution order, does nothing
cosmo run --repo /path/to/your-project               # the real thing
```

This drains the *entire* queue, strictly one task at a time, until it's
empty, a task's circuit-breaker threshold trips, a cost/quota ceiling
intervenes, or the run's own wall clock expires. It stops with a clear
reason either way — see [Monitoring a run](#monitoring-a-run) below.

To drive a single already-queued task through to completion instead of
the whole queue (useful for testing one change in isolation):

```bash
cosmo run --repo /path/to/your-project --task add-login
```

## Two ways to queue work

**Start from a rough idea (recommended).** Write down whatever you have —
it can describe several pieces of work, not just one — and let Cosmo turn
it into well-scoped, dependency-aware tasks:

```bash
# docs/specs/<name>-spec.md is the one file you write by hand (or point
# --from at a file anywhere and Cosmo copies it in under the enforced name).
cosmo spec add add-login --repo /path/to/your-project --from ./my-rough-idea.md
```

This drives the harness through enrichment (reading the target repo's own
`docs/backend/`, `docs/frontend/`, `docs/data-model.md`, `docs/base-standards.md`
for conventions) and decomposition, and writes one
`docs/specs/add-login-spec/tasks/<task>-task.md` file per identified unit of
work — each a small, git-tracked markdown file with `task_id`/`depends_on`/
`priority`/`title` frontmatter — then prints a preview of the resulting task
list and dependency graph. **Nothing is queued yet.** This is the preview:
open and hand-edit any of those files if you want to adjust scope, wording,
or dependencies before committing to the queue.

```bash
cosmo spec queue add-login --repo /path/to/your-project
```

Scans `docs/specs/add-login-spec/tasks/*.md` and inserts one task per file
into the real queue (tagged `spec_batch_id=add-login-spec`, so they're
identifiable as having come from the same spec later). No OpenSpec change
exists yet at this point either — each task creates its own, lazily, the
first time it actually runs.

**Or hand-author an OpenSpec change yourself**, if you'd rather work at that
level directly. Use OpenSpec's own flow to describe a change under the
target repo's `openspec/changes/<change-name>/` (see OpenSpec's own docs
for the authoring format), then:

```bash
cosmo queue add openspec/changes/add-login-page --task-id add-login
```

Add more, with dependencies where one task's work needs another's to land
first:

```bash
cosmo queue add openspec/changes/add-login-tests --task-id login-tests --depends-on add-login
```

Either path lands tasks in the same queue, scheduled by the same DAG —
`cosmo run` doesn't know or care which front door a task came through.
`--priority <int>` breaks ties among tasks that become eligible at the same
time (higher runs first); it never overrides a `depends_on` edge. Cycles
are rejected at enqueue time either way, not discovered mid-run.

## Monitoring a run

```bash
cosmo report                       # the most recent run's summary
cosmo report --run <run_id>        # a specific one
```

Shows status, stop/pause reason, completed/blocked counts (broken down by
reason), cost, and duration.

```bash
cosmo events tail                          # recent events across everything
cosmo events tail --run <run_id>           # scoped to one run
cosmo events tail --task <task_id>         # scoped to one task
cosmo events tail --type task.blocked      # filter by event type
cosmo events tail --payload                # print each event's full JSON body
```

The table alone (timestamp/severity/type/run/task) tells you *that*
something happened; add `--payload` to see *what* — the actual blocked
reason, failing test names, cost figures, pause details.

```bash
cosmo queue show <task_id>         # current status, attempt count, last error, worktree path
cosmo queue failures <task_id>     # every recorded attempt's full failure detail
```

`queue failures` is the one that matters most after an unattended run: it
prints each attempt's failure type/stage, a summary, the *actual* error
detail (assertion messages, stack excerpts), files touched, and whether
it was retried or gave up and blocked.

A task that ends up `BLOCKED` stays that way until you act on it:

```bash
cosmo queue retry <task_id>                          # requeue it as-is
cosmo queue block <task_id> --reason merge_conflict   # block it by hand, if you need to
```

A `BLOCKED` task's worktree and branch are left on disk for inspection —
find the path via `cosmo queue show <task_id>`.

## Configuration

Cosmo ships sensible defaults for everything (see `src/cosmo/config/defaults.toml`).
Override them with a TOML file layered on top:

```bash
cosmo config show                 # the fully resolved config
cosmo config show --paths         # just where config/state/logs live
```

By default the user config lives at `$XDG_CONFIG_HOME/cosmo/config.toml`
(or `~/.config/cosmo/config.toml`); point at a different one with
`--config <path>` on any command, or `$COSMO_CONFIG`. State (the SQLite
database, worktrees, logs) similarly defaults to XDG data paths, and lives
wherever `paths.data_dir`/`work_dir`/`log_dir` are set in your config
otherwise — a droplet deployment typically points these at `/var/cosmo`.

Things worth knowing about before you tune them:

- `[cost]` — `max_cost_per_run_usd`/`max_cost_per_task_usd` default to
  `0.0` ("no hard stop"), the right posture for a subscription-billed
  harness; set them if you're on metered billing.
- `[circuit_breaker]` — how many distinct blocked tasks (or one
  process-reap failure) pause the whole run for you to look at.
  `merge_conflict`/`flaky` blocks don't count toward it.
- `[disk]` — `min_free_gb` (default 10) is checked before every run
  starts; below it, the run aborts immediately rather than fail every
  task partway through with a full disk.
- `[log_retention]` — how long harness logs are kept: 7 days for `DONE`
  tasks, 30 for `BLOCKED` ones.
- `[quota]` — how a harness usage-window rate limit is detected and how
  long the run pauses before auto-resuming (Claude Code's own rate-limit
  signal is the only one wired up today).
- `[review]` — `enabled` (default `true`) turns the fresh-session
  adversarial review between validation and merge on or off; a rejected
  review retries with the same bounded budget as a gate failure.

## Running unattended

`deploy/cosmo-run.service` is a systemd unit for running `cosmo run`
continuously and unattended — see `deploy/README.md` for installation,
the exact restart semantics (it restarts a wedged/watchdog-killed process,
but *not* a deliberate stop like a circuit-breaker pause, which needs you),
and WSL2 notes.

## Other commands

```bash
cosmo doctor                       # is this host ready to run Cosmo
cosmo validate <worktree> --task-id <id>   # run the validation gate standalone, outside a real task
cosmo harness list                 # registered harness adapters and their capabilities
cosmo harness probe --prompt "..." # smoke-test the harness with a raw prompt
cosmo project register <path>      # register a target repo without full cosmo init
cosmo project list
cosmo templates list               # available project doc templates
```

Every command accepts `--config`/`-c` to point at a specific config file,
and most accept `--harness` to override the resolved harness for one
invocation.

## Safety notes

- Cosmo never uses `--dangerously-skip-permissions`/`bypassPermissions`
  with the harness — asserted in code, not just by convention.
- `develop` (or whatever `git.base_branch` is set to) is the only merge
  target Cosmo ever touches automatically. Merging to `master`/`main` is
  always a manual, human step.
- A gitleaks pre-commit hook runs on every commit inside a task's
  worktree; a secret in the diff blocks the commit, not just a later gate
  check.
- The diff gate rejects a task that weakens or deletes tests to make a
  failing build pass — an autonomous agent's own claim of success is
  never trusted over the actual gate result.
