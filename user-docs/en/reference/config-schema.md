# Configuration reference

Every key Cosmo reads, with its shipped default.

## Where configuration comes from

Three layers, lowest precedence first:

1. `src/cosmo/config/defaults.toml`, shipped in the package.
2. A user config file: `$COSMO_CONFIG` if set, otherwise
   `$XDG_CONFIG_HOME/cosmo/config.toml` (i.e. `~/.config/cosmo/config.toml`).
3. Explicit overrides passed by the CLI (`--config` points at a different
   file for layer 2; it does not add a fourth layer).

Layering is a deep merge per table, so a user file only needs the keys it
changes.

```bash
cosmo config show          # the fully resolved configuration
cosmo config show --paths  # just where config, state, work and logs live
```

Validation happens at load time. A bad value fails the command immediately
(exit code `2`) rather than mid-run at 3am. Unknown keys are rejected — the
model forbids extras, so a typo'd key is an error, not a silent no-op.

---

## `[harness]`

The only place in Cosmo's core that names a specific harness.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `name` | string | `"claude"` | Which adapter to use. Resolution order: `--harness` flag → project registration → this. |
| `permission_mode` | string | `"dontAsk"` | Permission posture passed to the harness. The Claude adapter accepts `dontAsk` or `auto`, and refuses `bypassPermissions` outright. |
| `max_turns` | int > 0 | `80` | Turn ceiling per harness call. |
| `model` | string | `"claude-sonnet-5"` | Pinned so a run's model doesn't drift with whatever the host CLI defaults to. |

## `[timeouts]`

All values in seconds, all must be > 0.

| Key | Default | Description |
| --- | --- | --- |
| `proposing_wall` | `900` | Wall clock for the propose call. Also the default `--timeout` for `harness probe` and `spec add`. |
| `implementing_wall` | `5400` | Wall clock for the implement call. |
| `implementing_stall` | `1200` | No observed activity for this long during `IMPLEMENTING` kills the call. |
| `validating_wall` | `2700` | Wall clock for `VALIDATING`. |
| `validating_stall` | `600` | Stall timer for `VALIDATING`. |
| `reviewing_wall` | `900` | Wall clock for the adversarial review call. One bounded call, so no stall variant. |
| `committing_wall` | `300` | Wall clock for `COMMITTING`. |
| `merging_wall` | `300` | Wall clock for `MERGING`. |
| `run_wall` | `36000` | Whole-run wall clock (10 hours). Expiry stops the run with `max_time`. |
| `kill_grace` | `20` | Seconds between `SIGTERM` and `SIGKILL` on the process group. |

**Validated**: `implementing_stall` must be less than `implementing_wall`,
and `validating_stall` less than `validating_wall`. A stall timer that
outlives its wall clock can never fire, silently disabling the only
protection against a hung harness.

## `[retries]`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `max_attempts` | int > 0 | `2` | Code-level attempts before a task blocks. With the default, the third code-level failure blocks. |
| `delay_min` | int ≥ 0 | `30` | Lower bound of the randomized delay between retries, in seconds. |
| `delay_max` | int ≥ 0 | `60` | Upper bound. |
| `repeat_block_threshold` | int > 0 | `2` | `cosmo queue retry` refuses once the task's latest terminal block matches this many prior blocks for the same reason. `--force` overrides. |

**Validated**: `delay_min` must not exceed `delay_max`.

Only attempts that represent a genuine code-level judgment increment the
counter — a gate verdict of `code_error` or `test_integrity`, or an
`IMPLEMENTING` timeout. An `environment_error` never does.

## `[circuit_breaker]`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `consecutive_blocked_threshold` | int > 0 | `3` | Distinct tasks blocking consecutively before the run pauses. A `DONE` task resets the streak. |
| `environment_error_threshold` | int > 0 | `3` | Accumulated environment-error weight across distinct tasks before the run pauses. |
| `reap_failure_weight` | int > 0 | `2` | Weight a process-reap failure contributes. A leaked process pool poisons every later task, so it trips the breaker faster. |

`merge_conflict` and `flaky_unresolved` blocks are excluded from the
consecutive tally entirely — they signal queue contention over shared files,
not a broken environment.

