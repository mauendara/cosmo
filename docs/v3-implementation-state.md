# Cosmo — Implementation State

Running record of what actually exists in the codebase, phase by phase. Updated at
the end of each working session.

The plan ([v3-implementation-plan.md](v3-implementation-plan.md)) says what *will*
be built. This document says what *is* built, and records decisions and gotchas
made during implementation that a future session would otherwise have to
rediscover.

| | |
|---|---|
| Last updated | 2026-08-25 |
| Working branch | `develop` |
| Head commit | `97b1742` — the `--config` fix (Phase 4 not yet committed) |
| Spec | [v3-cosmo-autonomous-agent-spec.md](v3-cosmo-autonomous-agent-spec.md) |

## Phase status

| Phase | Status |
|---|---|
| 0 — Repository skeleton and configuration | **Complete** |
| 1 — Persistent state and the event log | **Complete** |
| 2 — Process supervision | **Complete** |
| 3 — Harness abstraction and Claude Code adapter | **Complete** |
| 4 — Template system and `cosmo init` | **Complete** |
| 5 — Worktree lifecycle and git operations | Not started |
| 6 — Validation gate | Not started |
| 7 — Task state machine | Not started |
| 8 — Run loop, DAG, circuit breaker, quota | Not started |
| 9 — Observability, logs, deployment | Not started |
| 10 — Acceptance run | Not started |

---

## Phase 0 — Complete

All exit criteria met. 38 tests passing; `ruff`, `ruff format`, and `mypy --strict`
clean. `./check.sh` runs all four in one command.

### What exists

| Path | Contents |
|---|---|
| `src/cosmo/checks.py` | `CheckResult` / `CheckStatus` — the neutral result type both core and adapter preflight produce |
| `src/cosmo/config/model.py` | The full typed config model, every spec tunable, with cross-field validators |
| `src/cosmo/config/defaults.toml` | Shipped spec defaults; each value annotated with its spec section |
| `src/cosmo/config/loader.py` | Three-layer loading: shipped defaults → user config → CLI overrides |
| `src/cosmo/doctor.py` | Core (harness-agnostic) preflight checks |
| `src/cosmo/harness/base.py` | `HarnessAdapter` ABC, `HarnessCapabilities`, `HarnessResult` |
| `src/cosmo/harness/registry.py` | Name → adapter mapping and resolution-with-provenance |
| `src/cosmo/harness/claude.py` | Claude adapter: capabilities + `preflight()` implemented; execution methods raise `NotImplementedError` |
| `src/cosmo/cli/main.py` | `cosmo` command: `--version`, `config show`, `harness list`, `doctor` |
| `src/cosmo/{store,events,proc,git,gate,task,run,knowledge}/` | Empty packages, each with a one-line comment naming its phase and spec section |
| `check.sh` | Lint + format + types + tests |

Working commands:

```
cosmo --version
cosmo config show [--paths]
cosmo harness list
cosmo doctor [--harness NAME] [--config PATH]
```

### Decisions made during Phase 0

**1. `doctor` is split along the harness boundary.**
The original plan put an `ANTHROPIC_API_KEY` check in a generic `cosmo doctor`.
That check is meaningless to a Cursor or Codex adapter and hardcodes one harness
into the harness-agnostic layer, which §2 forbids. Now:
- `cosmo/doctor.py` holds core checks only and **does not import `cosmo.harness`
  at all**.
- Harness-specific preconditions come from `preflight()` on the resolved adapter.
- `cli/main.py` composes the two and renders them as separate tables.

**2. `HarnessAdapter.preflight()` — extension to spec §2.2.**
The spec's interface lists `propose`/`implement`/`validate`/`get_progress`/`cancel`.
A sixth method was needed so each adapter declares its own environmental
preconditions rather than core hardcoding them. Fold into a future spec revision.

**3. `validate()` deliberately omitted from the adapter interface.**
Spec §2.2 lists `validate(task_id)` as an adapter method while also stating that
validation "bypasses the LLM harness entirely (direct Docker invocation)." Those
conflict. Validation is owned by `cosmo.gate` (Phase 6). Recorded in a comment at
the bottom of `harness/base.py`.

**4. Top-level `stream/` package removed from the plan's layout.**
`stream-json` is Claude Code's wire format, not a universal one. A core-level
reader would leak this harness's wire protocol across the §2 boundary. The reader
belongs in `harness/claude/` in Phase 3. Found by the boundary test on its first
run, not by inspection.

**5. Paths default to XDG, not the spec's `/var/cosmo`.**
`/var` requires root on a WSL2 development box. `paths.data_dir` /
`paths.work_dir` / `paths.log_dir` default to `~/.local/share/cosmo/*`; the
droplet overrides them to `/var/cosmo` via its own config file. Same code,
different config per host.

**6. Harness resolution returns provenance.**
`resolve_harness_name(flag, project, configured) -> (name, source)`. Every
command prints which adapter it chose and why; an audit log should never have to
guess. Order: `--harness` flag > project registration (Phase 1) > config default.

**7. Config rejects settings that would fail silently at runtime.**
Beyond type checking, the model refuses:
- a stall timeout at or above its wall clock — it could never fire, silently
  disabling the only guard against a hung harness (§3.3)
- `retries.delay_min > delay_max`
- a `playwright_image` pinned to `:latest` or with no tag — §1.1 requires atomic
  version pinning
- unknown keys (`extra="forbid"`), so a config typo fails loudly rather than
  being ignored

**8. A cost ceiling of `0.0` means "disabled."**
The posture for a subscription-billed harness, where §7.1 usage windows govern
instead. `CostConfig.run_limit_enabled` / `.task_limit_enabled` express this so
callers never compare against zero themselves.

### Things that will matter later

**The boundary test is load-bearing.**
`tests/test_harness_boundary.py` fails if any harness-specific token
(`ANTHROPIC_API_KEY`, `stream-json`, `--permission-mode`, `max-turns`,
`dangerously-skip-permissions`) or the bare literal `"claude"` appears in a core
module, or if `doctor.py` imports `cosmo.harness`. It already caught one real
violation. **When adding a genuinely harness-aware module, add it to
`ALLOWED_HARNESS_AWARE` rather than weakening the test.**

**`defaults.toml` is the only place in core that names a harness.**
It is configuration data, not logic, and is on the allow-list for that reason.

