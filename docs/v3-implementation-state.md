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
| Head commit | `b1b4d98` — Phase 6 (Phase 7 not yet committed) |
| Spec | [v3-cosmo-autonomous-agent-spec.md](v3-cosmo-autonomous-agent-spec.md) |

## Phase status

| Phase | Status |
|---|---|
| 0 — Repository skeleton and configuration | **Complete** |
| 1 — Persistent state and the event log | **Complete** |
| 2 — Process supervision | **Complete** |
| 3 — Harness abstraction and Claude Code adapter | **Complete** |
| 4 — Template system and `cosmo init` | **Complete** |
| 5 — Worktree lifecycle and git operations | **Complete** |
| 6 — Validation gate | **Complete** |
| 7 — Task state machine | **Complete** |
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

## Phase 5 — Complete

All exit criteria met. 201 tests passing (181 carried forward + 20 new);
`ruff`, `ruff format`, and `mypy --strict` clean.

### What exists

| Path | Contents |
|---|---|
| `src/cosmo/git/worktree.py` | `create_worktree()` (`git worktree add` + `sync_harness_assets` + gitleaks hook install + `worktree_path` write), `remove_worktree()`, `sweep_stale_worktrees()` |
| `src/cosmo/git/secrets.py` | `install_gitleaks_pre_commit_hook()` -- the spec 6.1 hook, idempotent, marker-based refresh-not-clobber |
| `src/cosmo/git/merge.py` | `attempt_merge_ladder()` (pure git mechanics, spec 3.4) and `merge_task()` (ties the ladder to `StoreWriter`/`EventEmitter`) |
| `src/cosmo/store/writer.py` | Adds `queue_set_worktree_path()`, `queue_complete()` |
| `src/cosmo/config/model.py`, `defaults.toml` | `GitConfig.commit_author_name` / `commit_author_email` |
| `src/cosmo/doctor.py` | `gitleaks` added to `core_checks` |
| `tests/test_git_worktree.py`, `test_git_secrets.py`, `test_git_merge.py`, `test_git_boundary.py` | All against real `git`; `test_git_secrets.py`'s real-scan tests skip if `gitleaks` isn't on PATH (same posture as Phase 4's `openspec` tests) |

No CLI command was added for any of this, deliberately -- see decision 5 below.

### Decisions made during Phase 5

**1. `gitleaks` hooks are shared across a repo's worktrees, not genuinely
per-worktree -- confirmed by hand before writing any code.** `git rev-parse
--git-path hooks`, run from any linked worktree, resolves to the *same*
common `.git/hooks/` directory as the main checkout (there is no per-worktree
hooks directory in git at all). Spec 6.1's "a gitleaks pre-commit hook in
each worktree" is satisfied by installing once, idempotently, on every
`create_worktree()` call -- cheap, and self-healing if the file is ever
deleted. `install_gitleaks_pre_commit_hook()` resolves the hooks dir via
`git rev-parse --git-path hooks` rather than assuming `<repo>/.git/hooks`,
so this keeps working even if `repo_path`'s `.git` layout is ever unusual.

**2. The hook fails closed on a missing `gitleaks` binary** (refuses the
commit rather than silently skipping the scan), mirroring the Phase 4
test-path-guard hook's posture. `cosmo doctor` now checks for `gitleaks` on
PATH (`core_checks`, alongside `git`/`docker`/`openspec`) so this is a
preflight-visible surprise, not a silent one at commit time. This box had no
`gitleaks` installed at the start of this phase; installed via the upstream
release tarball to `~/.local/bin` for real-invocation testing (not via
`apt`, which needed interactive `sudo` this sandbox doesn't have) --
`test_installed_hook_blocks_a_commit_containing_a_secret` and its
clean-commit counterpart exercise the real binary, skipped automatically
where it's absent.

**3. The hook installer never clobbers a pre-existing, non-Cosmo
`pre-commit` hook.** Detected via a `HOOK_MARKER` comment line written by
Cosmo itself -- present means safe to overwrite (idempotent refresh),
absent means some other tool owns that file (`husky`, `pre-commit`
framework, a developer's own script) and installation reports
`skipped_conflict` rather than destroying it. Same refresh-not-clobber
posture `bootstrap/symlinks.py` (Phase 4) uses for root-level symlinks.

**4. `repo_path` is Cosmo's own dedicated checkout of `base_branch`, and the
merge ladder runs directly against it -- there is no separate "integration
worktree."** The first design attempted was a throwaway worktree checked out
on `base_branch` solely for the merge, specifically so `repo_path`'s own
working tree would never be touched. That's impossible: git refuses to check
out a branch that's already checked out in another worktree (confirmed by
hand), and `base_branch` is already checked out in `repo_path` itself in
normal operation. `attempt_merge_ladder()` therefore asserts `repo_path` is
on `base_branch` with a clean `git status` before doing anything, and raises
`MergeCommandError` (an environment-shaped error, not a merge conflict) if
that precondition doesn't hold. Practical implication for Phase 7/8: nothing
else may ever check out a different branch in `repo_path`, or leave
uncommitted changes there -- `repo_path` is not a place for interactive
human use while Cosmo is running (task work always happens in the isolated
linked worktrees; `docs/` being "edited directly in the target repo",
spec 10.1, does not require touching this specific checkout).

**5. Constructing the "rebase recovery succeeds" exit-criterion scenario
needed a real, deliberately-chosen git mechanism, not just "two branches
touch the same file."** Verified by hand (a real experiment, reproduced in
`test_git_merge.py`'s module docstring and its first test) before writing
any ladder code: for a plain divergent edit on the same line, `git merge`
and `git rebase` onto the same target hit the *identical* conflict --
rebase's default merge-backend uses the same 3-way logic per commit, so
there is no "rebase magically resolves what merge couldn't" for a simple
case. The reliable, well-documented mechanism that *does* produce the
asymmetry: a task branch whose first commit is byte-identical (same diff) to
a commit already merged into `develop` gets silently skipped by `git
rebase`'s empty-commit / patch-id detection, while a flat `git merge` of the
un-rebased branch still sees a real disagreement against `develop`'s current
tip and conflicts. This is the scenario `test_git_merge.py` builds; it is
not a contrived edge case invented for the test -- it is exactly the shape a
real "two tasks edited the same line, one already landed" conflict takes.

**6. No CLI command was added for worktree lifecycle or the merge ladder.**
Same posture Phase 2 took toward `proc.orphans.sweep()`/`proc.reap
.cancel_and_reap()`: these are functions with real test coverage against
real `git`, awaiting the run loop (Phase 7/8) as their real caller, not a
CLI stand-in. `cosmo harness probe` (Phase 3) was a deliberate exception
because harness probing has independent diagnostic value standalone; git
worktree/merge operations don't -- they only make sense as part of driving
an actual task through the state machine.

**7. `attempt_merge_ladder()`/`merge_task()` never import `cosmo.harness`,
enforced by `tests/test_git_boundary.py` via `ast`-based import inspection
(not a text search, which would false-positive on this module's own
docstring explaining the invariant).** This is what makes spec 3.4 step 2
("the conflict is never handed back to the agent to resolve blind")
structural: there is no harness adapter anywhere in scope on this code path
for a conflict to be handed to. The same test file also asserts `master` is
never named as a merge target anywhere under `src/cosmo/` (spec 3.2's own
exit criterion), case-insensitively, permitting only full-line `#` comments
explaining the exclusion.

**8. `check_work_dir_filesystem` (Phase 0's `/mnt/c` WSL2 warning) needed no
changes now that worktrees are actually created under `config.paths
.work_dir` for real.** It already inspects the exact path worktrees land in;
Phase 0's check was correctly anticipatory.

### Things that will matter later

**`create_worktree()` will raise `WorktreeError` on a duplicate branch name**
(`git worktree add -b task/<spec-id>` fails if that branch already exists --
e.g. a task retried after a crash left the old branch around). Phase 5 does
not resolve this: branch-name collision handling on retry is a Phase 7
concern (it owns `FAILED_RETRY`/attempt-numbering), not a git-mechanics one.
`sweep_stale_worktrees()` deletes the branch for any worktree it prunes when
it can determine the branch name (`git worktree list --porcelain`), which
covers the common case, but a task retried *without* an intervening startup
sweep could still hit this. Worth a real end-to-end check once Phase 7
exists.

**`queue_complete()` and `queue_set_worktree_path()` are plain column
writers, not part of a `task.state_changed` sequence.** Phase 7 owns "every
transition persisted and emitting `task.state_changed`" (plan Phase 7); the
Phase 5 exit criteria only asked for `task.completed`/`task.blocked` around
the merge outcome, which `merge_task()` emits directly, matching the
existing `cosmo queue block` CLI command's precedent (it also emits
`task.blocked` without a paired `task.state_changed`).

**The "commit step" named in the plan's Phase 5 bullet is the *agent's* own
`git commit`, already covered by Phase 4** (`CLAUDE.md`'s "Committing"
section + `commit_integrity_guard.py`'s `--no-verify` denial) -- Phase 5
builds no separate commit primitive of its own. `COMMITTING`'s spec 11
knowledge-file step (append 2-3 lines, enforce the 400-line cap) is
explicitly Phase 7's (plan Phase 7: "`COMMITTING` also runs the §11
knowledge step"). The merge ladder's only precondition is "the task
branch's HEAD already has the committed work," which every Phase 5 test
satisfies explicitly before calling into it.

## Phase 6 — Complete

All exit criteria met against a **real Docker daemon**, not just unit tests:
`cosmo validate <worktree>` against the fixture repo produces a full
structured result for a green run, a compile failure, a unit failure, an
e2e failure, an injected flaky test (correctly classified `flaky`, gate
still passes), and a deliberately weakened test (caught by the diff gate
before any container ran). `./check.sh` (238 tests, 6 skipped) stays at
~15s; the 6 skipped are the real-Docker regression tests, opt-in only (see
decision 9 below). 244 tests total (238 carried/new fast + 6 opt-in slow).

### What exists

| Path | Contents |
|---|---|
| `src/cosmo/gate/types.py` | `TestCounts`, `FailingTest`, `StageResult`, `DiffGateViolation`, `DiffGateResult`, `GateResult` -- the spec 9.2/9.3 payload shapes |
| `src/cosmo/gate/docker_runner.py` | Raw `docker` subprocess mechanics: `container_flags` (spec 1.1's `--ipc=host`/`--shm-size`/labels), `run_container` (foreground `--rm`), `run_detached_service`/`stop_service`/`service_logs`/`published_port` (long-lived e2e services), `create_network`/`remove_network`, `wait_for_http` |
| `src/cosmo/gate/parsers.py` | `parse_maven_surefire_reports` (reads `target/surefire-reports/*.txt`, not console output), `parse_vitest_json`, `parse_playwright_json` |
| `src/cosmo/gate/diffgate.py` | `compute_diff` (real `git diff --unified=0 <base>...<branch>`), `run_diff_gate` (spec 6.1 layer 2: modified/deleted test files, net assertion count, skip annotations, LOC drop) |
| `src/cosmo/gate/quarantine.py` | `load_quarantine`/`append_quarantine_candidate` (spec 6.4), bundled defaults at `src/cosmo/gate/data/{quarantine,quarantine-candidates}.yml` |
| `src/cosmo/gate/flaky.py` | `confirm_by_rerun`, `maybe_escalate_to_quarantine_candidate` (spec 6.4) |
| `src/cosmo/gate/error_detail.py` | `build_stage_error_detail`/`build_diff_gate_error_detail` (spec 9.3, size-capped) |
| `src/cosmo/gate/runner.py` | `run_validation_gate` -- the whole spec 1.2 sequence: diff gate → gitleaks → build → unit → e2e, pure mechanics, no `StoreWriter`/`EventEmitter` |
| `src/cosmo/gate/validate.py` | `validate_task` -- ties `run_validation_gate` to `StoreWriter`/`EventEmitter` (spec 9.2's `task.validation_result`, spec 9.3's `task_failures` row), the Phase 7/8 seam |
| `src/cosmo/gate/fake.py` | `FakeGate`, `ScriptedGateResult`, `FakeGate.as_gate_rerun()` |
| `src/cosmo/git/secrets.py` | Adds `run_gitleaks_scan` (spec 6.1's gate-side backstop) alongside Phase 5's pre-commit hook |
| `src/cosmo/store/writer.py` | Adds `record_task_failure` -- `task_failures`' first real writer |
| `src/cosmo/store/reader.py` | `list_events` gains an `event_type` filter (needed by flaky escalation's cross-run history query) |
| `src/cosmo/store/migrations.py`, `enums.py` | Migration 2: `task_failures.failure_stage` gains `secrets` (deviation 12) |
| `src/cosmo/config/model.py`, `defaults.toml` | `GateConfig` gains `backend_image`/`backend_dir`/`frontend_image`/`frontend_dir`/`stage_timeout_seconds`/`diff_gate_*`/`flaky_*`/`quarantine_*`/`error_detail_max_chars` |
| `src/cosmo/cli/main.py` | `cosmo validate <worktree> --task-id ID [--task-branch] [--base-branch] [--allow-test-edits] [--run-id]` -- standalone diagnostic, same posture as `cosmo harness probe` |
| `tests/fixtures/gate_repo/` | The fixture: a minimal real Spring Boot backend (Maven, JUnit+AssertJ) and Vite+React frontend (Vitest, Playwright), committed lockfile |
| `tests/fixtures/fake_gate_docker.sh` | Env-var-driven `docker` stand-in for fast unit tests |
| `tests/test_gate_*.py` | Diff gate, quarantine, flaky, docker_runner mechanics, parsers, boundary -- all fast, all real (real git, real regex/JSON fixtures) |
| `tests/test_gate_fixture_e2e.py` | The 6 real-Docker regression tests, opt-in via `COSMO_GATE_DOCKER_E2E=1` |

### Decisions made during Phase 6

**1. `FailureStage.SECRETS` added, not in spec 9.3's enumerated list**
(deviation 12). The gate-side `gitleaks` backstop (spec 6.1) needed its own
attribution -- folding it into `TEST_INTEGRITY` would make that value
ambiguous for anyone querying `task_failures` later, since a leaked secret
is not a test-integrity violation. Required a real schema migration
(Migration 2): SQLite has no `ALTER TABLE ... DROP CONSTRAINT`, so the CHECK
constraint change is a create-copy-swap, written as a genuine `INSERT ...
SELECT` (not a blind drop+recreate) so it stays correct once real rows
exist, even though `task_failures` had none yet.

**2. `cosmo validate <worktree>` never touches `StoreWriter`/`EventEmitter`.**
Same posture Phase 3 gave `cosmo harness probe`: a bare worktree path need
not correspond to a queued task at all, so the CLI command is a standalone
diagnostic that calls `runner.run_validation_gate` directly. The real seam
for Phase 7/8's `VALIDATING` state handler is `gate.validate_task`
(`validate.py`) -- built and tested now (mirroring how Phase 5 built and
tested `merge_task` well before Phase 7 existed to call it), but not wired
to any CLI command.

**3. `GateRerun` (`Callable[[], bool]`, spec 3.4's merge-ladder seam) is
not satisfied directly by `run_validation_gate`.** Its natural signature
takes `worktree_path`/`base_branch`/`task_branch`/etc. and returns a full
`GateResult` -- far more than the ladder's merge-retry seam needs, and
reshaping either signature to fit the other would lose information one side
needs. `FakeGate.as_gate_rerun(task_id)` returns a closure of the right
shape for tests; whichever of Phase 7/8 becomes the real caller wraps
`run_validation_gate` the same way. Recorded per the Phase 5 handoff's own
instruction to treat this as a spec-deviation-shaped note rather than a
silent reshape.

**4. Diff gate never flags a newly *added* test file, only modified or
deleted ones -- found by hand against a real fixture run, not reasoned out
in advance.** Spec 6.1 layer 2's own wording is "modified or deleted";
an early version of this gate additionally flagged every *added* test file
too, which rejected every task that added a new e2e/unit test at all --
exactly backwards for an autonomous agent that is expected to write tests
for its own features. `DiffFile.is_added` (git's `A` status) is now
excluded from the `test_path_modified`/`test_path_deleted` violations, but
an added-but-immediately-disabled test is still caught by the
skip-annotation check, which applies regardless of file status. Locked in
by `test_diff_gate_does_not_flag_a_newly_added_test_file` and
`test_diff_gate_still_flags_a_disabled_newly_added_test`.

**5. Assertion counting (Open Item 1) is a regex line-count heuristic, not
a real per-language parser** -- `assertThat(`/`assert[A-Z]\w*(`/`expect(`
call sites on added vs. removed lines, fails safe (worst case a real
violation slips through, never a false failure on honest work). The spec
explicitly defers a real parser to a follow-up spec; a from-scratch
JUnit/AssertJ/Vitest/Playwright AST parser was out of scope for what this
phase could verify by hand in the time available.

**6. Maven's Surefire *text reports* (`target/surefire-reports/*.txt`) are
parsed, never Maven's own console output** -- found by hand against a real
failing run: the console's `[ERROR] Failures:` recap section names the
failing method but omits the assertion message entirely, while each
report file carries the full exception message and a real stack. Vitest and
Playwright are parsed from their own JSON reporters (`--reporter=json` /
the built-in `json` reporter), and **the Vitest report is written to a file
via `--outputFile`, never read from stdout** -- also found by hand: `npm
ci`'s own stdout ("added N packages...") precedes Vitest's JSON on the same
combined stream when both run via `sh -c "npm ci && ..."`, which broke
`json.loads` outright on a real container run before the fix.

**7. Vite 5's `preview.allowedHosts` guard blocks the e2e stage entirely
unless the target repo sets it** -- found by hand via a real Playwright run
that failed every assertion as a confusing "element not found." The gate
reaches `vite preview` by Docker network container hostname (Playwright and
the frontend are separate containers on a shared network, spec 1.1's own
`--ipc=host`/`--shm-size` container-isolation posture), and Vite 5 rejects
any Host header but localhost/an IP by default. This is not fixable from
Cosmo's side (it's the target repo's `vite.config.ts`), so
`templates/projects/java-spring-react/docs/frontend/architecture.md` now
carries a note; a repo that doesn't set `preview.allowedHosts: true` will
see every e2e test fail with a misleading error until this is understood.

**8. `npm ci`, not `npm install`, for every gate-side npm invocation --
requires the target repo's `package-lock.json` to be committed.** Chosen
for the same reproducibility reason CI conventionally uses `ci` over
`install`: a build that silently re-resolves a slightly different dependency
tree between build/unit/e2e's three separate containers (each a fresh
`npm ci`, no shared `node_modules`) is exactly the kind of nondeterminism
spec 1.1's atomic-version-pinning discipline is trying to eliminate
elsewhere. Practical implication: **the fixture's `frontend/package-lock
.json` is deliberately committed, not gitignored** -- found by hand when an
early real run failed at `npm ci` with `EUSAGE` against an uncommitted
lockfile. A real target repo must commit its lockfile the same way.

**9. The real-Docker fixture tests (`test_gate_fixture_e2e.py`) are
opt-in via `COSMO_GATE_DOCKER_E2E=1`, not run by default even when `docker`
is on PATH.** Unlike `test_git_secrets.py`'s real-`gitleaks` tests
(sub-second), a full gate run through real Maven/npm/Playwright containers
takes minutes even warm (~9 min for all 6 scenarios combined) and far
longer cold (first image pulls, Maven Central, npm registry -- observed
close to an hour on this box across several false starts, see decision 10).
Running this on every `./check.sh` would make the fast local loop
unusable, so it's opt-in, matching the "fake the mechanics, verify for real
by hand" split every prior phase drew between its default suite and a real
invocation.

**10. `npm install` hung repeatedly and non-deterministically on this box,
independent of network reachability -- root-caused, not just worked
around.** Confirmed by hand, ruled out in this order: (a) not a slow
network -- `curl` against the npm registry and Maven Central both returned
in under a second throughout; (b) not `npm audit`'s endpoint specifically --
`--no-audit` alone didn't fix it; (c) not IPv6 -- forcing
`NODE_OPTIONS=--dns-result-order=ipv4first` didn't fix it either; (d) the
real cause was a **leftover, inconsistent `node_modules`** from an earlier
killed install -- every hang happened while npm reconciled a partial tree
left by a previous interrupted run (`ps`/`/proc/<pid>/stat` CPU-jiffy
sampling showed genuine zero-progress stalls, not merely slow ones,
specifically in this state). A verified-clean `rm -rf node_modules
package-lock.json` before every `npm install` was reliable every time
(6 seconds once the package cache was warm). Worth knowing for any future
session that sees `npm install` hang on this host: check for a partial
`node_modules` from a prior kill before assuming it's a network problem.

**11. Docker containers write their build artifacts as root on the bind
mount** (Maven/npm run as root inside the official images by default),
which blocks a later unprivileged `rm -rf` of `backend/target`/
`frontend/node_modules` on the host -- found by hand while resetting the
verification scratch repo between scenarios. Not fixed in the gate runner
itself (no `--user $(id -u):$(id -g)` added) -- Cosmo's own worktree
`remove_worktree` (Phase 5) does not currently account for this, and a
future session should check whether task worktree cleanup can hit the same
permission wall after a real gate run. Worked around here with a throwaway
`alpine` container to `rm -rf` the mounted paths as root.

### Things that will matter later

**`remove_worktree` (Phase 5) has not been verified against a worktree
that has real gate-container-written, root-owned files in it.** Decision 11
above found this against a hand-built scratch repo, not through Cosmo's own
worktree lifecycle -- a real Phase 7/8 task run (worktree → gate → cleanup)
should be checked for this specifically. If it bites, the fix is either
running gate containers with `--user $(id -u):$(id -g)` (changes what UID
Maven/npm run as inside the container, untested) or having
`remove_worktree` shell out to a root container for cleanup the same way
this session's manual verification did.

**Per-stage container cache mounts (`~/.m2`, npm's cache) are not
implemented.** Every stage is a fresh `--rm` container with no persisted
dependency cache, so build → unit → e2e's three separate `mvn`/`npm`
invocations each redownload the same dependencies from scratch. This kept
the gate runner simple and made the real verification runs slower than
they need to be (minutes instead of potentially seconds) but is correct,
not broken. A future phase (likely 9, "observability, logs, disk,
deployment") is the natural place to add a persistent build-cache volume
mounted read-write into every gate container, once real gate-duration data
(build item 9 below) shows it's worth the added state to manage.

**Gate duration is recorded via `task.validation_result`'s
`duration_seconds` payload field, not a dedicated table.** Spec 3.3's own
note wants duration "recorded and queryable" so the 45-minute `VALIDATING`
timeout can be retuned empirically (Open Item 2) -- `events` (spec 9)
already supports this via `list_events(event_type=...)` plus JSON payload
inspection, so a new table would have duplicated existing schema rather
than filled a real gap. No query convenience beyond `list_events` was
added; Phase 9 (observability) is the natural place for a real "p95 gate
duration" report once there's enough real run history to make one useful.

**Flaky-test reruns are scoped to e2e only, matching spec 6.4's own
framing** ("When a non-quarantined e2e test fails..."). Unit-test flakiness
is not addressed by the spec and this phase makes no attempt to handle it;
a flaky unit test still fails the gate as an ordinary `code_error`.

**`gate.backend_image`/`frontend_image`/`backend_dir`/`frontend_dir` assume
the spec's fixed target stack (Java+Spring backend, Vite+React frontend,
conventional `backend/`/`frontend/` monorepo layout) -- the spec names this
stack but never specifies concrete build images, commands, or directory
conventions.** This phase had to make a concrete choice to have anything to
run; documented as config (overridable per host/repo) rather than hardcoded,
but a repo that doesn't follow this exact `backend/`+`frontend/` layout, or
uses a different build tool, is out of scope until a future spec revision
generalizes it (most likely via a per-repo manifest, not attempted here).

## Phase 7 — Complete

All exit criteria met. `cosmo run --task <id>` drives one task through the
full spec 3.2 state machine (`QUEUED -> PROPOSING -> PROPOSED ->
IMPLEMENTING -> VALIDATING -> COMMITTING -> MERGING -> DONE`, with
`FAILED_RETRY`/`BLOCKED`), with a real per-state persisted transition +
`task.state_changed` event trail, against `FakeHarnessAdapter` for the fast
suite and against the **real** Docker gate (`COSMO_GATE_DOCKER_E2E=1`,
opt-in, actually run this session -- 1 task, real worktree, real merge, 2m40s)
for the integration exit criterion. `./check.sh`: 264 tests, 7 skipped (the
6 real-Docker gate tests from Phase 6 plus this phase's own opt-in
real-adapter+real-gate test), ~17s.

### What exists

| Path | Contents |
|---|---|
| `src/cosmo/task/machine.py` | `run_task` -- the actual driver. One `_do_*` helper per state; see its own module docstring for the full attempt-counting model (below) |
| `src/cosmo/task/timeouts.py` | `run_with_wall_clock_timeout`/`run_with_liveness_timeout` -- generalizes the background-thread+join+cancel pattern `cli/main.py`'s Phase-3 `harness probe` command hand-rolled once; `on_tick` lets a caller drain `StoreWriter` and poll progress on the right cadence while a harness call blocks |
| `src/cosmo/task/progress.py` | `ProgressWatcher` -- `watchdog` observer (file mode) + `on_tick`-driven polling (both modes), `parse_tasks_md`/`read_progress_from_file`; every write, including event emission, goes through `writer.submit()` -- see decision 4 below, a real bug this phase found and fixed |
| `src/cosmo/task/classify.py` | `classify_harness_failure` -- `PROPOSING`/`IMPLEMENTING` failure classification (spec 6.2) |
| `src/cosmo/task/retry.py` | `build_retry_context` -- spec 6.3 informed retries, reads back `task_failures` via the new `list_task_failures` reader |
| `src/cosmo/task/types.py` | `TaskContext`, `FailureClassification` |
| `src/cosmo/knowledge/caps.py` | `docs_md_files` (git diff, `docs/**/*.md` only), `files_over_cap` -- spec 11's line-cap enforcement |
| `src/cosmo/knowledge/decisions_log.py` | `append_decision_entry` -- one Cosmo-authored, structured `decisions-log.md` line per task that reaches `COMMITTING` |
| `src/cosmo/store/writer.py` | `TransitionResult` (every `task_queue.status` writer now returns one); `queue_transition` (generic setter for the six states with no dedicated method); `queue_begin_attempt` (the one place `attempt_count` increments) |
| `src/cosmo/store/reader.py` | `list_task_failures`/`TaskFailureRow` |
| `src/cosmo/events/helpers.py` | `emit_state_changed(emitter, TransitionResult)` -- the one canonical `task.state_changed` payload builder, now used everywhere a transition happens (`cli/main.py`'s `queue add`/`queue retry`/`queue block`, `git/merge.py`'s two `merge_task` outcomes, and every transition `task/machine.py` drives) |
| `src/cosmo/gate/validate.py` | `validate_task` gains a `gate_runner: GateRunner = run_validation_gate` parameter -- the real seam that lets `task/machine.py` call the exact same tested side-effect logic (the `task.validation_result` event, `record_task_failure` on failure) against a scripted `FakeGate` result in tests, without duplicating that logic in a second place |
| `src/cosmo/config/model.py`, `defaults.toml` | New `ProgressConfig`/`[progress]` section: `poll_interval_seconds` (spec 4's "5-10s" polling fallback, also used for native-progress polling -- no config field existed for this at all before Phase 7) |
| `src/cosmo/cli/main.py` | `cosmo run --task <id> --repo <path> [--base-branch] [--harness]` -- creates the worktree, then calls `task.machine.run_task` |
| `tests/test_task_*.py`, `test_knowledge.py`, `test_cli_run.py` | Fast suite: state machine (4 exit-criterion scenarios), progress watcher (checkbox parsing, debounce, real `watchdog` file-mode test, a real thread-safety bug regression), classify, retry, knowledge caps/decisions-log, CLI glue |
| `tests/test_task_fixture_e2e.py` | The one real-adapter(fake)+real-gate integration test, opt-in via `COSMO_GATE_DOCKER_E2E=1` (same var as Phase 6's, not a new one) -- run for real this session |

### Decisions made during Phase 7

**1. `attempt_count` is 0-indexed and peeked-before-incremented, not
incremented eagerly.** The first design written incremented
`task_queue.attempt_count` as soon as `IMPLEMENTING` succeeded and control
reached `VALIDATING` -- wrong, caught before any test was written by
working through spec 6.3's "Third code-level failure -> BLOCKED" against the
default `max_attempts=2`: that phrasing only holds if `attempt_count`
represents *attempts already consumed* (0 for the first attempt), passed to
`validate_task`/re-derived in `run_task` **before** the new attempt's
outcome is known, and only persisted (`queue_begin_attempt`) afterward, and
only for a genuine code-level judgment -- a pass, a `code_error`/
`test_integrity` verdict at `VALIDATING`, or a timeout at `IMPLEMENTING`.
An `environment_error`, wherever it originates, never persists the
increment. `test_retry_exhaustion_blocks_with_code_failure` pins the
resulting arithmetic: three consecutive `code_error` verdicts are needed to
reach `BLOCKED` with `max_attempts=2`, and `attempt_count` reads `3` at that
point (not `2`) -- a correct, if initially counter-intuitive, consequence of
0-indexing.

**2. `COMMITTING` never calls the harness.**
`templates/harness/claude/CLAUDE.md` (built in Phase 4, already committed)
already instructs the agent to append knowledge notes and commit its own
work as the *last step of `IMPLEMENTING`* -- found by rereading that file
before writing any Phase 7 code, not assumed from the spec text alone,
which reads ambiguously enough to suggest a separate harness call at
`COMMITTING` time. `COMMITTING` is therefore fully deterministic: it
inspects whatever `docs/**/*.md` files the task's own commits touched
(`git diff base...branch`, computed fresh, not threaded through from
`HarnessResult.files_changed`), enforces `knowledge.max_file_lines`
(failing `code_error`/`failure_stage=commit`, informed-retry-eligible, on a
violation -- exactly the enforcement `CLAUDE.md` explicitly leaves to
Cosmo: "say so in your summary instead of trimming yourself"), and appends
one Cosmo-authored, structured `decisions-log.md` line unconditionally
(trading spec 11's conditional "if a decision was introduced" for a cheap,
consistent, always-parseable entry -- deciding "was this a decision" would
need exactly the unverified LLM self-report spec 11 is designed to avoid).

**3. `gate.validate_task` gains an injectable `gate_runner` parameter
rather than `task/machine.py` reimplementing its retry/event logic against
`FakeGate` separately.** The alternative -- give `task/machine.py` its own
`ValidateFn` abstraction bound to either `validate_task` (real) or a
hand-rolled `FakeGate`-based equivalent (tests) -- would have duplicated
`validate_task`'s already-tested side-effect logic (the
`task.validation_result` event, `record_task_failure` on failure) in a
second, divergence-prone place. Instead `validate_task(..., gate_runner:
Callable[..., GateResult] = run_validation_gate)` lets a test inject `lambda
**kw: fake_gate.validate(kw["task_id"])` underneath the *same*, real
`validate_task` call `task/machine.py` always makes -- spec 6.2/6.3's
retry logic is exercised for real in every test, never mocked out. `MERGING`
keeps the *other* seam Phase 6 already built for this
(`gate_runner`-shaped closure wrapping `run_validation_gate` directly, no
second `task_failures` row) -- two distinct integration points, not one,
matching Phase 6 decision 3's own guidance.

**4. A real cross-thread SQLite bug, caught by `test_watchdog_observer_
detects_a_real_write_to_tasks_md`, not by inspection.** An early
`ProgressWatcher.check()` wrote `task_progress`/`task_heartbeat` through
`writer.submit()` (correct, cross-thread-safe) but called
`emitter.emit(...)` **directly** for the paired `task.progress`/
`task.heartbeat` events -- `EventEmitter.emit` uses `self._writer
.connection` with no locking of its own (spec 8 assumes one thread calls
it). Calling it from the `watchdog` observer's own background thread raised
`sqlite3.ProgrammingError: SQLite objects created in a thread can only be
used in that same thread` the first time the real-file-watching test
actually exercised that code path -- the fake-clock/synchronous tests
earlier in the same file didn't catch it because they only ever call
`check()` from the main thread. Fixed by folding the event emission *inside*
the submitted job closure, so it always runs on the connection-owning
thread via `drain()`. A second, unrelated bug found by the same test file:
the submitted job functions executed `conn.execute(...)` with no `with
conn:` wrapper, so the write was never committed -- a second connection
(`connect_reader`, used by `get_progress`) never saw it. Both are now fixed
and pinned by tests; recorded because both are the kind of bug that a
type checker cannot catch and only exercising the real background-thread
path surfaced.

**5. A real `fnmatch` bug in the knowledge-cap filter, also caught by a
test, not inspection.** `docs_md_files` originally matched touched paths
against `"docs/**/*.md"` with `fnmatch.fnmatch`. `fnmatch`'s `**` is not a
recursive-directory wildcard -- it is just two ordinary `*`s, so the pattern
still requires a literal `/` between them and the trailing `*.md`, meaning
it silently **rejected** `docs/architecture.md` itself (a file directly
under `docs/`, no subdirectory) while matching `docs/backend/x.md` just
fine. `test_docs_md_files_finds_only_docs_markdown_touched_on_the_branch`
caught this on first run. Fixed by replacing the glob with a plain
`path.startswith("docs/") and path.endswith(".md")` check -- simpler and
has no such edge case.

**6. `environment_error`'s bounded local retry (both `IMPLEMENTING`'s own
process failures and `VALIDATING`'s environment-error verdicts) reuses
`config.retries.max_attempts` as its bound rather than a new config field.**
Spec 6.2 says `environment_error` "does not count toward the task's retry
limit," full stop -- taken completely literally, a task stuck against a
broken environment would retry forever within one `run_task()` call, since
nothing before Phase 8's circuit breaker exists to stop it. Reusing the
existing retry-count config (rather than inventing
`environment_retry_limit` or similar) is a deliberately conservative,
minimal interim choice, explicitly not the real fix -- the real fix is
Phase 8's circuit breaker noticing repeated `environment_error`s *across
distinct tasks* and pausing the whole run, which this bound cannot see or
substitute for. `gate.validate_task`'s own docstring already flagged this
exact gap ("Until Phase 8 exists, an environment_error here always reports
next_action=RETRY"); this decision is what keeps that documented gap from
becoming an actual infinite loop in the meantime.

**7. `VALIDATING`'s own external wall/stall timeout
(`timeouts.validating_wall`/`validating_stall`) is not wired to a timer in
this phase -- a deliberate, documented scope reduction, not an oversight.**
`gate.stage_timeout_seconds` (Phase 6, already tuned and verified against
real Docker) bounds each of the gate's three stages individually and
already converts a stage timeout into `FailureType.ENVIRONMENT_ERROR`
before `GateResult` reaches `task/machine.py` at all
(`StageResult.timed_out`'s docstring) -- combined with decision 6 above,
spec 3.3's "VALIDATING timeouts do not consume the code-level retry budget"
already holds structurally, without a second timer. Building a real outer
wall-clock wrapper was considered and rejected: `run_validation_gate` has
no `cancel()` hook (unlike `HarnessAdapter`), so an outer timeout could only
abandon a background thread without stopping the Docker containers it
started -- worse than no timeout, since it would claim a bound it can't
actually enforce. `PROPOSING`/`IMPLEMENTING` don't have this problem
because `HarnessAdapter.cancel()` exists and genuinely terminates the
process group (spec 2.4). Recorded as a deferred item, not silently
dropped.

**8. `HeartbeatSource.STREAM` is never produced.** Nothing in the current
`HarnessAdapter` ABC exposes a live per-event callback during a blocking
`implement()`/`propose()` call -- even the real Claude adapter, which
declares `supports_structured_stream=True` and does parse `stream-json`
internally, only returns a single `HarnessResult` at the end of one
blocking call; there is no channel back to the caller mid-flight. Progress/
liveness is observed the only way actually available from outside that one
call: `ProgressWatcher`'s `on_tick`-driven polling (both for a native-
progress adapter's `get_progress()` and for `tasks.md` file reads) plus a
`watchdog` observer for immediate file-change detection. Both existing
sources are reused for this: `FILE` for a real `watchdog` event, `MTIME` for
every poll-driven check regardless of *what* is being polled (a file's
mtime, or an adapter's native progress) -- the schema has no fourth value,
and "detected via a poll, not a push" is the honest, shared description of
both cases. Realizing `source=stream` for real needs an ABC change (a
progress/event callback parameter on `implement()`), out of scope here;
recorded for whichever future phase revisits the harness interface.

**9. `run_task()` never creates or removes the worktree itself.** `cosmo
run` (the CLI command) calls `git.worktree.create_worktree` before invoking
`run_task`, and `MERGING`'s success path already removes the worktree
*inside* `git.merge.merge_task` (Phase 5, unchanged). This keeps
`task/machine.py`'s own unit tests free of any real git worktree setup
except where `merge_task` itself is exercised (`test_task_machine.py`
builds a real repo + real worktree via `create_worktree`, matching
`test_git_merge.py`'s own pattern, since `MERGING` is a real code path this
phase actually drives for the first time).

**10. `task_transitions.run_id`/`task_failures.run_id` stay `None`
everywhere Phase 7 writes them, including inside `run_task`.** Both columns
carry a real, `PRAGMA foreign_keys = ON`-enforced FK to `run_state`, and
nothing writes a `run_state` row until Phase 8's run-level state machine
exists -- a non-`None` `run_id` here would raise `sqlite3.IntegrityError`
outright. `cosmo run` does generate a `run_id` (a fresh uuid), but only to
namespace the worktree's path (`work_dir/<run_id>/<task_id>`, `git.worktree
.create_worktree`'s own existing parameter), never to any FK'd column.

**11. Real, confirmed evidence of Phase 6's "Things that will matter
later" item on root-owned worktree files.** Running the opt-in real-gate
integration test for real left `backend/target/` (Maven, root-owned inside
the Docker container) undeletable by `remove_worktree`'s unprivileged
fallback (`shutil.rmtree(..., ignore_errors=True)`) -- confirmed by hand,
not merely predicted: `merge_task` still reported success (the `git
worktree remove --force`/prune path tolerates the leftover directory), but
pytest's own `tmp_path` teardown later warned with a real `PermissionError`
trying to clean the same directory. Not fixed here (same as Phase 6 left
it) -- worked around by hand with a throwaway root `alpine` container,
identical to Phase 6's own workaround. Still the natural candidate for a
future phase to fix for real (`--user $(id -u):$(id -g)` on gate
containers, or teaching `remove_worktree` the same root-container trick).

### Things that will matter later

**No mid-state resumption, as spec 3.2 already accepts.** A crash during
`IMPLEMENTING`/`VALIDATING` restarts that state from scratch on the next
`cosmo run` invocation for the same task -- `session_id` is still not
threaded anywhere (deviation 3's `HarnessResult.session_id` field exists
but nothing persists or reads it back yet). Unchanged from the plan's own
framing; still deliberately deferred (§12).

**`cosmo run` takes `--repo` explicitly; there is still no `task_id ->
project/repo` linkage anywhere in the schema.** `task_queue` has no
`project_id` column, so a multi-project run loop (Phase 8) will need to add
one, or otherwise resolve which registered project a given task belongs to
before it can drive tasks across more than one repo in the same run. Not a
regression -- `cosmo project register` (Phase 4) and `task_queue` (Phase 1)
were never linked before Phase 7 either -- but Phase 7 is the first code
that would have benefited from it, so it's worth Phase 8 addressing
directly rather than working around again.

**Per-stage container cache mounts are still not implemented** (Phase 6's
own note, reconfirmed): the opt-in integration test's 2m40s runtime is
almost entirely fresh `mvn`/`npm` dependency resolution inside three
separate `--rm` containers, same as Phase 6 observed. Still the natural
Phase 9 item.

**`RetryConfig.delay_min`/`delay_max` are real `time.sleep()` calls in
production** (spec 6.3: "not rate-limiting, but letting transient resource
contention settle") -- every Phase 7 test therefore overrides them to `0`
via `model_copy`. A future run-loop-level test (Phase 8) driving several
tasks through real retries back to back should keep doing the same, or the
suite will slow down proportionally to how many retries a scenario needs.

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
| 11 | `GitConfig.commit_author_name` / `commit_author_email` added | §3.4 | 5 | Spec never names a git identity for Cosmo's own merge/rebase commits; this box (and a fresh dev box generally) has no global git identity configured, found by hand -- passed as `-c user.name=...` per invocation, never written globally |
| 12 | `FailureStage.SECRETS` added, not in the spec's enumerated list | §9.3 | 6 | The gate-side `gitleaks` backstop (§6.1) needs distinct attribution from `test_integrity`; required a real schema migration (task_failures.failure_stage CHECK constraint) |
| 13 | Diff gate never flags a newly *added* test file, only modified/deleted | §6.1 | 6 | §6.1's own wording is "modified or deleted"; flagging additions too rejected every task that wrote a new test at all -- found by hand against a real run |
| 14 | `GateConfig` gains `backend_image`/`backend_dir`/`frontend_image`/`frontend_dir`/`stage_timeout_seconds`/`diff_gate_*`/`flaky_*`/`quarantine_*`/`error_detail_max_chars` | §1, §6.1, §6.4 | 6 | The spec names the target stack and the guardrail behaviors conceptually but never their concrete build images/commands/thresholds/file locations; this phase needed real values to run anything against |
| 15 | `run_validation_gate`'s signature does not conform to `git.merge.GateRerun` (`Callable[[], bool]`) | §3.4 | 6 | Its natural signature needs `worktree_path`/`base_branch`/`task_branch`/etc. and returns a full `GateResult`; `FakeGate.as_gate_rerun()` is the adapter, per the Phase 5 handoff's own instruction to record this rather than reshape either signature |
| 16 | `COMMITTING` never invokes the harness | §11 | 7 | `templates/harness/claude/CLAUDE.md` (Phase 4) already has the agent append knowledge notes and commit as the last step of `IMPLEMENTING`; `COMMITTING` only enforces the line cap and appends a Cosmo-authored `decisions-log.md` entry, deterministically |
| 17 | `HeartbeatSource.STREAM` is never produced; `MTIME` is reused for both file-mtime polling and native-progress polling | §4, §9.2 | 7 | No adapter ABC method exposes a live per-event callback during a blocking `implement()`/`propose()` call, even for an adapter that declares `supports_structured_stream=True`; realizing it needs an ABC change, out of scope here |
| 18 | `VALIDATING`'s `timeouts.validating_wall`/`validating_stall` are not wired to an external timer | §3.3 | 7 | `gate.stage_timeout_seconds` (Phase 6) already bounds each gate stage and converts a stage timeout to `environment_error` before `task/machine.py` ever sees it; `run_validation_gate` has no `cancel()` hook, so a second outer timer could only abandon a thread without stopping the Docker containers it started |
| 19 | `environment_error`'s retry bound (both `IMPLEMENTING` and `VALIDATING`) reuses `retries.max_attempts` rather than a dedicated field | §6.2, §6.5 | 7 | Spec 6.2 says it "does not count toward the retry limit" with no bound at all; without Phase 8's circuit breaker to eventually stop a stuck environment across tasks, an explicit local bound is the conservative interim choice, not the real fix |
| 20 | `ProgressConfig`/`[progress].poll_interval_seconds` added | §4 | 7 | Spec names "polling fallback at 5-10s" but no config field existed for it; also reused as the native-progress poll interval since there is no separate stream-driven path (deviation 17) |
