# CLI reference

Generated against the shipped command tree. Every command and flag below is
what `cosmo --help` actually exposes.

## Global

```
cosmo [OPTIONS] COMMAND [ARGS]...
```

| Option | Description |
| --- | --- |
| `--version` | Print the version and exit. |
| `--help` | Show help and exit. |

### Common options

These recur across commands rather than being global:

| Option | Applies to | Description |
| --- | --- | --- |
| `--config`, `-c <path>` | every command except `harness list`, `templates list` | Config file layered over the shipped defaults. For `notify config` only, this is also *where to write*. |
| `--harness <str>` | `doctor`, `init`, `harness probe`, `spec add`, `project register`, `run`, `run resume` | Override the resolved harness for this invocation. |
| `--repo <path>` | `spec add`, `spec queue`, `queue retry`, `run`, `run resume` | Target repo. Defaults to the current directory. |

Harness resolution order: `--harness` flag → the project's registration →
`harness.name` from config.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success. For `cosmo run`, only a `completed` or `queue_empty` stop. |
| `1` | Operation failed, or the run stopped/paused for any other reason (circuit breaker, quota, cost ceiling, disk, `blocked_remaining`). |
| `2` | Configuration could not be loaded or validated. |

---

## `cosmo doctor`

Check that this host can run Cosmo. Reports core (harness-agnostic) checks
and harness checks separately. Exits non-zero if any check is blocking.

| Option | Description |
| --- | --- |
| `--config`, `-c <path>` | Config file. |
| `--harness <str>` | Override the harness. |
| `--project-path <path>` | A registered target repo — supplies the project tier of harness resolution. |

Core checks: `python`, `git`, `docker`, `openspec`, `gitleaks`, `disk space`,
`state dirs writable`, `work dir filesystem`, `event/state store`, `leaked
gate containers`.

Claude adapter checks: `claude cli`, `subscription billing`
(fails if `ANTHROPIC_API_KEY` is set), `permission mode`.

## `cosmo init TARGET_PATH`

Bootstrap a target repo: `git init` and the base branch if needed,
`openspec/`, `docs/`, `.agent/<harness>/`, root symlinks, project
registration.

| Argument | Description |
| --- | --- |
| `target_path` | Path to the target repo. Runs `git init` itself if it isn't one. |

| Option | Default | Description |
| --- | --- | --- |
| `--harness <str>` | resolved | Override the harness. |
| `--project-template <str>` | `_blank` | Project docs template. See `cosmo templates list`. |
| `--force` / `--no-force` | `--no-force` | Overwrite `docs/` files already present. Prompts for confirmation. |
| `--git-author-name <str>` | — | Git identity to configure locally in the target repo. Paired with `--git-author-email`; given together, skips the interactive prompt. |
| `--git-author-email <str>` | — | See `--git-author-name`. |
| `--config`, `-c <path>` | — | Config file. |

## `cosmo validate WORKTREE`

Run the Docker validation gate standalone against a worktree. A diagnostic
entry point — it never touches the store, so the worktree need not correspond
to a queued task.

| Argument | Description |
| --- | --- |
| `worktree` | Path to the worktree to validate. |

| Option | Default | Description |
| --- | --- | --- |
| `--task-id <str>` | **required** | Task identifier, for container labels and attribution. |
| `--task-branch <str>` | the worktree's current branch | Branch under test. |
| `--base-branch <str>` | `git.base_branch` | Branch to diff against. |
| `--allow-test-edits` / `--no-allow-test-edits` | `--no-allow-test-edits` | Skip the diff gate's test-integrity checks. |
| `--run-id <str>` | — | Attaches gate container labels only. |
| `--config`, `-c <path>` | — | Config file. |

## `cosmo report`

Post-run triage: one run's `run_state` row plus its `run.summary` payload —
status, stop/pause reason, completed and blocked counts by reason, cost,
duration.