**`harness/claude.py` becomes a package in Phase 3.**
`harness/claude/` with `adapter.py` and `stream.py`, so the stream reader sits
beside the adapter rather than in core.

**Project registration is the missing middle tier of harness resolution.**
`cli/main.py` currently passes `None` for the project tier, with a comment. Phase 1
adds the `projects` table (§10.4 step 6); wire it in there.

**Tests must never read the developer's real user config.**
`tests/test_cli.py` sets `COSMO_CONFIG` and `XDG_DATA_HOME` to temp paths in an
autouse fixture. Any new test touching config needs the same isolation, or it will
pass or fail depending on whose machine it runs on.

**`cosmo doctor` warns rather than fails on a `/mnt` work dir.**
Slow WSL2 filesystem I/O distorts every §3.3 timeout, but it is not a hard block.

**Repo branch model mirrors the spec's target-repo model.**
`develop` is the working branch, `master` is promoted manually — the same shape
§3.2 describes for managed projects. Cosmo's own branches are unrelated to
`git.base_branch` in config, which refers to the *target* repo.

### Environment as verified on 2026-08-24

| | |
|---|---|
| Python | 3.14.4 |
| `uv` | 0.11.28 |
| `git`, `docker`, `claude`, `openspec` | all on PATH; `docker` resolves to the Docker Desktop shim under `/mnt/c` |
| `ANTHROPIC_API_KEY` | unset (correct) |
| Free disk | ~940 GB on the WSL2 ext4 filesystem |
| Install | `uv tool install --editable .` → `cosmo` on PATH at `~/.local/bin/cosmo`, pointing back at the source tree |

---

## Phase 1 — Complete

All exit criteria met. 71 tests passing; `ruff`, `ruff format`, and `mypy --strict`
clean.

### What exists

| Path | Contents |
|---|---|
| `src/cosmo/store/clock.py` | One `utcnow_iso()` used by every timestamp column |
| `src/cosmo/store/enums.py` | `TaskStatus`, `BlockedReason`, `FailureType`, `FailureStage`, `NextAction`, `RunStatus`, `PauseReason`, `StopReason`, `HeartbeatSource`, `Severity` — each mirrors a CHECK constraint in the schema |
| `src/cosmo/store/connection.py` | Pragmas (§8.1) applied on every connection; `connect_writer` (rw) and `connect_reader` (genuine SQLite `mode=ro`) |
| `src/cosmo/store/migrations.py` | Forward-only migration runner, `schema_migrations` table, migration 1 = the full schema DDL |
| `src/cosmo/store/writer.py` | `StoreWriter` — the single write connection; `task_queue` add/retry/block, `task_transitions` append, `projects` registration, `submit()`/`drain()` for cross-thread handoff |
| `src/cosmo/store/reader.py` | Read-only queries, each opening and closing its own `connect_reader` connection |
| `src/cosmo/events/envelope.py` | `Event` dataclass (§9.1 envelope), `EventType` (§9.2), `EVENT_SCHEMA_VERSION = 1` |
| `src/cosmo/events/emitter.py` | `EventEmitter` — transactional `sequence` allocation via one `INSERT ... ON CONFLICT ... RETURNING` plus one `INSERT` per emit, both in the same `sqlite3` transaction |
| `src/cosmo/cli/main.py` | Adds `cosmo queue add\|ls\|show\|retry\|block`, `cosmo events tail`, `cosmo project register\|list`; wires the project tier into `doctor --project-path` |
| `src/cosmo/doctor.py` | Adds `check_database` — readable + at the expected schema version, `ok` (not `fail`) when the DB simply doesn't exist yet |

Working commands (all new):

```
cosmo queue add <spec_path> [--task-id ID] [--depends-on ID ...] [--priority N]
                [--max-attempts N] [--allow-test-edits]
cosmo queue ls [--status STATUS]
cosmo queue show <task_id>
cosmo queue retry <task_id>
cosmo queue block <task_id> --reason REASON
cosmo events tail [--run ID] [--task ID] [--severity SEV] [--limit N]
cosmo project register <path> [--harness NAME] [--project-template NAME]
cosmo project list
cosmo doctor --project-path <path>   # now resolves the project tier for real
```

### Decisions made during Phase 1

**1. `task_queue` does not carry a `project_id`, deliberately.**
§5 lists an exact column set and it does not include one. A queue-to-project
association is real and will be needed, but nothing in Phases 1-3 requires it
yet (`cosmo run`, which would need it, is Phase 8) — adding it now would be
speculating ahead of the spec. Revisit when Phase 4's `cosmo init` or Phase 8's
run loop actually needs to resolve a task to a target repo.

**2. `cosmo project register` is a deliberate, minimal addition — not part of
`cosmo init`.**
The handoff's instruction to "wire up the missing harness-resolution tier"
needs *some* way to populate `projects` ahead of Phase 4's full bootstrap flow
(templates, symlinks, `openspec/` seeding). This command does none of that —
it only inserts a row so `doctor --project-path` has something to resolve
against. `cosmo init` in Phase 4 should treat this as the persistence
primitive it already has, not reimplement it.

**3. Single-writer discipline is enforced two ways, not one.**
`connect_writer` (the only function that opens a writable connection) is
import-restricted to `store/writer.py` and `store/migrations.py` by
`tests/test_store_boundary.py`, mirroring the Phase 0 harness-boundary test.
Separately, `connect_reader` opens a genuine SQLite `mode=ro` connection —
not just a convention — so a read path cannot become a second writer even by
accident. `StoreWriter.submit()`/`.drain()` exist now for the cross-thread
handoff the plan describes, ahead of Phase 2/3 having any threads that
actually need it.

**4. `sequence` is scoped per `run_id`, with `''` as the scope for run-less
events.**
§9.1 says "monotonic within the run," but §9.2 also names project-level
events (`agent_assets.synced`) that carry no `run_id`. A single
`event_sequence` table keyed by scope (empty string for the run-less case)
covers both without a nullable-column special case in the counter logic.

**5. `EventType`/`Severity`/`blocked_reason`/`failure_type`/`failure_stage`
are real Python enums whose `.value` sets are hand-kept in sync with the
schema's CHECK constraints.**
No codegen from one to the other — the plan's "enums in the schema, not free
text" is about the schema; mirroring it in Python is what makes a typo a
mypy error instead of a runtime CHECK failure. `event_type` itself is *not*
CHECK-constrained in the schema (only the three the plan names are) — new
event types are expected to be added over the project's life without a
migration.

