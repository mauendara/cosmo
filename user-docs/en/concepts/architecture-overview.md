# Architecture overview

What Cosmo is made of, and why the pieces are arranged this way.

## The shape of it

```
                       cosmo run
                           │
              ┌────────────▼────────────┐
              │  run loop (serial)      │   one task at a time
              │  DAG scheduler          │   recomputes eligibility each pass
              │  circuit breaker        │
              │  quota + cost accounting│
              └────────────┬────────────┘
                           │  per task
              ┌────────────▼────────────┐
              │  task state machine     │
              └──┬───────┬───────┬──────┘
                 │       │       │
        ┌────────▼──┐ ┌──▼─────┐ ┌▼──────────┐
        │ harness   │ │ gate   │ │ git       │
        │ adapter   │ │ Docker │ │ worktree  │
        │ (LLM)     │ │ only   │ │ + merge   │
        └───────────┘ └────────┘ └───────────┘
                 │       │       │
              ┌──▼───────▼───────▼──┐
              │ SQLite: state       │
              │ + append-only events│
              └─────────────────────┘
```

Three boundaries in that picture are enforced by tests that read the source,
not by convention:

- **The gate never imports the harness.** Validation bypasses the LLM
  entirely — it is direct Docker invocation, and there is no code path by
  which an agent could influence its own verdict.
- **The merge ladder never imports the harness.** A merge conflict is
  therefore never handed back to the agent to resolve blind — there is no
  adapter in scope for that code path to hand it to.
- **Only the Claude adapter module may name Claude-specific binaries, flags
  or environment variables.** Core orchestration code never branches on which
  harness is configured.

## Serial by design

Cosmo runs exactly one task at a time. Worktrees isolate *code*, not runtime:
ports, databases and `/dev/shm` are still shared, so two concurrent tasks
would contend on all three. Parallelism would mean solving that first, and
the current design says so rather than pretending isolation it doesn't have.

A process lock enforces it. A second `cosmo run` against the same store
refuses to start.

## The queue is a DAG, not a FIFO

Tasks carry explicit `depends_on` edges. Nothing is ever inferred from
filenames, spec content, or ordering — an inferred dependency that's wrong is
worse than no dependency at all.

Scheduling recomputes the full eligible set on every pass, not one task
ahead. When a task blocks, independent branches of the graph keep running.
`priority` breaks ties among simultaneously eligible tasks; it never
overrides an edge.

Cycles are rejected at enqueue time and again at run startup, never
discovered mid-run.

Two front doors lead into the same queue, and `cosmo run` can't tell which a
task came through:

- **`cosmo spec add` → `cosmo spec queue`** — a rough spec, enriched against
  your repo's own docs and decomposed into task files you can hand-edit
  before queueing.
- **`cosmo queue add`** — a hand-authored OpenSpec change, queued directly.

## The task state machine

```
QUEUED → PROPOSING → PROPOSED → IMPLEMENTING → VALIDATING
       → REVIEWING → COMMITTING → MERGING → FINISHING → DONE
                              ↘ FAILED_RETRY ↗
                              ↘ BLOCKED
```

- **PROPOSING** — the harness runs OpenSpec's propose workflow. Gets its own
  bounded "retry once, then block" policy that never touches the task's
  attempt counter; there's no code yet to attribute a code error to. The
  change's name is pinned in the prompt, because everything downstream
  (`openspec archive`, worktree reuse) assumes it.
- **PROPOSED → IMPLEMENTING** — the harness writes and commits the code,
  watched by a wall clock and a stall timer. Progress is read from the
  change's `tasks.md`, not from anything the agent asserts.
- **VALIDATING** — the gate. See
  [validation-gate-and-guardrails](validation-gate-and-guardrails.md).
- **REVIEWING** — a fresh, memoryless adversarial review. Skipped when
  `review.enabled = false`.
- **COMMITTING** — never calls the harness. It enforces the knowledge-file
  line cap on any `docs/**/*.md` the task touched, and appends one
  Cosmo-authored line to `docs/decisions-log.md`. A cap violation loops back
  to `IMPLEMENTING` as an informed retry.
- **MERGING** — the conflict ladder, below.
- **FINISHING** — best-effort `openspec archive`. A failure here is logged as
  a warning and never un-does a merge that already succeeded.

`attempt_count` is incremented only for attempts that represent a genuine
code-level judgment. See the failure table in the gate document.

## Worktree isolation

Every task gets `git worktree add <work_dir>/<run_id>/<task_id> -b
task/<spec_id>` — a dedicated working directory over one shared object
store. No branch switching, no half-applied work from the previous task, no
`git stash` dance.

Immediately after creation, Cosmo syncs the harness assets into the worktree
and installs the gitleaks pre-commit hook. (Git hooks live in the
repository's *common* hooks directory, shared by every linked worktree, so
installing per-worktree is idempotent and self-healing.)

A `BLOCKED` task's worktree and branch are left on disk for you to inspect.
A startup sweep removes worktrees belonging to runs that already ended.

## The merge ladder

