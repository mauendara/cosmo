# Event schema reference

Every event Cosmo writes, its envelope, and its payload.

Events live in the `events` table of `<data_dir>/cosmo.db` and are read with
`cosmo events tail` (add `--payload` for the JSON body). The log is
append-only.

## Envelope

Every row carries these columns, regardless of type:

| Field | Type | Description |
| --- | --- | --- |
| `event_id` | string | Primary key. |
| `run_id` | string or null | Null for events outside a run (e.g. `agent_assets.synced` at `cosmo init` time). |
| `task_id` | string or null | Null for run-level events. |
| `timestamp` | string | ISO 8601 with timezone offset, e.g. `2026-08-29T01:54:20.259+00:00`. |
| `sequence` | int | Monotonic within a scope — the `run_id`, or `''` for run-less events. Written in the same transaction as the event, so ordering survives a crash. |
| `event_type` | string | One of the types below. |
| `severity` | `info` \| `warning` \| `error` \| `critical` | |
| `schema_version` | int | Currently `1`. Present from day one so the table can migrate without archaeology. |
| `payload` | JSON object | Type-specific; documented per type below. |

---

## Run-level events

### `run.started` — `info`

| Field | Type |
| --- | --- |
| `harness` | string |
| `permission_mode` | string |
| `max_turns` | int |
| `base_branch` | string |
| `run_wall_seconds` | int |
| `max_cost_per_run_usd` | float |

### `run.paused` — `warning`

Emitted for both pause causes; the fields present differ.

| Field | Type | Notes |
| --- | --- | --- |
| `reason` | string or null | A pause reason: `circuit_breaker`, `quota_exhausted_5h`, `quota_exhausted_weekly`. |
| `triggering_task` | string | Circuit-breaker pauses only. |
| `resume_delay_seconds` | number | Quota pauses only — seconds until auto-resume. |
| `confirmed` | bool | Quota pauses only. `false` means the wall-clock heuristic fired, not a real wire signal. |

### `run.resumed` — `info`

Empty payload.

### `run.stopped` — severity varies

`info` normally; `critical` for a disk abort or a startup DAG cycle.

| Field | Type | Notes |
| --- | --- | --- |
| `reason` | string or null | A stop reason (see below). |
| `detail` | string | Present on `disk_low` — the disk check's own message. |
| `error` | string | Present when a dependency cycle stopped the run. |

Stop reasons: `completed`, `max_time`, `queue_empty`, `cost_limit_reached`,
`manual`, `quota_exhausted_weekly`, `disk_low`, `crashed`,
`blocked_remaining`.

`blocked_remaining` is chosen instead of `queue_empty` whenever at least one
task actually blocked during the run, so a run that finished only because
everything is stuck is never reported as a success.

### `run.summary` — `info`

The payload `cosmo report` renders.

| Field | Type |
| --- | --- |
| `completed` | int |
| `blocked` | int |
| `blocked_by_reason` | object — blocked reason → count |
| `requeued` | int |
| `retried` | int |
| `flaky_detected` | list[string] — test ids |
| `repeated_merge_conflict_tasks` | list[string] |
| `knowledge_files_near_cap` | list[string] |
| `stalled_queued_tasks` | list[string] — queued but unschedulable (unmet dependencies) |
| `total_duration_seconds` | number |
| `total_cost_usd` | number |

### `run.cost_warning` — `warning`

Fires once `cost.warn_at_fraction` of `cost.max_cost_per_run_usd` is reached.

| Field | Type |
| --- | --- |
| `total_cost_usd` | float |
| `limit_usd` | float |

### `quota.bypassed` — `warning`

A confirmed five-hour quota signal was *not* paused on, because
`quota.bypass_5h_with_credits` is set. The operator has opted in to spending
real usage credits past the included allowance.

| Field | Type |
| --- | --- |
| `resets_at` | string or null — UTC ISO 8601 |
| `run_cost_so_far_usd` | float |

---

## Task-level events

### `task.state_changed` — `info`

Emitted on an actual state transition only, never on heartbeats.

| Field | Type |
| --- | --- |
| `from_state` | string or null |
| `to_state` | string |
| `attempt_number` | int |

State values: `queued`, `proposing`, `proposed`, `implementing`,
`validating`, `reviewing`, `committing`, `merging`, `finishing`, `done`,
`failed_retry`, `blocked`.

### `task.validation_result` — `info` when passed, `warning` when not

The gate's verdict.

| Field | Type |
| --- | --- |
| `passed` | bool |
| `duration_seconds` | number |
| `unit` | stage object or null |
| `e2e` | stage object or null |
| `flaky_detected` | list[string] — test ids reclassified as flaky |
| `quarantined_skipped` | list[string] — test ids excluded by the quarantine list |