**6. Migration transactionality is achieved with a literal `BEGIN`/`COMMIT`
inside each migration's own SQL text, not with Python-level transaction
control.**
`sqlite3.Connection.executescript()` implicitly commits any pending
transaction before running and gives no transaction guarantee across the
statements it runs — the transaction control has to live in the script
itself. `migrate()` appends a generic `schema_migrations` stamp (the table
creation plus the version-stamping `INSERT`) to every migration's script
before wrapping the whole thing in `BEGIN; ...; COMMIT;`, so the schema
change and its version stamp can never land separately.

**7. Timestamps are UTC ISO 8601 with millisecond precision everywhere**,
via one `store.clock.utcnow_iso()`, so every timestamp column sorts and
compares as a plain string with no parse step.

### Things that will matter later

**The concurrency exit criterion is tested against the pragmas, not just the
discipline.** `tests/test_store_concurrency.py` deliberately opens *two*
independent write connections (the shape single-writer discipline exists to
avoid) and hammers both for a second, asserting zero `SQLITE_BUSY`. This
proves the §8.1 pragma set actually holds under real contention, which is
what the plan's exit criterion literally asks for — the boundary test
(`test_store_boundary.py`) separately proves Cosmo's own code never creates
that situation.

**The "no gaps or duplicates" exit criterion is tested as a transaction
atomicity property, not by an actual `kill -9`.** `tests/test_events.py`
wraps the real connection in a proxy that fails exactly the `INSERT INTO
events` call on the second `emit()`, then asserts the next successful emit
gets sequence 2, not 3. `sqlite3.Connection` is a C type with no per-instance
`__dict__`, so a `monkeypatch.setattr` on the connection *object* doesn't
work — patch `StoreWriter._conn` instead (a plain Python attribute) and route
through a proxy that delegates everything except the one call under test.

**`StoreWriter.submit()`/`.drain()` has no real caller yet.** Phase 2's
process supervision and Phase 3's stream reader are the first consumers.
`tests/test_store_writer.py` exercises the mechanism directly (a background
thread submits a job, the main thread drains it) since there is no watcher to
exercise it end-to-end yet.

**`doctor --project-path` only resolves what has been registered.** Until
Phase 4 ships `cosmo init`, the only way to populate `projects` is `cosmo
project register`. This is expected, not a gap — see decision 2 above.

**Any new migration must go through `MIGRATIONS`, never edit `_SCHEMA_V1`.**
Once shipped, a migration's SQL is frozen (forward-only, Open Item 5). A
schema change is always `Migration(2, ...)` appended to the list.

---

## Phase 2 — Complete

All exit criteria met. 91 tests passing; `ruff`, `ruff format`, and `mypy --strict`
clean.

### What exists

| Path | Contents |
|---|---|
| `src/cosmo/proc/managed.py` | `ManagedProcess` — `Popen(..., start_new_session=True)`, non-blocking stdout/stderr drain to a raw log file, `cancel()` |
| `src/cosmo/proc/timers.py` | `WallClockTimer`, `StallTimer`, `LivenessTimers` — the two independent timers per managed run (spec 3.3) |
| `src/cosmo/proc/orphans.py` | `sweep_containers` (docker label filter + `rm -f`), `find_worktree_holders` (`/proc` scan), `sweep` (both, spec 2.4 steps 4-5) |
| `src/cosmo/proc/reap.py` | `cancel_and_reap` — ties `cancel()` + `sweep()` together and emits `task.failed`/`environment_error` with the breaker weight on a failed reap (spec 2.4 step 6) |
| `src/cosmo/doctor.py` | Adds `check_no_leaked_gate_containers` to `core_checks()` |
| `tests/fixtures/spawn_ignoring_grandchild.sh` | Shell fixture: a process with no SIGTERM trap spawns a grandchild that ignores SIGTERM and loops forever |
| `tests/fixtures/fake_docker.sh` | Recording stand-in for the `docker` CLI, including a `FAKE_DOCKER_FAIL` mode (see decision 3) |

### Decisions made during Phase 2

**1. `cancel()`'s liveness check is `killpg(pgid, 0)`, not "has our direct
child's `wait()` returned."**
The obvious-looking implementation -- `Popen.wait(timeout=grace_s)` then
escalate -- only proves the *direct* child exited. A grandchild that traps
and ignores `SIGTERM` survives its parent's death, is still in the same
process group, and is exactly the leak spec 2.4's opening paragraph
describes. `_wait_for_group_empty` polls `os.killpg(pgid, 0)` (raises
`ProcessLookupError` once no process anywhere carries that pgid) instead,
which is the only check that actually proves the whole tree is gone. This
also drives the escalation to `SIGKILL` correctly: a `SIGTERM`-only reap that
looked "done" because the direct child died would never send the `SIGKILL`
a stubborn grandchild needs.

**2. Reaping the direct child is interleaved with the liveness poll, not
deferred until the group is confirmed empty.**
The first version of `_wait_for_group_empty` deferred `Popen.wait()` to
avoid a pid-reuse race (reaping early frees the pid, which is also the pgid,
for the kernel to hand to an unrelated process while still polling). That
created a real deadlock instead: a child with *no* surviving descendants of
its own becomes a zombie the moment it exits, and only its actual parent
(us) can reap it -- nothing else ever will. `killpg(pgid, 0)` sees the
zombie as "still there" forever, so `cancel()` timed out even against a
plain `sleep` that dies cleanly on `SIGTERM` (`tests/test_proc_managed.py`
caught this immediately). Fixed by calling `self._proc.poll()` on every
polling iteration -- `Popen.poll()` reaps via `waitpid(WNOHANG)` the instant
the child exits. The pid-reuse race this reopens is real but is the same
order of risk every process supervisor using pgids accepts; not worth
architecting around for a serial, single-host v1.