## `[cost]`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `max_cost_per_run_usd` | float ≥ 0 | `0.0` | Hard stop for one run. `0.0` means no hard stop — the right posture for a subscription-billed harness. |
| `max_cost_per_task_usd` | float ≥ 0 | `0.0` | Hard stop per task. `0.0` disables it. |
| `warn_at_fraction` | float in (0, 1] | `0.8` | Fraction of `max_cost_per_run_usd` at which a `run.cost_warning` event fires. |

A task blocked on `cost` is re-evaluated against the *current* ceiling at the
next run's startup and unblocked automatically if a human raised or disabled
it in between.

## `[gate]`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `playwright_image` | string | `"mcr.microsoft.com/playwright:v1.49.0-noble"` | E2E stage image. |
| `playwright_npm_version` | string | `"1.49.0"` | The `@playwright/test` version your repo must pin to match the image's browser binaries. |
| `shm_size` | string | `"2gb"` | `--shm-size` on every gate container. |
| `ipc_host` | bool | `true` | `--ipc=host` on every gate container. |
| `backend_image` | string | `"maven:3.9.9-eclipse-temurin-21"` | Backend build/test image. |
| `backend_dir` | string | `"backend"` | Backend directory, relative to the worktree root. If absent, backend stages are skipped. |
| `frontend_image` | string | `"node:24.19-bookworm"` | Frontend build/test image. |
| `frontend_dir` | string | `"frontend"` | Frontend directory, relative to the worktree root. If absent, the e2e stage is skipped. |
| `stage_timeout_seconds` | int > 0 | `1800` | Docker-run budget per serial stage (build, unit, e2e). Distinct from `timeouts.validating_wall`. |
| `diff_gate_test_path_patterns` | list[string], non-empty | see below | Glob patterns identifying test files in the diff. |
| `diff_gate_skip_annotations` | list[string], non-empty | see below | Substrings that mark a test as skipped or disabled. |
| `diff_gate_loc_drop_threshold` | int > 0 | `20` | Net lines removed from a test file before the diff gate flags it. |
| `flaky_rerun_limit` | int > 0 | `3` | Isolated reruns of a failing non-quarantined e2e test before calling it a real failure. |
| `flaky_quarantine_candidate_threshold` | int > 0 | `3` | Flaky classifications across *distinct runs* before a test is appended to the candidates file for human review. |
| `quarantine_file` | path or null | `null` | Path to `quarantine.yml`. `null` uses the copy bundled with Cosmo. |
| `quarantine_candidates_file` | path or null | `null` | Path to `quarantine-candidates.yml`. `null` uses the bundled copy. |
| `error_detail_max_chars` | int > 0 | `4000` | Cap on stored `error_detail`, so it stays model-consumable rather than archival. |

**Validated**: `playwright_image` must be pinned to an explicit tag. A
`:latest` tag, or no tag at all, is rejected — a silent upstream update turns
a green suite red overnight and surfaces as a phantom regression the agent
will try to "fix".

Defaults for the two list keys:

```toml
diff_gate_test_path_patterns = [
    "**/src/test/**",
    "**/*.test.*",
    "**/*.spec.*",
    "**/e2e/**",
]
diff_gate_skip_annotations = [
    "@Disabled", "@Ignore", ".skip(", ".only(",
    "xit(", "xdescribe(", "test.skip(", "describe.skip(",
]
```

## `[knowledge]`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `max_file_lines` | int > 0 | `400` | Line cap on every `docs/**/*.md` knowledge file a task touches. Exceeding it fails `COMMITTING` and loops back to `IMPLEMENTING`. Compaction is never automated. |

## `[review]`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `enabled` | bool | `true` | The fresh-session adversarial review between `VALIDATING` and `COMMITTING`. `false` skips `REVIEWING` entirely. A rejected review retries against `retries.max_attempts`, not a separate budget. |

## `[progress]`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `poll_interval_seconds` | int > 0 | `7` | Interval for watching the change's `tasks.md` (and for native-progress polling on an adapter that reports it). |