Each stage object:

| Field | Type |
| --- | --- |
| `passed` | bool |
| `duration_seconds` | number |
| `passed_count` | int or null |
| `failed_count` | int or null |
| `skipped_count` | int or null |
| `failing_tests` | list[string] — test ids |

**Failing tests are named here; their assertion text is not.** That detail
lives only in the `task_failures` table, reachable via
`cosmo queue failures <task_id>`.

### `task.completed` — `info`

| Field | Type |
| --- | --- |
| `rebase_attempted` | bool — whether the merge ladder needed its rebase step |

Always notified regardless of `notify.min_severity`.

### `task.blocked` — `warning`

| Field | Type |
| --- | --- |
| `blocked_reason` | one of `code_failure`, `cost`, `merge_conflict`, `environment`, `timeout`, `flaky_unresolved` |
| `note` | string or null — free-text context, when the blocking site has any |
| `rebase_attempted` | bool — merge-ladder blocks only |

### `task.failed` — `critical`

**Emitted only by the process-reap path.** An ordinary task failure is
recorded in the `task_failures` table and surfaced through
`cosmo queue failures`, not through this event.

| Field | Type |
| --- | --- |
| `failure_type` | `environment_error` |
| `error_detail` | string |
| `circuit_breaker_weight` | int — `circuit_breaker.reap_failure_weight` |
| `containers_removed` | list |
| `worktree_holder_pids` | list[int] |

### `task.progress` — `info`

Read from the change's `tasks.md`, not from anything the agent asserts.

| Field | Type |
| --- | --- |
| `completed` | int |
| `total` | int |
| `last_label` | string or null |

Numerator and denominator are stored separately, never a precomputed
percentage: the total is not constant, and progress can legitimately move
backwards.

### `task.heartbeat` — `info`

| Field | Type |
| --- | --- |
| `state` | string — the task state being observed |
| `source` | `stream` \| `file` \| `mtime` |

### `task.interrupted` — `warning`

A task found mid-flight by the startup reconciliation sweep, because the
process driving it crashed or was killed. Emitted once per reconciled task,
before it is requeued.

| Field | Type |
| --- | --- |
| `previous_status` | string |

### `task.cost_requeued` — `info`

A task blocked on `cost` is no longer over the *current*
`max_cost_per_task_usd` — a human raised or disabled the ceiling between
runs — so the block was cleared. Nothing failed here.

| Field | Type |
| --- | --- |
| `task_cost_usd` | float |

### `task.finishing_failed` — `warning`

`FINISHING`'s best-effort `openspec archive` step failed. Always a warning:
`FINISHING` never blocks a task that already merged successfully. This is an
observability signal for post-run review, nothing more.

| Field | Type |
| --- | --- |
| `spec_id` | string |
| `error` | string |

### `task.guardrail_tripped`

**Declared but not emitted.** The type exists in the event enum; no code path
writes it today. Guardrail denials currently surface in the harness session's
own log and in the resulting failure, not as an event of this type. Don't
build alerting on it.

---

## Project-level events

### `agent_assets.synced` — `info`

The harness's operating policy, agents, skills and hooks were copied into a
target repo or worktree. `run_id` is null when this happens at `cosmo init`
time.

| Field | Type |
| --- | --- |
| `harness` | string |
| `template_version` | string — content hash of the synced template tree |
| `target_path` | string |

---

## Synthetic events

### `watch.stale`

Not a stored event type. `cosmo notify watch` constructs this in memory and
forwards it when the `events` table has gone quiet for
`notify.stale_after_seconds` while the run is not in a terminal status — the
one signal that can report the run loop's own death.

| Field | Type |
| --- | --- |
| `stale_after_seconds` | int |

---

## Related tables

The event log is not the only record. These are queryable in the same SQLite
database and hold detail events deliberately don't carry:

| Table | Contents | CLI surface |
| --- | --- | --- |
| `task_queue` | current state of every task | `cosmo queue ls` / `show` |
| `task_failures` | per-attempt `failure_type`, `failure_stage`, `error_summary`, **`error_detail`**, `files_touched`, `will_retry`, `next_action`, `failure_signature` | `cosmo queue failures` |
| `task_transitions` | append-only state-change trail | — |
| `run_state`, `run_cost`, `task_cost` | run status and spend | `cosmo report` |
| `task_progress`, `task_heartbeat` | latest progress and liveness, one row per task | — |
| `projects` | registered target repos | `cosmo project list` |