**3. `sweep_containers` / `check_no_leaked_gate_containers` check
`returncode`, not just whether stdout parsed into plausible-looking lines.**
Found by manually running `cosmo doctor` on this WSL2 box, not by a unit
test: the local `docker` resolves to the Docker Desktop shim, which is
non-functional here and, on failure, prints its "command not found" banner
to **stdout** (not stderr) and exits 1. The original code treated every line
of that banner as a container id and would have called `docker rm -f` on
them. Both call sites now short-circuit to "found nothing" / "warn, docker
ps failed" on a non-zero exit rather than parsing stdout at all.
`fake_docker.sh` gained a `FAKE_DOCKER_FAIL` mode specifically to pin this
regression in tests, since the real shim's behavior can't be exercised in
CI.

**4. `ManagedProcess._drain` reads via `os.read(fd, 4096)`, not
`pipe.read(4096)`.**
A `BufferedReader.read(size)` blocks until it fills the requested size *or*
hits EOF -- it does not return early just because some bytes are available.
A short heartbeat line followed by silence would sit unflushed in the pipe
buffer for however long the harness stayed quiet, which defeats the entire
point of "non-blocking drain" the plan asks for. `os.read` mirrors the raw
POSIX `read(2)`: it returns as soon as *any* data is available, up to the
requested size. Also caught by a test hang, not by inspection.

**5. `docker`/container tests use a recording fake script, not a live
daemon.**
This sandbox's `docker` does not actually work (see decision 3) -- the
handoff's own instruction to fake `claude -p` rather than invoke it for real
extends naturally to a fake `docker`. `tests/fixtures/fake_docker.sh` logs
every invocation and returns canned `ps -q` output (or, in `FAKE_DOCKER_FAIL`
mode, a non-zero exit with stdout-only error text). A future integration
test against real Docker belongs with Phase 6's gate work, where a real
container is actually launched.

**6. `check_no_leaked_gate_containers` added to `core_checks()`, per the
handoff's "worth a look."**
Scoped narrowly: it only checks for containers still carrying
`orchestrator.run_id`, which only step 5's convention makes possible. It
does not attempt the worktree-holder half of the sweep across a process
restart (that needs the task queue's `worktree_path` column cross-referenced
against a "run in progress" concept that doesn't fully exist before Phase
8) -- left for whichever later phase actually needs it.

### Things that will matter later

**`ManagedProcess.cancel(grace_s=...)` takes the grace period as an
argument, not read from config internally.** `cancel_and_reap` is what wires
`config.timeouts.kill_grace` in. Keeping `ManagedProcess` itself
config-ignorant kept the fast unit tests fast (grace periods of 0.3s instead
of the real 20s) without needing a config fixture in every test.

**The circuit breaker is still not implemented (Phase 8, as planned).**
`cancel_and_reap` emits `task.failed` with `failure_type=environment_error`
and `circuit_breaker_weight` in the payload on a failed reap, but nothing
consumes that weight yet -- there is no breaker to trip. This matches the
handoff's explicit scope boundary; don't reach ahead of it in Phase 3 either.

**`find_worktree_holders` is a `/proc` scan, not `lsof` or `psutil`.**
No new dependency needed for a Linux-only, best-effort scan; skips any pid
it can't introspect (permission denied, exited mid-scan) rather than
raising. Untested on anything but Linux/WSL2 -- consistent with spec 2.4's
own "on POSIX" framing, so this is not considered a gap.

**Docker now works on this box (fixed 2026-08-24, after Phase 2 was written).**
Earlier in the session `docker` was on `PATH` but non-functional -- Docker
Desktop's WSL2 integration for this "Ubuntu" distro was stuck (its
per-distro setup step, which `wslexec`s into the distro to write
`~/.docker/config.json`, was timing out; Docker Desktop's own "WSL
integration ... unexpectedly stopped" dialog surfaced this and a "Restart
the WSL integration" click fixed it). `docker` now resolves to the real
integrated binary at `/usr/bin/docker`, and `sweep_containers`/`sweep()` was
verified end-to-end against a real labeled container (created, found by its
labels, force-removed) -- not just the `fake_docker.sh` unit tests. The
returncode-guard fix from decision 3 was written defensively before this was
fixed and remains correct/necessary regardless (a real `docker` can still
exit non-zero for other reasons, e.g. daemon down). No code changes were
needed once the environment was fixed. Also observed independently: this
box is usually near the 10 GB disk floor, so `cosmo doctor`'s disk check may
still show `FAIL` for reasons unrelated to whatever change is under test.

## Phase 3 — Complete

All exit criteria met. 118 tests passing; `ruff`, `ruff format`, and `mypy --strict` clean.

### What exists

| Path | Contents |
|---|---|
| `src/cosmo/harness/base.py` | Adds `HarnessAdapter.cwd` (constructor arg, defaults to `Path.cwd()`) and the abstract `probe(prompt)` method |
| `src/cosmo/harness/claude/__init__.py` | Re-exports `ClaudeCodeAdapter`, `BILLING_ENV_VAR` |
| `src/cosmo/harness/claude/adapter.py` | `ClaudeCodeAdapter` -- full `propose`/`implement`/`probe`/`get_progress`/`cancel`, argv/env construction, `preflight()` (carried over from Phase 0) |
| `src/cosmo/harness/claude/stream.py` | `NdjsonLineBuffer`, `classify_line`, `StreamReader` -- the stream-json reader and classifier |
| `src/cosmo/harness/fake/__init__.py`, `adapter.py` | `FakeHarnessAdapter`, `FakeOutcome`, `ScriptedCall` -- registered in the harness registry as `"fake"` |
| `src/cosmo/harness/registry.py` | Now registers both `claude` and `fake` |
| `src/cosmo/proc/managed.py` | Adds `ManagedProcess(..., on_stdout_chunk=...)` -- a tee of the stdout drain, not a second reader of the fd |
| `src/cosmo/cli/main.py` | Adds `cosmo harness probe --prompt TEXT [--timeout SECONDS]` |
| `tests/fixtures/fake_claude.sh` | Recording stand-in for the `claude` CLI, mirrors `fake_docker.sh` |
| `tests/fixtures/stream_json/*.ndjson` | `normal_run`, `tool_call`, `api_retry`, `truncated`, `malformed` fixtures |

Working commands (new):

```
cosmo harness probe --prompt "print hello" [--harness NAME] [--timeout SECONDS] [--config PATH]
```

### Decisions made during Phase 3

