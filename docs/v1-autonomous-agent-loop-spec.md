# Autonomous Software Development Agent — v1 Loop Specification
 
## Status
Draft — v1 scope only. Telegram notifications, web dashboard, and multi-harness support beyond Claude Code CLI are explicitly out of scope for this version (see Non-Goals).
 
## Overview
This spec defines an unattended orchestrator that executes a queue of spec-driven development tasks (built on OpenSpec's `propose`/`apply` flow) without human supervision, running overnight or during the day on a DigitalOcean droplet or a local Windows PC (via WSL2). It replaces the developer's current manual loop of running `propose`/`apply` interactively.
 
The orchestrator is its own project/repository, separate from the product codebase(s) it operates on.
 
---
 
## 1. Environment & Stack
 
| Concern | Decision |
|---|---|
| Target codebase stack | Java + Spring Boot (backend), Vite + TypeScript + React + Tailwind (frontend), MariaDB / SQLite (data) |
| Repo layout | Monorepo for the product; orchestrator lives in its own separate repo |
| Containerization | Docker for the database always; Docker for the validation gate (build + unit tests + e2e); native execution for day-to-day build/dev |
| Windows compatibility | Entire loop runs inside WSL2 (not native Windows). Docker Desktop configured with WSL2 backend. Avoid bind-mounting the Windows filesystem directly (slow I/O) — keep the working repo inside the WSL2 filesystem |
| Process supervision | systemd, identical on the droplet (native Linux) and on the local PC (WSL2 supports systemd natively) |
| Orchestrator language | Python (subprocess management, `sqlite3` stdlib, `watchdog` for file watching) |
| Droplet sizing | 8 GB RAM recommended (4 GB minimum + swap), 2–4 vCPU, 50–80 GB SSD (accounts for `.m2`, `node_modules`, Docker image caches, and headless Chromium overhead from Playwright) |
| E2E testing | Playwright via MCP, headless Chromium. No desktop/UI environment needed on the droplet. Recommended: run the validation gate from the official `mcr.microsoft.com/playwright` Docker image (dependencies + browser preinstalled and versioned) rather than `playwright install-deps` on bare Ubuntu |
 
---
 
## 2. Harness Abstraction Layer
 
The loop never talks to a specific harness directly — it talks to an adapter implementing a common interface. This is what allows adding harnesses beyond Claude Code CLI later without touching the state machine.
 
### Interface methods
- `propose(spec_path, context)` → runs OpenSpec's propose step
- `implement(task_id, spec_path, retry_context=None)` → runs the apply/implementation step; `retry_context` carries the previous failure's `error_detail` when this is a retry
- `validate(task_id)` → triggers the validation gate (build + unit tests + e2e). May bypass the LLM harness entirely (direct Docker invocation)
- `get_progress(task_id)` → returns current progress (see §4)
- `cancel(task_id)` → forcibly terminates a running harness process (used on timeout)
### Uniform result object
Every adapter method returns:
- `success` (bool)
- `output_summary` (short text)
- `raw_log_path` (full log location, for manual debugging)
- `files_changed` (list)
- `duration_seconds`
- `total_cost_usd` (if reported natively; null otherwise)
- `exit_code`
### Declared capabilities per adapter
- `reports_native_progress` (bool) — falls back to file-watching `tasks.md` if false
- `supports_retry_context` (bool) — falls back to composing a synthetic prompt if false
- `has_internal_timeout` (bool) — loop imposes external timeout if false
- `reports_native_cost` (bool) — falls back to a token-based estimator, or disables the cost hard-stop for that adapter if unavailable
### v1 adapter: Claude Code CLI
- Invoked via `subprocess`, headless mode (`claude -p`, `--output-format json`)
- Authenticated via the Pro/Max subscription (no `ANTHROPIC_API_KEY` set in the environment for this process — setting it silently switches billing to per-token API rates)
- `total_cost_usd` read directly from the JSON output (`reports_native_cost: true`)
- Permission mode: `auto` (Claude Code's background-classifier mode — approves low-risk actions automatically, does not blanket-bypass all checks like `bypassPermissions`/`--dangerously-skip-permissions`). Configured via `defaultMode: "auto"` in `~/.claude/settings.json` on the host running the loop (this mode can only be set at the user-settings level, not per-project).
  - In headless mode there is no TTY to answer a prompt the classifier doesn't auto-approve; such cases are expected to fail the invocation rather than hang, and are classified as `environment_error` (see §6).
- Subject to the plan's usage window (see §7, quota handling) rather than per-token billing, as long as no API key is configured. As of this writing, `claude -p` usage still counts against the standard Pro/Max subscription limit (a prior announcement to split this into a separate credit pool was paused by Anthropic) — this is a point of external policy that could change and should not be hard-coded as a permanent assumption.
---
 
## 3. State Machines
 
### 3.1 Run-level (one nightly/daytime session)
```
IDLE → RUNNING → PAUSED (circuit breaker / quota exhausted) → STOPPED
```
- `RUNNING`: actively pulling tasks from the queue
- `PAUSED`: circuit breaker tripped, or quota window exhausted — process stays alive, resumes automatically (quota) or manually (circuit breaker) after review
- `STOPPED`: terminal — reached via `completed | max_time | queue_empty | cost_limit_reached | manual`
Each run has a unique `run_id`.
 
### 3.2 Task-level (one OpenSpec change)
```
QUEUED → PROPOSING → PROPOSED → IMPLEMENTING → VALIDATING → COMMITTING → DONE
                                      ↑____FAILED_RETRY____↓
                                      (max attempts exceeded → BLOCKED)
```
- Each task runs on its own branch: `task/<spec-id>`
- On `DONE`: the orchestrator merges `task/<spec-id>` into `develop` automatically (only if `VALIDATING` passed), then deletes/closes the task branch
- **Merging `develop` → `master` is always manual, performed by the developer. This is explicitly out of scope for the orchestrator.**
- `BLOCKED` branches are kept (not deleted) for manual review; re-queuing resets the attempt counter
- No mid-state resumption: if the process dies while a harness is running (`IMPLEMENTING`/`VALIDATING`), that state restarts from scratch on recovery — no attempt to reconstruct partial LLM context
- Each state has its own timeout (values TBD in a follow-up spec)
---
 
## 4. Progress Tracking
 
- Primary mechanism: watch `tasks.md` (the OpenSpec change's checklist) for checkbox transitions (`- [ ]` → `- [x]`), via `watchdog`/inotify (fallback: polling every 5–10s)
- Progress % = completed checkboxes / total checkboxes
- This is telemetry inside `IMPLEMENTING`, not a separate state
- Enables a finer-grained timeout: no new checkbox completed in N minutes → stalled-harness signal, independent of the state's overall timeout
- stdout parsing is a secondary/heartbeat signal only ("still alive"), never the primary source of progress truth
---
 
## 5. Task Queue
 
- Modeled as a **DAG**, not FIFO: tasks declare explicit `depends_on` (hard constraint on ordering), separate from `priority` (soft tie-breaker among tasks already eligible to run)
- Dependencies are explicit metadata on the change, never inferred from spec content
- Execution is strictly **serial** in v1 — no parallel harness runs against the same repo
- Same table backs both the queue and the run's persistent state (see §8): `task_id`, `spec_path`, `depends_on`, `priority`, `status`, `attempt_count`, `max_attempts`, `last_error`, `created_at`, `updated_at`
---
 
## 6. Failure Classification, Retries, Circuit Breaker
 
### Failure types
- `code_error` — build/test/e2e failure caused by the generated code. Counts toward the task's retry limit.
- `environment_error` — Docker unresponsive, MCP/Playwright connection failure, permission-mode denial, network timeout. Does **not** count toward the task's retry limit; feeds the global circuit breaker instead.
- `timeout` — state-specific or stalled-harness timeout reached.
### Per-task retries
- Max attempts: 2 (configurable). Third code-level failure → `BLOCKED`, not further retries.
- Retries are **informed**, not blind: the retry prompt includes the previous attempt's `error_detail` (see §9) plus a short summary of what was already tried, so the harness doesn't repeat the same failed approach.
- Small delay (30–60s) between retries — not for rate-limiting, but to let transient resource contention (e.g., Docker releasing resources from the previous attempt) settle.
### Global circuit breaker
- Trips to `PAUSED` when: N distinct tasks (not retries of the same task) land in `BLOCKED` consecutively, **or** repeated `environment_error`s occur across distinct tasks — both signal something in the environment is broken, not the specs themselves.
- `PAUSED` keeps the process and queue state intact; resuming requires manual intervention (or, for quota exhaustion specifically, automatic resumption once the window resets — see §7).
---
 
## 7. Quota & Cost Limits
 
Two distinct mechanisms, because they behave differently:
 
### Subscription usage windows (e.g., Claude Pro/Max)
- No dollar cost, but a hard cap that resets on a timer (5-hour rolling window for Pro/Max)
- On exhaustion: `quota_exhausted` event → run transitions to `PAUSED`, does **not** count toward the circuit breaker's failure tally, resumes automatically once the window resets
### Dollar-denominated cost limits (harnesses billed per token, e.g., a future Cursor/Cline/API-key adapter)
- `max_cost_per_run_usd` — hard ceiling for the entire session
- `max_cost_per_task_usd` — prevents a single task's retries from consuming the whole run's budget before the run-level check catches it
- Warning event at 80% of `max_cost_per_run_usd` (useful once a notification channel exists)
- On reaching `max_cost_per_run_usd`: run transitions to `STOPPED` (not `PAUSED` — nothing to auto-recover from; requires a deliberate decision to raise the budget or wait)
- On a single task exceeding `max_cost_per_task_usd`: that task moves to `BLOCKED` with a cost-related reason; the rest of the queue continues
- Cost is read from `total_cost_usd` when the adapter reports it natively (Claude Code CLI's `--output-format json` does); otherwise estimated from token counts, or the cost hard-stop is disabled for that adapter and flagged as unsupported in config
- For the v1 Claude Code CLI adapter, cost limits are effectively inert (governed by the subscription window instead) but the config fields exist so the same mechanism applies unchanged when a cost-billed adapter is added later
---
 
## 8. Persistent State
 
- SQLite, local to the orchestrator's own repo/data directory
- Two kinds of tables, treated differently:
  - **Historical / append-only**: task transitions, failures, completions — full audit trail of the run, never overwritten
  - **Current-state / UPSERT**: progress %, heartbeat timestamp, accumulated run cost, accumulated per-task cost — one row per entity reflecting the latest known value, not one row per tick (avoids flooding the DB from high-frequency file-watching)
---
 
## 9. Event Schema
 
### Common envelope
`event_id`, `run_id`, `task_id` (nullable for run-level events), `timestamp`, `sequence` (monotonic within the run, for ordering when timestamps collide), `event_type`, `severity` (`info | warning | error | critical`), `payload`
 
### Run-level event types
- `run.started` — harness used, limits configured (max tasks/time/cost), base branch
- `run.paused` — reason (`circuit_breaker | quota_exhausted`), triggering task, consecutive-failure count
- `run.resumed`
- `run.stopped` — reason (`completed | max_time | queue_empty | cost_limit_reached | manual`)
- `run.summary` — totals at close: completed, blocked, retried, total duration, total cost
### Task-level event types
- `task.state_changed` — `from_state`, `to_state`, `attempt_number`
- `task.failed` — see payload spec below
- `task.blocked` — reason, attempts exhausted (or cost/quota reason)
- `task.completed` — duration, files changed, commit hash, merge target (`develop`)
- `task.validation_result` — unit test and e2e results reported **separately** (pass/fail counts each, not one combined boolean)
- `task.progress` — percent, subtasks completed/total, label of last completed subtask (UPSERT)
- `task.heartbeat` — last-activity timestamp, current state, time elapsed in that state (UPSERT)
### `task.failed` payload detail
- `failure_type`: `code_error | environment_error | timeout`
- `failure_stage`: `propose | implement | build | unit_tests | e2e_tests | commit`
- `error_summary`: 1–2 lines, human-readable
- `error_detail`: **actionable** content for the retry prompt — failing test name + assertion + trimmed stack trace; failing build error; failing Playwright step + trace/screenshot path (path only, not embedded binary). Never a full raw log dump.
- `files_touched`: files modified in the failed attempt
- `attempt_number`, `previous_attempts_summary`: short summary of prior attempts, so the retry prompt can say "X was already tried and failed because Y"
- `will_retry` (bool), `next_action`: `retry | block | escalate_circuit_breaker`
### Persistence note
Transition events are inserted (append-only history). Telemetry events (`progress`, `heartbeat`) are upserted (current-state only). `severity` is the field a future notification channel would filter on (`warning`+).
 
---
 
## 10. Spec & Knowledge Management (replaces Engram for this loop)
 
- Architecture/domain knowledge lives in topic-scoped markdown files, versioned in git: `backend-arch.md`, `frontend-arch.md`, `data-model.md`, `e2e-validation.md`
- Updated **by event**, not on a schedule: as part of the `COMMITTING` step, if a completed task introduced a decision that constrains future work, the harness appends 2–3 lines to the relevant file. Narration of "what happened" (not decision-relevant) does not belong here — it's already in git history and the event log.
- `decisions-log.md` — short ADR-style log (decision + date + originating task) for "why is it like this" lookups later
- No retrieval-based memory system (e.g., Engram) in the autonomous loop: retry context and cross-task continuity come from the structured event log and state store (deterministic, queryable), not from semantic retrieval, which is a worse fit for unattended overnight execution.
---
 
## 11. Non-Goals (v1)
 
- Telegram or any real-time notification channel
- Web dashboard
- Any harness other than Claude Code CLI
- Parallel task execution
- Automatic merge into `master`
- Resuming partial in-flight harness work after a crash
## Open Items for Follow-Up Specs
1. Concrete per-state timeout values and exact behavior on expiry
2. Full adapter configuration schema (required vs. optional fields per declared capability)
3. SQLite schema definition and the Claude Code CLI adapter implementation
 