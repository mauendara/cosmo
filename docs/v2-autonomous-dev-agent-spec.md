# Autonomous Software Development Agent — v2 Loop Specification
 
## Status
Draft v2. Supersedes v1. Incorporates the 13 approved recommendations from the state-of-the-art review (5 must-fix, 8 should-add) plus three resolved design decisions. Telegram notifications, web dashboard, parallel execution, and multi-harness support beyond Claude Code CLI remain out of scope (see §11).
 
### Changes from v1
| # | Change | Section |
|---|---|---|
| 1 | Test-gaming guardrail (hooks + diff gate) | §6.1, §2.5 (new) |
| 2 | Process-group kill semantics in adapter contract | §2.4 (new) |
| 3 | Flaky-test handling: quarantine + confirm-by-rerun | §6.4 (new), §1 |
| 4 | SQLite pragmas (WAL, busy_timeout, synchronous) | §8.1 (new) |
| 5 | Concrete per-state timeout values | §3.3 (new) |
| 6 | `git worktree` per task, replacing branch-switching | §3.2 |
| 7 | `stream-json` as liveness/heartbeat/rate-limit channel | §4 |
| 8 | Native OpenTelemetry export enabled | §9.4 (new) |
| 9 | Merge-conflict policy | §3.4 (new) |
| 10 | Secret handling (deny rules, gitleaks, no-verify block) | §2.5, §6.1 |
| 11 | Permission mode config-selectable, per-invocation | §2.3 |
| 12 | Weekly-cap branch in quota logic | §7.1 |
| 13 | `blocked_reason` enum column | §5 |
 
### Resolved decisions
- **Permission mode:** both `auto` and `dontAsk` are specified and selectable via config; set per-invocation via the `--permission-mode` CLI flag, never via `defaultMode` in `settings.json`.
- **Adapter invocation:** raw `subprocess`, not the Claude Agent SDK. Rationale in §2.1.
- **Validation gate baseline:** 5–15 minutes (build + unit + e2e). All timeout values in §3.3 are anchored to a 15-minute p95 gate.
---
 