**1. `HarnessAdapter.cwd` — extension to spec §2.2, alongside `preflight()`.**
Every subprocess-based adapter needs a working directory to launch its child
in, and `cancel()`'s orphan sweep (spec 2.4 step 4) needs a worktree path to
check for surviving holders. Phase 5 doesn't exist yet, so there is no real
worktree lifecycle to ask. Per the handoff's own suggestion, `cwd` is a
constructor argument (`HarnessAdapter.__init__(config, *, cwd=None)`,
defaulting to `Path.cwd()`), harness-agnostic, living in the base class
rather than being invented per-adapter. `ClaudeCodeAdapter.cancel()` passes
`self.cwd` as `cancel_and_reap`'s `worktree_path`.

**2. `probe(prompt) -> HarnessResult` — a third extension to spec §2.2.**
`propose`/`implement` both presuppose an OpenSpec change on disk; the plan's
own exit criterion (`cosmo harness probe --prompt "print hello"`) needs a
harness-agnostic raw-prompt entry point that doesn't. Added as an abstract
method next to `preflight()`, implemented by both adapters. `cosmo harness
probe` (in `cli/main.py`) stays harness-agnostic by calling it generically.

**3. Adapter constructor also takes optional `run_id`/`emitter` (`ClaudeCodeAdapter` only).**
Phase 8's run loop is what will normally supply these. Without them,
`cancel()` still kills the process (spec 2.4 steps 1-3, via a bare
`ManagedProcess.cancel()`) but skips `cancel_and_reap`'s orphan sweep and
`task.failed` emission. With them, `cancel()` routes through
`cancel_and_reap` for the full spec 2.4 sequence. This degrades gracefully
rather than crashing when nothing has wired a store/emitter yet -- true of
every call site before Phase 8 exists (`cosmo harness probe` included).

**4. `_invoke()` imposes no timeout of its own; `cosmo harness probe` does, externally.**
`has_internal_timeout=False` means Cosmo's orchestration layer owns the wall
clock (spec 3.3), and the adapter genuinely has no correct value to use even
if it wanted one -- it doesn't know which task-state wall clock
(proposing/implementing/validating, each configured separately) applies to a
given call. `ClaudeCodeAdapter._invoke()` therefore blocks on a plain
`process.wait()` with no timeout; it is unblocked only by another thread
calling `cancel()`, which is what actually kills the child. `cosmo harness
probe` demonstrates the pattern Phase 7/8 will need for real: it runs the
adapter call on a background thread, joins with a timeout
(`--timeout`, default `timeouts.proposing_wall`), and calls `adapter.cancel
("probe")` if the join times out.

**5. `on_stdout_chunk` on `ManagedProcess` is a tee on the existing drain thread, not a second reader.**
Considered three options (handoff explicitly flagged this as a real design
decision): a second consumer of the raw log file, a tee at the
`ManagedProcess` level, or extending `ManagedProcess` itself. Chose the tee:
a second reader of the same fd would race the drain thread for bytes, and
re-reading the log file introduces a filesystem-polling consumer with no
clean "caught up" signal. `on_stdout_chunk` (stdout only; stderr is untouched)
is called synchronously on the drain thread itself, so a caller that later
joins `_drain_threads` (any call to `ManagedProcess.cancel()`, including the
fast no-op path on an already-exited process) has a genuine
happens-before relationship with the callback's last invocation --
`StreamReader.feed` needs no lock of its own as a result.

**6. `ClaudeCodeAdapter` always calls `process.cancel(grace_s=...)` after `process.wait()`, even on a clean exit.**
Established by `test_cancel_on_an_already_exited_process_returns_true` in
Phase 2 as the correct idiom, not invented here: `cancel()` is what joins the
drain threads (`ManagedProcess._finalize`), and it's cheap/idempotent on an
already-exited process. Skipping this on the "normal" path would leave
`StreamReader`'s last chunk(s) possibly unflushed when `_invoke` reads
`reader.terminal_result` immediately after.

**7. `FakeHarnessAdapter` is a first-class registered adapter (`harness/fake/`), not a test fixture.**
The plan's own layout puts `fake/` beside `claude/` under `harness/`, not
under `tests/fixtures/`. Registered in `_REGISTRY` as `"fake"` so `--harness
fake` works from the CLI too (e.g. `cosmo doctor --harness fake`), which
costs nothing (it's inert) and gives every later phase's tests a harness
selectable the same way the real one is. Scriptable via `script=` (a
`ScriptedCall` or a sequence consumed in order, last one repeating);
`FakeOutcome.HANG` blocks on a `threading.Event` set only by `cancel()`,
simulating a stuck harness that only responds to cancellation -- not a fixed
`sleep()`, which would race whatever the test is timing.

**8. Real `stream-json` output was captured before writing the classifier, not guessed.**
Per the project's established "check with a real invocation" convention
(Phase 2's two worst bugs were both invisible to unit tests written from
inspection). Ran `claude -p "print hello" --output-format stream-json
--verbose --max-turns 3 --permission-mode dontAsk < /dev/null` for real
(CLI 2.1.207) and designed `stream.py` against the actual line shapes. This
surfaced deviation #5 below (`rate_limit_event` vs. the spec's
`system/api_retry`) that guessing would not have caught. No tool-call
content-block fixture was captured this way (the smoke prompt didn't invoke
any tool) -- `tests/fixtures/stream_json/tool_call.ndjson` is hand-derived
from the documented Claude message content-block shape instead, flagged as
such in the fixture's consuming test.

**9. Prompt content for `propose`/`implement` is deliberately thin.**
Spec §2.2 says these methods "run" OpenSpec's propose/apply steps, but no
section in Phase 3's scope (§2.1-2.3, §4, §7.2) specifies the actual prompt
text, and Phase 4's harness-facing `CLAUDE.md` (§10.3) is what's meant to
carry the real operating policy (how to invoke OpenSpec, guardrails, etc.).
`propose()`/`implement()` construct a minimal prompt naming the spec path,
task id, and retry context, and rely on Phase 4's template to fill in the
procedural knowledge. Revisit once Phase 4 exists.

**10. `files_changed` on `HarnessResult` is always `[]` from this adapter.**
No source of truth exists before Phase 5 gives Cosmo a worktree to `git
diff` against; the stream itself only reports tool *names*, not file paths
touched. Left empty rather than parsed out of tool-call `input` payloads,
which would be guessing at argument shapes per-tool.