## `[quota]`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `result_error_subtypes` | list[string], non-empty | `["error_rate_limit"]` | Terminal result error subtypes treated as quota exhaustion (secondary detection). |
| `heuristic_consecutive_threshold` | int > 0 | `3` | Consecutive distinct tasks failing near-instantly with zero tool calls before the wall-clock heuristic fires. |
| `heuristic_max_duration_seconds` | float > 0 | `5.0` | What counts as "near-instantly". |
| `default_5h_resume_delay_seconds` | int > 0 | `18000` | Resume delay when a confirmed five-hour signal carries no reset time. |
| `bypass_5h_with_credits` | bool | `false` | Don't pause on a confirmed five-hour signal — keep spending usage credits past the included allowance. |

**Validated**: `bypass_5h_with_credits = true` requires a non-zero
`cost.max_cost_per_run_usd`. Cosmo refuses to start otherwise — the bypass
must not exist without the spend ceiling it creates the need for.

The wall-clock heuristic is never reported as a confirmed signal, and is
always treated as the shorter, safer five-hour window; there is no way to
infer a weekly window from timing alone.

## `[notify]`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `enabled` | bool | `false` | Master switch. `cosmo notify watch` refuses to start when false. |
| `telegram_bot_token` | string or null | `null` | Bot token. Keep it in the user config file, `chmod 600`, outside any repo. |
| `telegram_chat_id` | string or null | `null` | Destination chat. |
| `min_severity` | `info` \| `warning` \| `error` \| `critical` | `"warning"` | Severity floor for forwarding. `task.completed` is always notified regardless. |
| `stale_after_seconds` | int > 0 | `1800` | No new run-level activity for this long, with the run not in a terminal status, is itself treated as a crash signal. |

`cosmo notify config` writes this table for you and sends a real test message
before declaring success.

## `[disk]`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `min_free_gb` | float > 0 | `10.0` | Checked before every run starts. Below it, the run aborts with `disk_low` rather than failing every task partway through. |

## `[log_retention]`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `done_days` | int > 0 | `7` | How long a `DONE` task's raw harness logs are kept. |
| `blocked_days` | int > 0 | `30` | How long a `BLOCKED` task's logs are kept. |

Keyed off the task's *current* status, not the status at the time each file
was written — a task that later reaches `DONE` has its older attempts' logs
age out on the shorter window.

## `[git]`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `base_branch` | string | `"develop"` | The **target repo's** integration branch, and the only branch Cosmo ever merges to automatically. Unrelated to Cosmo's own repo branch. Merging to `main`/`master` is always manual. |
| `commit_author_name` | string | `"Cosmo"` | Identity for commits Cosmo makes itself (merge ladder, decisions-log). Also `cosmo init`'s default local identity for a target repo with none configured. |
| `commit_author_email` | string | `"cosmo@entropiainversa.com"` | See above. |
| `unified_identity` | bool | `false` | `false`: Cosmo's own commits use the identity above, visibly distinct from the application-code commits. `true`: Cosmo's own commits inherit the repo's local git config — one identity for every commit. |

Cosmo passes its identity per invocation (`-c user.name=...`), never writing
to global git config.

## `[paths]`

Defaults are computed at load time from the XDG layout, not shipped in
`defaults.toml`, because they depend on the host.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `data_dir` | path | `$XDG_DATA_HOME/cosmo` (i.e. `~/.local/share/cosmo`) | State root. The SQLite database lives at `<data_dir>/cosmo.db`. |
| `work_dir` | path | `<data_dir>/work` | Where task worktrees are created, as `<work_dir>/<run_id>/<task_id>`. |
| `log_dir` | path | `<data_dir>/logs` | Raw harness logs, rotated per `[log_retention]`. |

The database path is derived from `data_dir` and is not separately
configurable.

A server deployment typically points all three at something like
`/var/cosmo`:

```toml
[paths]
data_dir = "/var/cosmo"
work_dir = "/var/cosmo/work"
log_dir  = "/var/cosmo/logs"
```

Under WSL2, keep `work_dir` off `/mnt/c`. `cosmo doctor` warns about it:
builds on the 9p bridge are slow enough to distort every timeout above.

---

## A minimal user config

```toml
# ~/.config/cosmo/config.toml

[git]
base_branch = "develop"

[cost]
max_cost_per_run_usd = 25.0

[notify]
enabled = true
telegram_bot_token = "..."
telegram_chat_id = "..."
min_severity = "info"
```

```bash
chmod 600 ~/.config/cosmo/config.toml
```