## Overview
An unattended orchestrator that executes a queue of spec-driven development tasks (built on OpenSpec's `propose`/`apply` flow) without human supervision, running overnight or during the day on a DigitalOcean droplet or a local Windows PC (via WSL2). It replaces the developer's current manual loop of running `propose`/`apply` interactively.
 
The orchestrator is its own project/repository, separate from the product codebase(s) it operates on.
 
**Core epistemic principle (new in v2):** the validation gate is the only source of truth about correctness. Every other signal — checkbox transitions, harness self-reports, stdout, exit codes — is *liveness* telemetry and is assumed gameable or lossy. No task reaches `DONE` on the strength of the agent's own claim to have finished.
 
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
| E2E testing | Playwright via MCP, headless Chromium, run from the official `mcr.microsoft.com/playwright` Docker image |
 
### 1.1 Validation gate container requirements (hardened in v2)
 
The gate container **must** be started with:
- `--ipc=host` — Playwright's own documentation states this is recommended for Chromium; without it Chromium can exhaust memory and crash.
- `--shm-size=2gb` — Docker's default `/dev/shm` is 64 MB, which is too small for Chromium. Renderer crashes from an undersized shm **present as flaky tests**, which in an unattended loop are misclassified as `code_error` and burn the task's retry budget. This flag removes the single most common source of false failures.
**Version pinning is atomic.** The Playwright npm version, the `mcr.microsoft.com/playwright` image tag, the browser binaries, and any CI cache key are treated as one unit that always bumps together. A mismatch between image tag and npm version leaves Playwright unable to locate browser executables. Never use `latest` — a silent upstream update can turn a green suite red overnight, and in this loop that surfaces as a phantom regression the agent will attempt to "fix."
 
### 1.2 Gate execution ordering
 
The gate runs **serially**: build → unit tests → e2e. On 8 GB, running Maven, Node/Vite, headless Chromium, and MariaDB concurrently invites the OOM killer. Serial execution also gives clean `failure_stage` attribution (§9.3) — a concurrent gate cannot cleanly say whether the build or the e2e run caused the failure.
 
### 1.3 Integration test layer
 
Spring Boot + MariaDB integration tests **may** use Testcontainers instead of the long-lived Docker DB. Tradeoff: correct wiring and disposable isolation per run, at the cost of additional memory pressure and a second container lifecycle for the orchestrator to supervise on timeout/kill. Decide per-repo; the orchestrator must not assume either.
 
---
 
## 2. Harness Abstraction Layer
 
The loop never talks to a specific harness directly — it talks to an adapter implementing a common interface. This is what allows adding harnesses beyond Claude Code CLI later without touching the state machine.
 
### 2.1 Invocation strategy: raw subprocess (decided)
 
The v1 adapter invokes the Claude Code CLI via Python `subprocess`, **not** via the Claude Agent SDK.
 
Rationale: the SDK wraps the same CLI as a subprocess and offers structured message streaming and hook callbacks, but this design's reliability depends on precise control over process groups, signal escalation, and orphan reaping (§2.4) — control that is direct and auditable with raw `subprocess` and indirect through an SDK layer. The SDK also has documented environment-inheritance quirks when nested inside a Claude Code session (`CLAUDECODE=1`). The parsing convenience the SDK provides is replaced by a thin NDJSON reader over `--output-format stream-json`, which we need anyway for the liveness channel (§4).
 
This decision is revisited if a future adapter needs in-process MCP tools.
 
### 2.2 Interface methods
- `propose(spec_path, context)` → runs OpenSpec's propose step
- `implement(task_id, spec_path, retry_context=None)` → runs the apply/implementation step; `retry_context` carries the previous failure's `error_detail`
- `validate(task_id)` → triggers the validation gate (build + unit tests + e2e). Bypasses the LLM harness entirely (direct Docker invocation)
- `get_progress(task_id)` → returns current progress (see §4)
- `cancel(task_id)` → forcibly terminates a running harness process **and its entire process group** (see §2.4)
### Uniform result object
Every adapter method returns:
- `success` (bool)
- `output_summary` (short text)
- `raw_log_path` (full log location, for manual debugging)
- `files_changed` (list)
- `duration_seconds`
- `total_cost_usd` (if reported natively; null otherwise)
- `exit_code`
- `session_id` (new — from the harness's structured output, for replay/audit; nullable)
### Declared capabilities per adapter
- `reports_native_progress` (bool) — falls back to file-watching `tasks.md` if false
- `supports_retry_context` (bool) — falls back to composing a synthetic prompt if false
- `has_internal_timeout` (bool) — loop imposes external timeout if false
- `reports_native_cost` (bool) — falls back to a token-based estimator, or disables the cost hard-stop for that adapter
- `supports_gating` (bool, **new**) — whether the harness can enforce protected-path and test-file guardrails *before* a tool call executes (Claude Code: true, via `PreToolUse` hooks). If false, the orchestrator falls back to post-hoc diff inspection in the validation gate (§6.1), which is strictly weaker because the edit has already happened.
- `supports_structured_stream` (bool, **new**) — whether the harness emits a parseable event stream usable as a liveness and rate-limit signal (Claude Code: true, via `stream-json`).
### 2.3 v1 adapter: Claude Code CLI
 
**Invocation.** `claude -p` in headless mode with `--output-format stream-json --verbose`. The stream is consumed line-by-line as NDJSON; the terminal `result` object carries `total_cost_usd`, `duration_ms`, `num_turns`, and `session_id`.
 
**Authentication.** Pro/Max subscription. `ANTHROPIC_API_KEY` **must not** be set in this process's environment — setting it silently switches billing to per-token API rates. The adapter explicitly scrubs it from the child environment rather than relying on it being absent.
 
**Turn cap.** `--max-turns` is always set, paired with the orchestrator's own wall-clock timeout (§3.3). A run with neither can spin until it exhausts an external limit while consuming quota the whole time.
 
**Permission mode — config-selectable (decided).** Set per-invocation via the `--permission-mode` flag, which works with `-p`. Two supported values:
 
| Mode | Behavior | When to use |
|---|---|---|
| `dontAsk` | Auto-**denies** every tool call that would otherwise prompt. Only calls matching `permissions.allow` rules and read-only Bash commands execute; explicit `ask` rules are denied rather than prompted. Fully non-interactive. | **Default for this loop.** Fails closed and fails loud — an unlisted action produces a clean non-zero exit classified as `environment_error`, not a silent approval. |
| `auto` | Auto-approves with background safety checks; a classifier model evaluates each tool call in context and blocks escalation beyond the requested task. Not a blanket bypass. | Opt-in for exploratory or wide-blast-radius tasks where a complete allowlist is impractical. Accepts a non-deterministic gate in exchange for coverage. |
 
`bypassPermissions` / `--dangerously-skip-permissions` is **never** used. The droplet has SSH keys and real credentials; the blast radius is not zero.
 
Configuring the mode per-invocation (rather than via `defaultMode` in `settings.json`) deliberately sidesteps a known hierarchy gotcha: `defaultMode: "auto"` set in a project's `.claude/settings.json` is silently ignored and only honored in user settings, so a checked-out repo cannot grant itself auto mode. Per-invocation flags make the adapter self-contained and make the mode visible in the run's audit log.
 
**Deny rules are absolute.** `permissions.deny` rules block a call even in `bypassPermissions` mode and merge across all settings scopes. This makes `deny` the correct place for the hard safety boundary (§2.5).
 
**Headless prompt handling.** There is no TTY to answer a prompt the permission layer doesn't resolve. Such cases fail the invocation rather than hanging, and are classified as `environment_error` (§6).
 
**Exit codes.** A complete enumerated exit-code table for `-p` outcomes is not published. The adapter branches on **zero versus non-zero only** and reads the structured output for the reason. It must not hard-code meaning onto specific non-zero values.
 
**Quota.** Subject to the plan's usage window (§7) rather than per-token billing as long as no API key is configured. Anthropic announced a separate monthly credit pool for `claude -p` and Agent SDK usage effective June 15, 2026, then paused that change the same day; programmatic usage still draws from the subscription's usage limits. This is external policy that can change and must not be hard-coded as a permanent assumption — the adapter's quota handling is driven by observed rate-limit events (§7.2), not by a belief about the billing model.
 
### 2.4 Process lifecycle and kill semantics (new — must-fix)
 
The harness spawns children (Maven, Node/Vite, `docker` clients, Playwright's Chromium). On POSIX, killing only the parent PID re-parents those children to init, where they continue running, holding ports, and consuming the droplet's memory. Timeout enforcement is meaningless without correct group semantics.
 
**Contract, binding on every adapter:**
 
1. Every harness process is launched with `subprocess.Popen(..., start_new_session=True)`, placing it in a new process group and session.
2. `cancel(task_id)` sends `SIGTERM` to the **process group** via `os.killpg(os.getpgid(pid), signal.SIGTERM)`.
3. After a grace period (**20 s**), any surviving group members receive `SIGKILL` via the same `killpg` call.
4. After the kill, the orchestrator performs an **orphan sweep**: any container labeled with the task's `run_id`/`task_id` is force-removed (`docker rm -f`), and any process still holding the task's worktree path is logged as `critical`.
5. All gate containers are launched with `--label orchestrator.run_id=<run_id> --label orchestrator.task_id=<task_id>` specifically to make step 4 possible.
6. Failure to fully reap within the grace period emits `task.failed` with `failure_type=environment_error` and increments the circuit breaker (§6.5), because a leaked process pool will poison every subsequent task.
### 2.5 Guardrail hooks (new — must-fix)
 
Where `supports_gating` is true, the adapter installs `PreToolUse` hooks. These execute **before** the permission system and can deny a tool call outright — by emitting `{"hookSpecificOutput": {"permissionDecision": "deny", ...}}` on exit 0, or by exiting non-zero with the reason on stderr. A deny decision from a hook overrides the permission mode entirely.
 
**Required hooks for v1:**
 
| Hook | Blocks | Why |
|---|---|---|
| Test-path guard | `Edit`/`Write` under `src/test/**`, `**/*.spec.ts`, `**/*.test.ts`, `e2e/**` | The primary test-gaming defense (§6.1). Bypassed only when the task's `tasks.md` explicitly authorizes test changes, signaled by an `allow_test_edits: true` flag on the queue row. |
| Annotation guard | Diffs introducing `@Disabled`, `@Ignore`, `test.skip`, `it.skip`, `xit`, `describe.skip` | Weakening a test is functionally identical to deleting it. |
| Commit-integrity guard | `Bash(git commit *--no-verify*)`, `Bash(git push *)`, `Bash(git reset --hard*)`, force-push forms | `--no-verify` bypasses local pre-commit hooks including secret scanning. Pushing is the orchestrator's job, not the agent's. |
| Secret-read guard | `Read(./.env*)`, `Read(./secrets/**)`, `Read(**/*.pem)`, `Read(**/id_rsa*)` | Enforced as `permissions.deny` rules rather than hooks, since deny is absolute across all modes. An agent that cannot read a secret cannot commit one. |
 
**Two constraints that make or break this:**
- A hook that **times out does not block**. Gate hooks must be fast, synchronous, and locally executable — no network calls, no LLM invocations. Budget under 2 s; the hook `timeout` is set to 5000 ms as a hard ceiling.
- Async hooks (introduced Jan 2026) do not block and are unsuitable for gating. Use them only for telemetry.
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
QUEUED → PROPOSING → PROPOSED → IMPLEMENTING → VALIDATING → COMMITTING → MERGING → DONE
                                      ↑____FAILED_RETRY____↓
                                      (max attempts exceeded → BLOCKED)
```
 
**Isolation via `git worktree` (changed in v2).** Each task gets its own worktree, not a branch checkout in a shared working directory:
 
```
git worktree add /var/orchestrator/work/<run_id>/<task_id> -b task/<spec-id> develop
```
 
Each task therefore has a dedicated working directory sharing a single `.git` object store. This replaces branch-switching, which in a long-running loop causes checkout thrash, leaves stale build artifacts from the previous task's branch in place, and creates state bleed between tasks that presents as inexplicable test failures. Worktrees are the isolation primitive that Claude Code, Cursor's Parallel Agents, and Codex all converged on for exactly this reason.
 
Worktrees cost effectively nothing for a serial loop and remove the largest structural blocker to the parallelism deferred in §11. Note the limit honestly: **worktrees isolate code, not runtime.** Ports, the database, and `/dev/shm` remain shared. Serial v1 is unaffected; parallel v2 will additionally need port and DB namespacing.
 
On terminal states, the worktree is removed (`git worktree remove --force`) for `DONE`, and **retained** for `BLOCKED` so the failure can be inspected exactly as the agent left it. A `worktree_path` column on the queue table tracks these; a startup sweep prunes worktrees belonging to completed runs.
 
**Merge policy.** On `DONE`, the orchestrator merges `task/<spec-id>` into `develop` (only if `VALIDATING` passed), then removes the worktree and deletes the branch. **Merging `develop` → `master` is always manual, performed by the developer, and explicitly out of scope.**
 
**Recovery.** No mid-state resumption: if the process dies while a harness is running (`IMPLEMENTING`/`VALIDATING`), that state restarts from scratch. This remains the v1 posture. It is a known cost, not an inevitability — OpenSpec's `apply` resumes from the first unchecked task, and the harness supports session resumption via `--resume` with the `session_id` captured in the result object, so restarting a 40-subtask apply from subtask 1 wastes quota. Deferred deliberately to v2 (§11); the `session_id` is persisted now so the capability is available when it is implemented.
 
### 3.3 Timeout values (new — must-fix)
 
Anchored to an observed validation gate of 5–15 minutes. All values are configurable; these are the defaults.
 
| State | Wall-clock timeout | Stall timeout | On expiry |
|---|---|---|---|
| `PROPOSING` | 15 min | — | `timeout` failure; retry once, then `BLOCKED` |
| `IMPLEMENTING` | 90 min | 20 min without a checkbox transition **or** stream event | `cancel()` (§2.4), classify `timeout`, counts as an attempt |
| `VALIDATING` | 45 min (3× the 15-min p95 gate) | 10 min without container log output | `cancel()`, classify `timeout`, **does not** count as a code-level attempt |
| `COMMITTING` | 5 min | — | `timeout`; retry once, then `BLOCKED` |
| `MERGING` | 5 min | — | `BLOCKED` with `merge_conflict` (§3.4) |
| Run-level | 10 h (configurable per session) | — | `STOPPED` with reason `max_time`; in-flight task returns to `QUEUED` |
 
Notes:
- The `IMPLEMENTING` stall timer is reset by **either** a checkbox transition or any `stream-json` event, so a legitimately long single subtask does not trip it.
- The `VALIDATING` wall clock is 3× the p95 gate specifically so a cold `.m2`/`node_modules` cache or a slow Docker pull does not read as a hang. Revisit once real p95 data accumulates; log actual gate duration on every run so this is empirically tunable rather than guessed.
- Timeouts in `VALIDATING` do not consume the code-level retry budget, because a gate that hangs is an environment problem, not an agent error.
### 3.4 Merge-conflict policy (new)
 
A strictly serial DAG still produces conflicts, because `develop` moves under long-running tasks.
 
1. Attempt fast-forward or standard merge into `develop`.
2. On conflict, **do not** hand the conflict back to the agent to resolve blind — that is a documented path to clobbered work and silent loss of the other task's changes.
3. Attempt exactly **one** automated recovery: rebase the task branch onto current `develop` and re-run the **full validation gate**. If the gate passes, merge. If the rebase itself conflicts, skip to step 4.
4. Otherwise: `BLOCKED` with `blocked_reason = merge_conflict`. Worktree and branch retained. Emit `task.blocked` at `severity = warning`.
Merge conflicts do **not** count toward the circuit breaker — they indicate queue contention over shared files, not a broken environment. Repeated conflicts on the same files are a signal that the DAG's `depends_on` edges are under-specified; surface this in `run.summary`.
 
---
 
## 4. Progress & Liveness Tracking
 
v2 separates two signals that v1 conflated.
 
**Semantic progress — `tasks.md` checkboxes.** Watch the OpenSpec change's checklist for `- [ ]` → `- [x]` transitions via `watchdog`/inotify (fallback: polling every 5–10 s). Progress % = completed / total checkboxes.
 
This is the agent's **self-report**, not verified truth. OpenSpec treats `tasks.md` as a living checklist the agent checks off as it goes, and it explicitly permits editing the list mid-flight. Consequences the implementation must handle:
- Total checkbox count is **not constant**; percent can move backwards. Store both numerator and denominator, never percent alone.
- A checked box means "the agent believes this is done." Correctness is established only by the validation gate.
- Debounce writes (the UPSERT design in §8 handles write amplification).
**Liveness, cost, and rate-limit — `stream-json` (new in v2).** The adapter consumes `claude -p --output-format stream-json --verbose` as newline-delimited JSON. Every line is a self-contained event. This provides:
- A reliable heartbeat independent of whether the agent chooses to write a file
- Tool-call granularity for the audit trail
- `session_id` on every event, for replay
- A native rate-limit signal: the `system/api_retry` event distinguishes "the model is thinking" from "we are being rate-limited, here is the ETA" — the primary input to quota detection (§7.2)
- `total_cost_usd` on the terminal `result` object
Raw stdout text parsing is **removed** as a signal. It was always brittle (it greps human-readable prose that changes phrasing between model versions) and `stream-json` supersedes it entirely.
 
Where an adapter reports `supports_structured_stream: false`, the loop falls back to file-mtime-based liveness on the worktree, and the stall timeout is the only protection.
 
---
 
## 5. Task Queue
 
- Modeled as a **DAG**, not FIFO: tasks declare explicit `depends_on` (hard constraint on ordering), separate from `priority` (soft tie-breaker among tasks already eligible to run)
- Dependencies are explicit metadata on the change, never inferred from spec content
- Execution is strictly **serial** in v1 — no parallel harness runs against the same repo
- Same table backs both the queue and the run's persistent state (§8)
**Columns:** `task_id`, `spec_path`, `depends_on`, `priority`, `status`, `attempt_count`, `max_attempts`, `last_error`, `blocked_reason`, `allow_test_edits`, `worktree_path`, `session_id`, `created_at`, `updated_at`.
 
**`blocked_reason` enum (new):** `code_failure | cost | merge_conflict | environment | timeout | flaky_unresolved`. Circuit-breaker logic, resume decisions, and manual triage all branch on this. Without it every consumer ends up regex-parsing `last_error`, which is a free-text field and will drift.
 
---
 
## 6. Failure Classification, Retries, Circuit Breaker
 
### 6.1 Test-gaming guardrail (new — must-fix)
 
The single most-documented failure mode of unattended coding agents is producing a green build by weakening the thing that measures it: deleting or disabling failing tests, adding `@Disabled`, catching exceptions to make a build pass, mocking the component under test, or asserting nothing. Published evaluation work (e.g. SpecBench, 2026) found this across every model and harness tested, with the gap between visible and held-out test pass rates widening as task complexity rose and model capability fell.
 
This matters doubly here because the loop's own progress signal (§4) is agent-reported. An agent that games the tests also checks the boxes.
 
**Defense in depth, three layers:**
 
1. **Prevention (`PreToolUse` hooks, §2.5).** Edits to test paths and additions of skip/disable annotations are denied before they execute. This is deterministic and is the strongest layer.
2. **Detection (diff gate, runs inside `VALIDATING`).** Before tests execute, the gate inspects `git diff develop...task/<spec-id>` and **fails the task** if any of the following hold and `allow_test_edits` is not set:
   - Any file under a test path was modified or deleted
   - Net assertion count across the diff decreased
   - Any skip/disable annotation was introduced
   - A test file's LOC decreased by more than a configured threshold
3. **Fallback (post-hoc, adapters with `supports_gating: false`).** Layer 2 only. Strictly weaker, since the edit has already been written; the task fails rather than being prevented.
A guardrail trip is classified as `code_error` with `failure_stage = test_integrity`, and the `error_detail` names the specific violation so the retry prompt can say what not to do again.
 
**Secret handling** rides the same machinery: `permissions.deny` on secret paths (§2.5), a `gitleaks` pre-commit hook in each worktree, a hook blocking `git commit --no-verify`, and a gate-side `gitleaks` scan as backstop since local hooks are bypassable. Any secret that reaches a commit is treated as compromised and requires rotation — detection is not remediation.
 
### 6.2 Failure types
- `code_error` — build/test/e2e/test-integrity failure caused by the generated code. Counts toward the task's retry limit.
- `environment_error` — Docker unresponsive, MCP/Playwright connection failure, permission denial with no TTY, process-reap failure, network timeout. Does **not** count toward the task's retry limit; feeds the global circuit breaker.
- `timeout` — state-specific or stalled-harness timeout reached. Counts as an attempt in `IMPLEMENTING` only.
- `flaky` (**new**) — a test failure that did not reproduce on isolated rerun. Does **not** count toward the retry limit and does **not** feed the circuit breaker.
### 6.3 Per-task retries
- Max attempts: 2 (configurable). Third code-level failure → `BLOCKED`.
- Retries are **informed**, not blind: the retry prompt includes the previous attempt's `error_detail` (§9.3) plus a short summary of what was already tried, so the harness doesn't repeat the same failed approach.
- Delay of 30–60 s between retries — not rate-limiting, but letting transient resource contention settle (Docker releasing the previous attempt's resources, ports unbinding).
- **Retry budget varies by stage (new):** a compile or build failure is almost always a real, fixable agent error and gets the full budget. An e2e failure is far likelier to be environmental and is subject to §6.4 before it consumes an attempt at all.
### 6.4 Flaky-test handling (new — must-fix)
 
Without this, an intermittent Playwright test will exhaust a task's retries on a code error that does not exist, and `BLOCKED` good work.
 
**Quarantine list.** A version-controlled `quarantine.yml` in the orchestrator repo names known-flaky tests. Failures of quarantined tests are recorded but do not fail the gate and do not consume retries. Entries require an owner and an expiry date — an unowned, unexpiring quarantine list is how a suite quietly stops testing anything. This mirrors standard practice at organizations running large suites (Google's flaky-test infrastructure; Atlassian's Flakinator), where quarantined tests keep running but stop blocking.
 
**Confirm-by-rerun.** When a non-quarantined e2e test fails:
1. Re-run **only that test**, in isolation, up to 3 times.
2. If it passes on any rerun with no code change, classify the failure as `flaky`, not `code_error`. Do not consume an attempt. Emit `task.validation_result` with a `flaky_detected` list and `severity = warning`.
3. If it fails all reruns, it is a genuine `code_error`.
4. Auto-propose quarantine: a test classified `flaky` three times across distinct runs is appended to a `quarantine-candidates.yml` for human review. Never auto-quarantine — that is the same self-weakening failure mode as §6.1, just performed by the orchestrator instead of the agent.
Rerun cost is bounded: an isolated single-test e2e run is a fraction of the full gate, and the alternative is burning a full retry cycle plus a full gate on a phantom failure.
 
### 6.5 Global circuit breaker
- Trips to `PAUSED` when: N distinct tasks (not retries of the same task) land in `BLOCKED` consecutively, **or** repeated `environment_error`s occur across distinct tasks — both signal something in the environment is broken, not the specs themselves.
- `merge_conflict` and `flaky` blocks are **excluded** from the tally.
- A process-reap failure (§2.4 step 6) counts double: a leaked process pool poisons every subsequent task, so the breaker should trip fast.
- `PAUSED` keeps the process and queue state intact; resuming requires manual intervention (or, for quota exhaustion, automatic resumption once the window resets — §7).
---
 
## 7. Quota & Cost Limits
 
Two distinct mechanisms, because they behave differently.
 
### 7.1 Subscription usage windows
 
No dollar cost, but hard caps that reset on timers. **There are two, and v1 only modeled one:**
 
- **Rolling 5-hour window.** Resets on a rolling basis; a pause here is short and auto-resume within the same session is realistic.
- **Weekly cap (added by Anthropic on 28 July 2025, effective 28 August 2025).** Resets at a **fixed time each week assigned to the account**, not on a rolling timer. Usage is shared across claude.ai, Claude Code, and Desktop, so the developer's own interactive usage consumes the same budget the loop depends on.
**Consequence for auto-resume:** the reset may be **days** away, not hours. The resume logic must branch:
- 5-hour window exhausted → `PAUSED`, schedule resume at window reset, keep the process alive.
- Weekly cap exhausted → `PAUSED`, and if the computed reset is beyond the run's remaining wall-clock budget, transition to `STOPPED` with reason `quota_exhausted_weekly` rather than holding a process idle for days.
Neither counts toward the circuit breaker.
 
### 7.2 Quota detection
 
Do not infer quota exhaustion from generic non-zero exits or by grepping prose. Detection sources in priority order:
1. The `system/api_retry` event in the `stream-json` stream, which carries rate-limit state and an ETA. **Primary.**
2. The terminal `result` object's error subtype.
3. Wall-clock heuristic (repeated immediate failures across distinct tasks with no tool calls executed) — last resort, `severity = warning`, and it must never silently masquerade as a confirmed quota state.
### 7.3 Dollar-denominated cost limits
 
For harnesses billed per token (a future Cursor/Cline/API-key adapter):
- `max_cost_per_run_usd` — hard ceiling for the entire session
- `max_cost_per_task_usd` — prevents a single task's retries consuming the whole budget
- Warning event at 80% of `max_cost_per_run_usd`
- On reaching `max_cost_per_run_usd`: `STOPPED` (not `PAUSED` — nothing to auto-recover from)
- On a single task exceeding `max_cost_per_task_usd`: that task → `BLOCKED` with `blocked_reason = cost`; queue continues
- Cost read from `total_cost_usd` where reported natively; otherwise estimated from token counts, or the hard-stop is disabled and flagged unsupported in config
For the v1 Claude Code CLI adapter these are effectively inert (governed by the subscription window instead), but the fields exist so the mechanism applies unchanged later.
 
---
 
## 8. Persistent State
 
- SQLite, local to the orchestrator's own repo/data directory
- Two kinds of tables, treated differently:
  - **Historical / append-only**: task transitions, failures, completions — full audit trail, never overwritten
  - **Current-state / UPSERT**: progress, heartbeat timestamp, accumulated run cost, accumulated per-task cost — one row per entity, not one row per tick (avoids flooding the DB from high-frequency file-watching)
### 8.1 Required pragmas (new — must-fix)
 
The design has a file-watcher and a stream reader writing telemetry while the main loop reads and writes state. The default rollback-journal mode produces lock contention in exactly this pattern.
 
```sql
PRAGMA journal_mode = WAL;        -- concurrent readers + one writer
PRAGMA busy_timeout = 10000;      -- 10s; avoid SQLITE_BUSY under contention
PRAGMA synchronous = NORMAL;      -- safe under WAL, substantially faster
PRAGMA foreign_keys = ON;
```
 
Applied on every connection, not once at creation. A periodic `wal_checkpoint(TRUNCATE)` runs at run boundaries to stop the WAL growing unbounded across a 10-hour session.
 
**Single-writer discipline:** all writes go through one connection owned by the main loop. The file-watcher and stream reader push onto an in-process queue rather than opening their own write connections. SQLite tolerates multiple writers under WAL, but serializing them removes a whole class of intermittent failures from the component that is supposed to be the reliable one.
 
---
 
## 9. Event Schema & Observability
 
### 9.1 Common envelope
`event_id`, `run_id`, `task_id` (nullable), `timestamp`, `sequence` (monotonic within the run), `event_type`, `severity` (`info | warning | error | critical`), `schema_version` (**new**), `payload`
 
`schema_version` is present from day one so the event table can migrate without a backfill archaeology project. `sequence` is written transactionally with the event so ordering survives a crash.
 
### 9.2 Event types
 
**Run-level:** `run.started` (harness, permission mode, limits, base branch), `run.paused` (reason: `circuit_breaker | quota_exhausted_5h | quota_exhausted_weekly`, triggering task, consecutive-failure count), `run.resumed`, `run.stopped` (reason), `run.summary` (completed, blocked, retried, flaky detected, merge conflicts, total duration, total cost).
 
**Task-level:** `task.state_changed` (`from_state`, `to_state`, `attempt_number`), `task.failed`, `task.blocked` (`blocked_reason`, attempts exhausted), `task.completed` (duration, files changed, commit hash, merge target), `task.validation_result` (unit and e2e results reported **separately** — pass/fail counts each, never one combined boolean — plus `flaky_detected[]` and `quarantined_skipped[]`), `task.progress` (completed/total subtasks, last completed subtask label — UPSERT), `task.heartbeat` (last-activity timestamp, current state, elapsed in state, source: `stream | file | mtime` — UPSERT), `task.guardrail_tripped` (**new** — which guardrail, which file, which tool call).
 
### 9.3 `task.failed` payload detail
- `failure_type`: `code_error | environment_error | timeout | flaky`
- `failure_stage`: `propose | implement | build | unit_tests | e2e_tests | test_integrity | commit | merge`
- `error_summary`: 1–2 lines, human-readable
- `error_detail`: **actionable** content for the retry prompt — failing test name + assertion + trimmed stack trace; failing build error; failing Playwright step + trace/screenshot path (path only, never embedded binary); for `test_integrity`, the specific violation and file. Never a full raw log dump. The retry prompt is the consumer, so this must be model-consumable, not archival.
- `files_touched`, `attempt_number`, `previous_attempts_summary`
- `will_retry` (bool), `next_action`: `retry | block | escalate_circuit_breaker`
### 9.4 Native OpenTelemetry export (new)
 
Enable Claude Code's built-in telemetry via `CLAUDE_CODE_ENABLE_TELEMETRY=1`. It emits metrics and logs (traces in beta) covering token usage, cost, tool calls, lines changed, and commits — per-tool-call granularity the orchestrator gets for free rather than reconstructing from the event stream. Default export interval is 60 s.
 
Content logging stays **off**. Prompts and file contents in a telemetry backend is a data-exfiltration path, and this loop operates on a private codebase.
 
**On not migrating to OTel spans yet:** the OpenTelemetry GenAI semantic conventions now model an agent run as a span tree (`invoke_agent` → `chat` → `execute_tool`), which is a natural fit for this run/task/tool hierarchy. They remain marked *Development* rather than stable, so a bespoke SQLite event table is the right v1 choice. The envelope in §9.1 is deliberately shaped so `event_type` and payload keys map onto GenAI span attributes later without a schema rewrite. Deferred, not rejected (§11).
 
### 9.5 Log and disk management (new)
 
A 10-hour run with `stream-json` at tool-call granularity plus full harness logs plus Playwright traces and screenshots will fill a 50 GB droplet faster than expected, and a full disk fails every subsequent task in a way that looks like a code error.
 
- `raw_log_path` files rotate per task; retained 7 days for `DONE`, 30 days for `BLOCKED`
- Playwright traces/screenshots retained only for failing runs
- A pre-run disk check aborts the run with `severity = critical` below a configurable free-space floor (default 10 GB)
- systemd unit sets `OOMPolicy=stop`, memory accounting, and `WatchdogSec` with the loop issuing a watchdog ping each state transition; journald rate limits are raised to avoid dropping the loop's own logs
---
 
## 10. Spec & Knowledge Management
 
- Architecture/domain knowledge lives in topic-scoped markdown files, versioned in git: `backend-arch.md`, `frontend-arch.md`, `data-model.md`, `e2e-validation.md`
- Updated **by event**, not on a schedule: as part of `COMMITTING`, if a completed task introduced a decision that constrains future work, the harness appends 2–3 lines to the relevant file. Narration of "what happened" does not belong here — that is already in git history and the event log.
- `decisions-log.md` — short ADR-style log (decision + date + originating task)
**No retrieval-based memory system in the loop.** Retry context and cross-task continuity come from the structured event log and state store — deterministic and queryable — not from semantic retrieval, which is a worse fit for unattended overnight execution where no human is available to notice a bad retrieval. This aligns with the CLAUDE.md / AGENTS.md convention the ecosystem has converged on, including OpenSpec's own generated `AGENTS.md`.
 
**Guarding against note rot (new).** These files feed back into the agent's context on every subsequent task, so they are not write-only — a bloated or self-contradictory `backend-arch.md` actively degrades future task quality, and "the agent writes notes nobody validates" is a documented failure mode.
 
- Each knowledge file has a hard size cap (default 400 lines). Exceeding it fails the `COMMITTING` step.
- The append instruction is an **edit/reconcile** instruction: if a new decision contradicts an existing line, revise that line rather than stacking a contradiction beneath it.
- `decisions-log.md` entries stay structured (decision + date + task id) so they remain queryable and de-dupable.
- A compaction pass is a manual, human-reviewed operation, surfaced as a recommendation in `run.summary` when a file approaches its cap. It is never automated — an agent silently rewriting its own accumulated architectural constraints is the §6.1 failure mode wearing a different hat.
---
 
## 11. Non-Goals (v1)
 
- Telegram or any real-time notification channel
- Web dashboard
- Any harness other than Claude Code CLI
- Parallel task execution
- Automatic merge into `master`
- Resuming partial in-flight harness work after a crash
- Full OpenTelemetry span-tree migration
- Automated quarantine of flaky tests (proposal only, human approves)
### Recorded for v2, deliberately deferred
1. **Parallel task execution.** The worktree decision (§3.2) removes the structural blocker. Remaining work is runtime isolation: port allocation, per-task database namespacing, and `/dev/shm` budgeting across concurrent Chromium instances.
2. **Full OTel span-tree migration.** Blocked on GenAI semantic conventions reaching stable. The §9.1 envelope is shaped to make this a mapping exercise rather than a rewrite.
3. **Partial mid-state resumption.** `--resume` with the persisted `session_id`, combined with OpenSpec's resume-from-first-unchecked-task behavior, would avoid restarting long applies from scratch. `session_id` is already captured (§2.2) so this needs no schema change.
---
 
## Open Items for Follow-Up Specs
1. Full adapter configuration schema (required vs. optional fields per declared capability, including the two new capability flags)
2. SQLite schema DDL and the Claude Code CLI adapter implementation
3. `PreToolUse` hook implementations and the diff-gate assertion-counting heuristic (language-specific: JUnit/AssertJ for Java, Vitest/Playwright for TS)
4. Empirical retuning of §3.3 timeouts once p95 gate duration data exists
5. Quarantine ownership and expiry policy, and the escalation path when `quarantine-candidates.yml` grows
 