### Things that will matter later

**A headless `claude -p` run inherits the *operator's* full user-level Claude
Code config -- global hooks, plugins, MCP servers -- not just the target
repo's.** Observed directly during the real probe capture: the child process
ran this box's own `engram` plugin's `SessionStart` hook (visible as
`hook_started`/`hook_response` events in the raw log) even though the probe's
`cwd` was `/tmp`, nothing to do with the cosmo project. For a droplet with a
clean `~/.claude` this is probably fine; for a developer box it means
unattended runs pull in whatever plugins/hooks happen to be configured
globally, with unknown token cost and side effects. Not fixed here --
Phase 4 owns `.claude/settings.json`/hook installation and is the natural
place to decide whether the child needs `HOME`/`XDG_CONFIG_HOME` isolation
or a `--settings` override to prevent this.

**`get_progress()` on `ClaudeCodeAdapter` still raises `NotImplementedError`, deliberately.**
`reports_native_progress=False` means core should never call it for real --
progress comes from watching `tasks.md` (Phase 7). `FakeHarnessAdapter.
get_progress()` is a real, settable implementation (`set_progress()`) since
later phases' tests will want to script it.

**`ClaudeCodeAdapter._running` is a plain `dict` guarded by one `threading.Lock`,**
covering exactly the concurrent-`cancel()`-during-`_invoke()` case Phase 3
needs. `ManagedProcess.cancel()` itself already tolerates being called twice
(once from `_invoke`'s `finally`, once from an external `cancel()`) via its
existing `ProcessLookupError` handling -- no additional synchronization was
added there.

**Getting a pgid for an already-separately-reaped direct child is a latent gap, not new here.**
If a child leaves a live grandchild running in its process group *after* the
direct child has already been `wait()`-ed on elsewhere, a later
`os.getpgid(that_pid)` can raise `ProcessLookupError` even though the group
technically still has members -- `getpgid` needs the exact pid it's given to
still exist. `ClaudeCodeAdapter`'s normal-exit path (`wait()` then
`cancel()`) matches the exact sequence Phase 2's own
`test_cancel_on_an_already_exited_process_returns_true` already covers, so
this isn't a new risk Phase 3 introduces -- just noted in case a future
phase sees an orphaned grandchild survive a clean-exit reap.

**`cosmo doctor`/`cosmo project register` are unaffected by the `cwd`/`run_id`/`emitter` additions** --
`get_adapter(name)(cfg)` still constructs an adapter with just `config`;
every new constructor argument defaults to `None`/`Path.cwd()`.

### Post-Phase-3 fix, found during manual testing

**`cosmo <command> --config <missing-file>` silently fell back to shipped
defaults instead of erroring.** Found by running `cosmo doctor --harness fake
--config /nonexistent` by hand and noticing it produced output identical to
omitting `--config` entirely. Root cause: `load_config()` (Phase 0) treats
*any* missing config path -- whether it's the computed XDG default (where
absence is legitimate: a fresh install has no user config yet) or an
explicit `--config` flag (where absence is almost always a typo) -- as "no
override, use defaults," with no distinction between the two callers.

**Not fixed inside `load_config()` itself.** Tests across Phases 0-3
(`test_doctor.py`, `test_proc_reap.py`, `test_harness_claude_adapter.py`,
`test_harness_fake.py`, ...) deliberately pass an explicit
`Path("/nonexistent/config.toml")` to `load_config()` directly as their
isolation idiom -- it's how they force "shipped defaults only" without
touching the developer's real `~/.config/cosmo/config.toml` or needing the
`monkeypatch`-based env isolation `test_cli.py` uses instead. Making
`load_config()` raise on a missing explicit path would have broken that
idiom across every one of those files.

**Fixed one layer up, in `cli/main.py`'s `_load()`** -- the CLI-facing
wrapper is the only place that knows *which* case it's in (a `--config` flag
was typed vs. nothing was typed at all). It now checks
`config_path.is_file()` itself before calling `load_config()`, and exits 2
with a clear message if an explicitly-named file doesn't exist. `load_config()`'s
own behavior, and every test that relies on it, is unchanged. Regression
test: `test_explicit_config_flag_naming_a_missing_file_fails_loudly` in
`tests/test_cli.py`.

**The engram/`SessionStart`-hook-inheritance finding (see above) was
independently reproduced by manual testing and confirmed still open.** User
decision: leave it for Phase 4 rather than attempting a partial mitigation
now -- consistent with the reasoning already recorded above (a real fix
needs `--settings`/`XDG_CONFIG_HOME` isolation that doesn't also break
Pro/Max subscription auth, which needs real experimentation Phase 4 is
better positioned to do once it owns `.claude/settings.json` anyway).

## Phase 4 — Complete

All exit criteria met. 181 tests passing; `ruff`, `ruff format`, and `mypy --strict`
clean.

### What exists