| Option | Default | Description |
| --- | --- | --- |
| `--run <str>` | most recently started run | Which run to render. |
| `--follow`, `-f` | off | Keep re-rendering until the run reaches a terminal status. |
| `--config`, `-c <path>` | — | Config file. |

---

## `cosmo config`

### `cosmo config show`

Print the resolved configuration.

| Option | Description |
| --- | --- |
| `--paths` | Show only where config, state, work and logs live. |
| `--config`, `-c <path>` | Config file. |

---

## `cosmo harness`

### `cosmo harness list`

List registered adapters and their declared capabilities. No options beyond
`--help`.

### `cosmo harness probe`

Smoke-test the resolved harness with a raw prompt. Applies an external
timeout from the orchestration layer, not inside the adapter.

| Option | Default | Description |
| --- | --- | --- |
| `--prompt <str>` | **required** | Raw prompt to send. |
| `--harness <str>` | resolved | Override the harness. |
| `--timeout <float>` | `timeouts.proposing_wall` | Seconds before cancelling. |
| `--config`, `-c <path>` | — | Config file. |

---

## `cosmo queue`

### `cosmo queue add SPEC_PATH`

Enqueue a hand-authored OpenSpec change.

| Argument | Description |
| --- | --- |
| `spec_path` | Path to the OpenSpec change, relative to the target repo. |

| Option | Default | Description |
| --- | --- | --- |
| `--task-id <str>` | the spec path's final component | Task identifier. |
| `--depends-on <str>` | — | A `task_id` this task depends on. Repeatable. |
| `--priority <int>` | `0` | Soft tie-breaker among simultaneously eligible tasks; higher runs first. Never overrides a dependency. |
| `--max-attempts <int>` | `retries.max_attempts` | Per-task retry budget. |
| `--allow-test-edits` / `--no-allow-test-edits` | `--no-allow-test-edits` | Bypass the test-path guard for this task. |
| `--config`, `-c <path>` | — | Config file. |

Dependency cycles are rejected at enqueue time.

### `cosmo queue ls`

List queued tasks: `task_id`, `status`, `attempts`, `depends_on`, `priority`,
`blocked_reason`, `spec_path`.