`cosmo run --repo` points at Cosmo's own dedicated checkout of the target
repo, which stays on the base branch at all times. It is never a developer's
interactive working directory.

On merge conflict, the ladder is:

1. Try the merge.
2. On conflict, rebase the task branch onto the base branch and **re-run the
   full validation gate**. A rebase changes the code under the tests; a
   rebase that isn't re-validated is a merge that was never tested.
3. Still conflicted, or the re-validation fails: block the task with
   `merge_conflict` and move on.

The conflict is never handed back to the agent. `merge_conflict` blocks are
excluded from the circuit breaker's tally — they signal queue contention over
shared files, not a broken environment.

## The harness adapter interface

One interface, `HarnessAdapter`, with seven methods: `preflight`, `probe`,
`propose`, `implement`, `review`, `get_progress`, `cancel`. Every method
returns the same uniform `HarnessResult`.

Each adapter also declares its capabilities as class-level data, and each
flag names the fallback Cosmo takes when it's false:

| Capability | False means |
| --- | --- |
| `reports_native_progress` | watch the change's `tasks.md` instead |
| `supports_retry_context` | compose a synthetic retry prompt |
| `has_internal_timeout` | Cosmo imposes an external timeout |
| `reports_native_cost` | estimate from tokens, or disable the cost hard stop |
| `supports_gating` | post-hoc diff inspection only — strictly weaker |
| `supports_structured_stream` | fall back to file-mtime liveness |

`cosmo harness list` prints the table. Writing an adapter:
[write-a-new-adapter](../how-to/write-a-new-adapter.md).

Note that `validate` is deliberately *not* on this interface, despite the
original design listing it. Validation bypasses the harness entirely, so a
method that never touches the harness doesn't belong on the harness adapter.

## Process lifecycle

Killing a harness call means killing its whole process group — SIGTERM,
`timeouts.kill_grace` seconds, then SIGKILL — followed by a sweep for
orphaned Docker containers (found by their `orchestrator.run_id` /
`orchestrator.task_id` labels) and any process still holding the worktree
open.

A failed reap emits a `critical` `task.failed` event weighted at
`circuit_breaker.reap_failure_weight` (default 2, i.e. double), because a
leaked process pool poisons every task after it and the run should stop fast.

`cosmo doctor` checks for leaked gate containers as a core check, so a
previous run's mess is visible before the next one starts.

## State and continuity: no retrieval memory

There is **no vector store, no embedding index, no retrieval-based memory**.
That's a decision, not a gap. Continuity across tasks comes from three
deterministic sources:

- **Structured event logs** — an append-only table with a transactional
  sequence number, so ordering survives a crash.
- **SQLite current-state tables** — the queue, run state, costs, progress,
  heartbeats, and a per-attempt failure history.
- **Version-controlled markdown** in the target repo — the `docs/` knowledge
  files the agent maintains, capped at `knowledge.max_file_lines` and
  enforced by Cosmo rather than trusted to the agent, plus
  `docs/decisions-log.md`, appended by Cosmo itself so its format never
  drifts.

The trade is deliberate: every one of those is queryable, diffable, and
identical on a re-read. A retrieval layer would make cross-task recall
fuzzier exactly where the system most needs to be reproducible — and a wrong
recall in an unattended loop is a bug nobody is awake to catch.

## Configuration and state on disk

```
$XDG_CONFIG_HOME/cosmo/config.toml     user config  (or $COSMO_CONFIG)
$XDG_DATA_HOME/cosmo/
  cosmo.db                             state + events
  work/<run_id>/<task_id>/             task worktrees
  logs/                                raw harness logs
```

Defaults follow XDG so a developer box needs no root; a server overrides all
three to something like `/var/cosmo`. Same code, different config per host.
Config is validated at load time — a malformed value fails now, not mid-run.

## Observability

| Question | Command |
| --- | --- |
| How did the run end? | `cosmo report` |
| What happened, in order? | `cosmo events tail --run <id> --payload` |
| Why is this task stuck? | `cosmo queue show <task_id>` |
| What actually failed, with the real error text? | `cosmo queue failures <task_id>` |
| Is the host ready? | `cosmo doctor` |

Under systemd, the run loop sends `sd_notify` readiness and watchdog pings,
so a genuinely wedged process is killed and restarted while a deliberate stop
(a circuit-breaker pause) is not. Notifications go out from a *separate*
always-on process (`cosmo notify watch`) — delivery inside the run loop
could never report the run loop's own crash.

---

## Not implemented yet

Stated here so it isn't mistaken for a shipped feature:

- **An MCP control plane.** A thin MCP server over the same CLI contract,
  letting an agent or tool drive Cosmo's control plane (enqueue, status,
  cancel, logs) from the outside. This is a distinct capability from Cosmo
  *using* an agent as a harness, and the two shouldn't be conflated. **No
  such server exists today.**
- **Adapters other than Claude Code.** The interface is real and the
  boundary is test-enforced; the second adapter isn't written.
- **Parallel task execution.** See "Serial by design" above.