| Path | Contents |
|---|---|
| `templates/harness/claude/settings.json` | `permissions.deny` for secret paths + `PreToolUse` hook wiring, `timeout: 5000` each |
| `templates/harness/claude/hooks/_hooklib.py` | Shared stdlib-only helpers (stdin JSON, deny/allow, the read-only `allow_test_edits` lookup) -- same-directory import, travels with the hooks it's imported by |
| `templates/harness/claude/hooks/test_path_guard.py` | Denies `Edit`/`Write`/`NotebookEdit` under `src/test/**`, `e2e/**`, `**/*.spec.ts`, `**/*.test.ts` unless `allow_test_edits` |
| `templates/harness/claude/hooks/annotation_guard.py` | Denies an `Edit`/`Write` that *introduces* `@Disabled`/`@Ignore`/`test.skip`/`it.skip`/`describe.skip`/`xit(` (before/after occurrence-count comparison, not a flat substring match) |
| `templates/harness/claude/hooks/commit_integrity_guard.py` | Denies `git commit --no-verify`, any `git push`, `git reset --hard` inside `Bash` calls |
| `templates/harness/claude/CLAUDE.md`, `agents/implementer.md`, `skills/openspec-workflow/SKILL.md` | Cosmo's harness-facing operating policy -- Open Item 4's "concrete contents" |
| `templates/projects/_blank/docs/**`, `templates/projects/java-spring-react/docs/**` | Schema-only vs. real-starter-content docs, per spec 10.3's file list |
| `src/cosmo/bootstrap/discover.py` | `templates_root()`, `harness_template_dir()`, `project_template_dir()`, `list_templates()` |
| `src/cosmo/bootstrap/hashing.py` | `compute_template_version()` -- sha256 of a sorted `relpath filehash` manifest |
| `src/cosmo/bootstrap/assets.py` | `sync_harness_assets()` -- the one function, two call sites (spec 10.5) |
| `src/cosmo/bootstrap/symlinks.py` | `create_root_symlinks()` -- relative-only, refresh-not-clobber |
| `src/cosmo/bootstrap/docs.py` | `copy_project_docs()` -- never-overwrite, `--force` as a caller decision |
| `src/cosmo/bootstrap/openspec.py` | `ensure_openspec_initialized()` -- real subprocess call to `openspec init` |
| `src/cosmo/bootstrap/init.py` | `run_init()` -- orchestrates spec 10.4 steps 1-7 |
| `src/cosmo/cli/main.py` | Adds `cosmo init`, `cosmo templates list` |
| `src/cosmo/harness/claude/adapter.py` | Adds `--setting-sources project` to argv; adds `COSMO_TASK_ID`/`COSMO_DB_PATH` to the child env |
| `tests/fixtures/fake_openspec.sh` | Recording stand-in for `openspec`, mirrors `fake_docker.sh`/`fake_claude.sh` |

Working commands (new):

```
cosmo init <path> [--harness NAME] [--project-template NAME] [--force] [--config PATH]
cosmo templates list
```

### Decisions made during Phase 4