| Option | Description |
| --- | --- |
| `--status <str>` | Filter by status (see [status values](#task-status-values)). |
| `--config`, `-c <path>` | Config file. |

### `cosmo queue show TASK_ID`

Full `task_queue` row for one task: `spec_path`, `depends_on`, `priority`,
`status`, `attempt_count`, `max_attempts`, `last_error`, `blocked_reason`,
`allow_test_edits`, `worktree_path`, `session_id`, `created_at`,
`updated_at`, `spec_batch_id`, `resume_at_stage`.

| Option | Description |
| --- | --- |
| `--config`, `-c <path>` | Config file. |

### `cosmo queue failures TASK_ID`

Per-attempt failure history. The only CLI surface for `error_detail` — the
actual assertion text and stack excerpts from a gate failure. Event payloads
never carry it.

| Option | Description |
| --- | --- |
| `--run <str>` | Narrow to one `run_id`. |
| `--config`, `-c <path>` | Config file. |

### `cosmo queue retry TASK_ID`

Reset a `blocked` task to `queued`. `attempt_count` resets to 0.

If the worktree still holds the commit `PROPOSING` made, only the failed
implementation is discarded (`git reset --hard` to that commit, then `git
clean -fdx`) — the worktree and the valid OpenSpec change survive, so the
next run resumes at `IMPLEMENTING`. Otherwise the worktree and branch are
removed and the task starts over.

**Repeat-block guard**: a task whose most recent block repeats
`retries.repeat_block_threshold` prior blocks for the same reason is refused
rather than silently granted another attempt budget.

| Option | Default | Description |
| --- | --- | --- |
| `--repo <path>` | current directory | Target repo the worktree lives in. |
| `--force` | off | Proceed past the repeat-block guard. |
| `--config`, `-c <path>` | — | Config file. |

### `cosmo queue block TASK_ID`

Block a task by hand.

| Option | Description |
| --- | --- |
| `--reason <str>` | **required.** A `blocked_reason` value (see [below](#blocked-reason-values)). |
| `--config`, `-c <path>` | Config file. |

---

## `cosmo spec`

### `cosmo spec add NAME`

Enrich and decompose `docs/specs/<name>-spec.md` into
`docs/specs/<name>-spec/tasks/*.md`, then print a preview. **A preview only**
— it does not touch the queue or `openspec/`. The written files are real,
git-tracked content you can hand-edit.

If task files already exist, you are asked whether to re-run the harness (not
free) or reuse them.

| Argument | Description |
| --- | --- |
| `name` | Short kebab-case name for this spec. |

| Option | Default | Description |
| --- | --- | --- |
| `--repo <path>` | current directory | Target repo. |
| `--from <path>` | — | Copy this file in as `docs/specs/<name>-spec.md` if it doesn't exist yet. |
| `--harness <str>` | resolved | Override the harness. |
| `--timeout <float>` | `timeouts.proposing_wall` | Seconds before cancelling. |
| `--config`, `-c <path>` | — | Config file. |

### `cosmo spec queue NAME`

Insert one task per `docs/specs/<name>-spec/tasks/*.md` file into the queue,
tagged `spec_batch_id=<name>-spec`. Task ids and intra-batch `depends_on`
edges are namespaced as `<name>-<task_id>`. Re-running on an already-queued
batch is a no-op.

The edit window between `spec add` and this command is the confirmation step;
there is no separate approval UI.

| Argument | Description |
| --- | --- |
| `name` | The spec name a prior `cosmo spec add` produced. |

| Option | Default | Description |
| --- | --- | --- |
| `--repo <path>` | current directory | Target repo. |
| `--config`, `-c <path>` | — | Config file. |

---

## `cosmo events`

### `cosmo events tail`

Print recent events. The table carries `seq`, `timestamp`, `severity`,
`event_type`, `run_id`, `task_id`.

| Option | Default | Description |
| --- | --- | --- |
| `--run <str>` | — | Filter by `run_id`. |
| `--task <str>` | — | Filter by `task_id`. |
| `--severity <str>` | — | Filter by severity: `info`, `warning`, `error`, `critical`. |
| `--type <str>` | — | Filter by event type, e.g. `task.blocked`. |
| `--payload` | off | Print each event's full JSON payload beneath its row. |
| `--limit <int>` | `50` | Most recent N events. |
| `--follow`, `-f` | off | Keep polling for new events and print each as it lands. |
| `--config`, `-c <path>` | — | Config file. |

---

## `cosmo project`

### `cosmo project register TARGET_PATH`

Register a target repo so its harness can be resolved by path, without
running a full `cosmo init`.

| Option | Description |
| --- | --- |
| `--harness <str>` | Override the harness. |
| `--project-template <str>` | Project template used at bootstrap, if any. |
| `--config`, `-c <path>` | Config file. |

### `cosmo project list`

List registered projects.

| Option | Description |
| --- | --- |
| `--config`, `-c <path>` | Config file. |

---

## `cosmo templates`

### `cosmo templates list`

Names available under `templates/harness/` and `templates/projects/`. No
options beyond `--help`.

Shipped today: harness `claude`; project templates `_blank`,
`java-spring-react`, `vite-react-local`.

---

## `cosmo run`

Drive the task queue. With no `--task`, drains the whole DAG one task at a
time until the queue empties, the circuit breaker trips, a cost or quota
ceiling intervenes, or `timeouts.run_wall` expires.

| Option | Default | Description |
| --- | --- | --- |
| `--repo <path>` | current directory | Cosmo's own checkout of the target repo, kept on the base branch. |
| `--task <str>` | — | Drive only this one queued task instead of the full DAG. |
| `--base-branch <str>` | `git.base_branch` | Merge target. |
| `--harness <str>` | resolved | Override the harness. |
| `--dry-run` | off | Print the resolved execution order and exit. Ignored with `--task`. |
| `--config`, `-c <path>` | — | Config file. |

### `cosmo run resume [RUN_ID]`

Re-attach to an existing `PAUSED` run instead of starting a fresh one. Cost
accounting, the startup reconciliation sweep and the process lock all apply
exactly as for a fresh `cosmo run`.

| Argument | Default | Description |
| --- | --- | --- |
| `run_id` | most recently paused run | Run to resume. |

| Option | Default | Description |
| --- | --- | --- |
| `--repo <path>` | current directory | Target repo checkout. |
| `--harness <str>` | resolved | Override the harness. |
| `--yes` | off | Skip the confirmation prompt. |
| `--config`, `-c <path>` | — | Config file. |

---

## `cosmo notify`

### `cosmo notify config`

One-shot interactive setup for Telegram notifications: prompts for a bot
token, discovers the chat id automatically (walking you through messaging the
bot first, since bots can't message first), writes the `[notify]` table of
your user config file, and sends one real test message before declaring
success.

| Option | Description |
| --- | --- |
| `--config`, `-c <path>` | **Where to write.** Unlike every other command's read-only use of `--config`, a path that doesn't exist yet is this command's normal first-run case. |

### `cosmo notify watch`

The always-on watcher: polls the `events` table and forwards anything
notification-worthy to the configured sink. Refuses to start if
`notify.enabled` is false or credentials are missing. Runs as its own
long-running process (`deploy/cosmo-notify.service`), never inline in `cosmo
run` — a sink inside the run process cannot report the run process's own
crash.

| Option | Description |
| --- | --- |
| `--config`, `-c <path>` | Config file. |

---

## Enumerated values

### Task status values

Used by `queue ls --status` and reported by `queue show`.

`queued`, `proposing`, `proposed`, `implementing`, `validating`, `reviewing`,
`committing`, `merging`, `finishing`, `done`, `failed_retry`, `blocked`

### Blocked reason values

Used by `queue block --reason`.

`code_failure`, `cost`, `merge_conflict`, `environment`, `timeout`,
`flaky_unresolved`

### Failure type values

`code_error`, `environment_error`, `timeout`, `flaky`

### Failure stage values

`propose`, `implement`, `build`, `unit_tests`, `e2e_tests`,
`test_integrity`, `secrets`, `adversarial_review`, `commit`, `merge`

### Run status and stop/pause reasons

Run status: `idle`, `running`, `paused`, `stopped`

Stop reasons: `completed`, `max_time`, `queue_empty`, `cost_limit_reached`,
`manual`, `quota_exhausted_weekly`, `disk_low`, `crashed`,
`blocked_remaining`

Pause reasons: `circuit_breaker`, `quota_exhausted_5h`,
`quota_exhausted_weekly`

## Environment variables

| Variable | Read by | Effect |
| --- | --- | --- |
| `COSMO_CONFIG` | Cosmo | Path to the user config file, overriding the XDG default. |
| `XDG_CONFIG_HOME` | Cosmo | Config lives at `$XDG_CONFIG_HOME/cosmo/config.toml`. |
| `XDG_DATA_HOME` | Cosmo | Default `data_dir`/`work_dir`/`log_dir` derive from `$XDG_DATA_HOME/cosmo`. |
| `NOTIFY_SOCKET` | Cosmo | Set by systemd; enables `sd_notify` readiness and watchdog pings. |
| `ANTHROPIC_API_KEY` | Claude adapter | **Must be unset.** `cosmo doctor` fails on it, and the adapter scrubs it from the child process environment — it switches billing from subscription to per-token API rates. |
| `COSMO_TASK_ID`, `COSMO_DB_PATH` | guardrail hooks | Set by the Claude adapter on the child process so the `PreToolUse` hooks can read the running task's `allow_test_edits` flag. Not something you set yourself. |