**1. `openspec init` is invoked with `--tools none`, never `--tools claude`.**
Probed by hand before writing any code (per this codebase's "check with a
real invocation" convention): `openspec init --tools claude` writes a real
`.claude/commands/opsx/*.md` and `.claude/skills/openspec-*/SKILL.md` tree of
its own. That directly conflicts with spec 10.2's `.claude -> .agent/claude`
symlink -- a real directory and a symlink cannot occupy the same path.
Cosmo's own `templates/harness/claude/` is the harness-facing integration;
OpenSpec's role in `cosmo init` is `openspec/` only. `templates/harness/
claude/skills/openspec-workflow/SKILL.md` is what replaces OpenSpec's own
generated skill content, written against the real CLI's actual surface
(`openspec status --change`, `openspec instructions <artifact> --change`,
`openspec new change`, `openspec validate`), not guessed.

**2. `--setting-sources project` added to `ClaudeCodeAdapter._build_argv`.**
This resolves the open finding carried from Phase 3 (a headless run
inheriting the operator's global `~/.claude` hooks/plugins) -- found by
reading `claude --help` for an existing mechanism rather than building
`HOME`/`XDG_CONFIG_HOME` isolation from scratch, which the Phase 3 handoff
worried could break Pro/Max auth. **Verified by a real invocation, both
directions:** with the default (all scopes), this box's own global
`SessionStart`/`UserPromptSubmit`/`Stop` hooks fired even with `cwd=/tmp`;
with `--setting-sources project`, none of them fired, and a project-scoped
`PreToolUse` hook in `.claude/settings.json` still fired and correctly
denied a `Write` call. `local` (the gitignored personal-override scope) is
excluded too -- it shouldn't exist at all in an unattended run.

**3. `COSMO_TASK_ID` / `COSMO_DB_PATH` -- the env vars the handoff asked to
be decided.** Set on the child process by `ClaudeCodeAdapter._build_env`.
The test-path guard hook reads `task_queue.allow_test_edits` for the running
task via a genuine read-only `sqlite3` connection
(`file:{path}?mode=ro`), stdlib only, no `cosmo` package import -- a hook
is a separate OS process running inside an arbitrary target repo, so it
cannot call into `StoreWriter` in-process, and cannot assume `cosmo` is
importable there at all. **Fails closed**: missing env vars, a missing
database, a missing row, or any query error all resolve to "not allowed",
never to "allowed" -- pinned by
`test_missing_db_env_vars_fail_closed_and_still_deny`.

**4. Hooks are self-contained Python (stdlib only), not shell.** Consistent
with this project's own language and testable the same way
(`subprocess.run(["python3", hook_path], input=json...)`), while staying
"local, synchronous, no network, no LLM" (spec 2.5). `_hooklib.py` is
imported via a same-directory `sys.path` insert rather than packaged --
`sync_harness_assets` copies the whole `hooks/` directory as one unit, so
this stays a plain file dependency, not a packaging problem.

**5. `template_version` = sha256 of a sorted `"relpath filehash"` manifest.**
Spec 9.2 asks for "a hash of the source template tree" without saying how;
recorded and justified in `bootstrap/hashing.py`'s docstring rather than left
implicit. Sorting first makes the result independent of filesystem
iteration order; hashing content (not mtime) means a byte-identical copy
produces an identical version.

**6. `__pycache__`/`*.pyc` excluded from both hashing and `sync_harness_assets`'s
copytree -- found by hand, not by a unit test.** Running the shipped hook
scripts at all (including this project's own test suite exercising them via
subprocess) leaves `hooks/__pycache__/_hooklib.cpython-*.pyc` next to
`_hooklib.py`. Running `cosmo init` for real against a scratch repo
surfaced this concretely: the bytecode was silently part of both the hashed
tree and the copied `.agent/claude/hooks/`, which would have made
`template_version` depend on which Python build last ran a hook locally.
Already gitignored so it never reaches version control, but hashing/copying
still needed the explicit exclusion (`_IGNORED_DIR_NAMES` /
`ignore=shutil.ignore_patterns(...)`). Regression test:
`test_pycache_artifacts_are_excluded_from_the_hash`.

**7. `templates_root()` resolves relative to the installed package's own file
location** (`Path(cosmo.__file__).resolve().parent.parent.parent /
"templates"`), not `importlib.resources` -- `templates/` lives at the repo
root, alongside `src/`, not inside the installed package, so
`importlib.resources` (which only covers files shipped *inside* a package)
doesn't apply. This resolves correctly for the documented install method
(`uv tool install --editable .`) and is documented as *not* solving a future
packaged/wheel distribution, which would need `templates/` shipped as real
package data instead -- deliberately deferred, not a Phase 4 gap.

**8. Root symlinks refresh-if-symlink, never clobber a real file/directory.**
`create_root_symlinks` only removes and recreates a link path that is
already a symlink; a real file or directory already at that path (e.g. a
developer's own `CLAUDE.md`, pre-Cosmo) is left untouched and reported
`skipped_conflict`. Spec 10.4 step 5 says "create or refresh" without
addressing this case; erring toward not destroying content the developer
may have written themselves.

**9. `bootstrap/symlinks.py` added to `ALLOWED_HARNESS_AWARE`
(`test_harness_boundary.py`), per the Phase 0 state doc's own instruction
("add it to the allowlist rather than weakening the test").** Which root
paths a harness expects symlinked (`.claude`, `CLAUDE.md`, `agents`,
`skills` for Claude) is genuinely per-harness knowledge, the same shape as
the adapter/registry entries already on that list -- not a boundary leak.

**10. `cosmo init` re-run is idempotent by construction, not by a special
"already initialized" branch.** `openspec init` is itself idempotent
(confirmed by hand -- re-running against an existing `openspec/config.yaml`
is a safe no-op, with or without `--force`); `copy_project_docs` never
overwrites by default; `sync_harness_assets` always replaces `.agent/`
wholesale by design (spec 10.5 -- that's the point, not a re-run special
case); only project registration needed an explicit idempotency check
(`find_project_by_path` before `register_project`, since `projects.
target_path` is `UNIQUE` and Phase 1 built no upsert path). `run_init` and
`cosmo init` both stayed thin as a result -- one `if` for the one genuinely
non-idempotent step.

**11. `cosmo project register` is kept, not deprecated, once `cosmo init`
exists.** Phase 1's own state doc entry called this decision out in
advance ("treat this as the persistence primitive it already has"); `cosmo
init`'s registration step calls the same `StoreWriter.register_project`
rather than duplicating it. `cosmo project register` remains useful as a
lower-level primitive (e.g. registering a project that was bootstrapped by
hand, before `cosmo init` existed, or outside Cosmo's own template flow
entirely).

**12. The manual adversarial exit criterion was run against the real CLI, not
simulated.** `cosmo init` was run against a real scratch git repo; a real
`claude -p --setting-sources project` invocation against that repo was
prompted to (a) edit `src/test/java/AppTest.java` and (b) run `git commit
--no-verify`. Both were denied -- confirmed by the unmodified file on disk
and by `permission_denials` in the terminal `result` object showing the
`Bash` call never reached `git` at all. Full transcript reasoning recorded
in this session's log, not reproduced here.

### Things that will matter later

**Phase 5's worktree-creation call site is a real seam, not a TODO comment.**
`sync_harness_assets(target, harness, *, emitter, run_id=None, ...)` already
accepts `run_id` specifically for Phase 5 (a per-task sync has a real
`run_id`; `cosmo init` does not) -- Phase 1's `EventEmitter` sequence
scoping (`run_id or ""`) already handles both without a schema change.

**`bootstrap/init.py`'s `run_init` takes both `writer: StoreWriter` and a
separate `db_path: Path`.** `find_project_by_path` (Phase 1) opens its own
short-lived read connection rather than sharing the writer's -- consistent
with the existing single-writer/read-your-own-connection discipline
(`store/reader.py`'s whole design), but means callers hand over the path
twice. Not worth a `StoreWriter.db_path` property for one caller; revisit if
a third caller wants it.

**`docs/data-model.md` in `java-spring-react` is deliberately about the
convention, not fabricated entities.** No real product schema exists yet to
document truthfully; inventing one would be exactly the kind of note-rot
spec 11 warns about (a doc a future task takes as fact when it isn't).

**Doctor was not extended in this phase.** No new `cosmo doctor` check was
added for template staleness or a registered project's `.agent/` drift --
nothing in the plan's Phase 4 exit criteria asked for one, and Phase 5's
per-task sync (once it exists) makes staleness self-correcting rather than
something a preflight check needs to catch. Revisit only if a real gap
shows up in practice.

## Deviations from the spec, cumulative

Kept here so a future spec revision can absorb them in one pass.

| # | Deviation | Spec ref | Phase | Rationale |
|---|---|---|---|---|
| 1 | `preflight()` added to the adapter interface | §2.2 | 0 | Adapters must declare their own preconditions; core cannot know them |
| 2 | `validate()` not on the adapter interface | §2.2 | 0 | Contradicts §2.2's own statement that validation bypasses the harness |
| 3 | State paths default to XDG, not `/var/cosmo` | §3.2 | 0 | `/var` needs root on WSL2; droplet overrides via config |
| 4 | `cosmo project register` CLI added, ahead of `cosmo init` | §10.4 | 1 | The `projects` table (step 6) needs a populator before Phase 4's full bootstrap exists; this is the persistence primitive only, no templates/symlinks |
| 5 | `claude -p`'s primary quota signal is a top-level `rate_limit_event`, not `system/api_retry` | §7.2, §4 | 3 | Observed on a real CLI 2.1.207 probe run, not documented anywhere upstream. Both shapes are classified as `RATE_LIMIT` so either is caught regardless of CLI version |
| 6 | `cwd` added to the adapter base constructor | §2.2 | 3 | Every subprocess adapter needs a working directory; Phase 5's worktree lifecycle doesn't exist yet to supply one |
| 7 | `probe(prompt)` added to the adapter interface | §2.2 | 3 | `cosmo harness probe`'s exit criterion needs a harness-agnostic raw-prompt entry point; `propose`/`implement` both presuppose an OpenSpec change on disk |
| 8 | `openspec init` invoked with `--tools none`, never `--tools claude` | §10.4 | 4 | `--tools claude` writes a real `.claude/commands`/`.claude/skills` tree that conflicts with the spec's own `.claude` symlink (§10.2); found by hand |
| 9 | `--setting-sources project` added to the Claude adapter's argv | §2.3 | 4 | Not named in the spec's invocation description at all; fixes the Phase 3 global-config-inheritance finding, verified by a real invocation |
| 10 | `COSMO_TASK_ID` / `COSMO_DB_PATH` env vars added to the child process | §2.5 | 4 | The test-path guard hook is a separate OS process with no other way to read `allow_test_edits`; the handoff explicitly left this variable's name undecided |
