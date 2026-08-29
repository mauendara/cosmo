# Cosmo — Implementation State

Running record of what actually exists in the codebase, phase by phase. Updated at
the end of each working session.

The plan ([v3-implementation-plan.md](v3-implementation-plan.md)) says what *will*
be built. This document says what *is* built, and records decisions and gotchas
made during implementation that a future session would otherwise have to
rediscover.

| | |
|---|---|
| Last updated | 2026-08-27 |
| Working branch | `develop` |
| Head commit | `f721e7b` plus this session's v5 improvements plan work (see "v5 improvements plan — Implemented" below), uncommitted as of this entry |
| Spec | [v3-cosmo-autonomous-agent-spec.md](v3-cosmo-autonomous-agent-spec.md) |
| Still needs a real invocation to validate | [v8-validations-for-later.md](v8-validations-for-later.md) |
| Out of scope / deferred / open decisions | [v9-out-of-scope-desirables.md](v9-out-of-scope-desirables.md) |

**Everything in the plan except Phase 10 is implemented.** Phases 0-9 are
complete, and the two real, already-diagnosed bugs plus the one
observability gap found while reviewing Phase 9 against Phase 10's own
scope (see "Fast-follow, same session" under Phase 9 below) are fixed, not
carried forward as open items. What remains is Phase 10 itself: a real
target repo, a real overnight run, and the two genuinely data-driven items
that can only be resolved by that run (Open Item 2's timeout retuning, and
confirming or correcting the quota heuristic's guessed config values). One
session of genuine prep happened ahead of it (see "Phase 10 prep" below) --
a new project template plus two small real bugs it surfaced, in the same
"check by hand before trusting a green" spirit every prior phase followed --
but this is not new phase scope, and Phase 10 itself still needs nothing
more written ahead of the actual overnight run.

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
| 8 — Run loop, DAG, circuit breaker, quota | **Complete** |
| 9 — Observability, logs, deployment | **Complete** (+ 2 fast-follow fixes, same session) |
| 10 — Acceptance run | **Not started** — prep done ahead of it this session: `vite-react-local` template, gate e2e backend-optional fix, guardrail `.tsx`/`.jsx` widening (see "Phase 10 prep" below) |

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

## Phase 8 — Complete

All exit criteria met. `cosmo run` (no `--task`) drives the whole queue as
a dependency-ordered DAG via `run.loop.run_queue`, calling Phase 7's
`task.machine.run_task` once per eligible task, strictly serial (spec 5),
until the queue empties, the circuit breaker trips, a quota/cost ceiling
intervenes, or the run-level wall clock expires. `./check.sh`: 316 tests,
7 skipped (unchanged from Phase 7 -- the real-Docker opt-ins), ~20s. Real
invocations run this session, not just unit tests (state doc convention):
`cosmo queue add --depends-on`/`cosmo run --dry-run` against a live DB
(caught nothing, confirmed correct); a standalone script driving
`run_queue` directly against `FakeHarnessAdapter`+`FakeGate` outside pytest
(caught decision 6's real bug -- a genuine, unstubbed 5-hour `time.sleep`).

### What exists

| Path | Contents |
|---|---|
| `src/cosmo/run/loop.py` | `run_queue` -- the run-level orchestrator. One `_run_one_task` call per DAG-eligible task; `_handle_quota_pause_or_stop`, `_environment_error_weight`, `_fill_summary_extras`/`_knowledge_files_near_cap` are its own private helpers |
| `src/cosmo/run/dag.py` | `resolve_execution_order` (Kahn's algorithm, hard `depends_on` + soft `priority` tie-break), `find_cycle` (plain `{task_id: depends_on}` graph, not `TaskRow`-shaped -- reused by `cli/main.py`'s `queue add`), `DagCycleError` |
| `src/cosmo/run/breaker.py` | `CircuitBreaker` -- in-memory, per-`cosmo run`-process; `record_done`/`record_blocked` |
| `src/cosmo/run/quota.py` | `observe_harness_result` (primary+secondary), `HeuristicTracker` (tertiary), `decide` (the pause-vs-resume-vs-stop branch), `QuotaSignal`/`QuotaDecision` |
| `src/cosmo/run/cost.py` | `check_run_cost`, `task_cost_ceiling_reached`, `CostVerdict` |
| `src/cosmo/run/types.py` | `RunSummary`, `RunOutcome` |
| `src/cosmo/task/machine.py` | `run_task` gains `run_id`/`on_harness_result`/`check_run_guard` -- all optional, all additive; see decision 3 |
| `src/cosmo/task/types.py` | `RunGuardAction` (`BLOCK_COST`/`REQUEUE`) -- lives here, not `cosmo.run`, so the dependency direction (`run` depends on `task`, never reversed) holds |
| `src/cosmo/harness/base.py` | `HarnessResult` gains `quota_window`/`quota_resets_at`/`tool_call_count`, all defaulted |
| `src/cosmo/harness/claude/stream.py` | `extract_quota_signal` -- normalizes both observed rate-limit wire shapes into `(window, resets_at)` |
| `src/cosmo/harness/fake/adapter.py` | `FakeOutcome.RATE_LIMIT`/`COST_OVERRUN` (Phase 3 scaffolding, unused until now) wired for real; `ScriptedCall` gains `quota_window`/`quota_resets_at`/`tool_call_count` |
| `src/cosmo/store/writer.py` | `run_create`/`run_transition`/`run_cost_add`/`task_cost_add` -- `run_state`/`run_cost`/`task_cost`'s first real writer (Phase 1 shipped the tables unused); `queue_transition`/`queue_block`/`queue_complete`/`queue_retry` gain an optional `run_id` |
| `src/cosmo/store/reader.py` | `RunRow`/`get_run`/`get_run_cost`/`get_task_cost`; `list_task_failures` gains an optional `run_id` filter |
| `src/cosmo/events/envelope.py` | `EventType.RUN_COST_WARNING` (deviation 21) |
| `src/cosmo/config/model.py`, `defaults.toml` | New `QuotaConfig`/`[quota]` section |
| `src/cosmo/cli/main.py` | `cosmo run` gains `--dry-run`; `--task` becomes optional (omitted -> the DAG path); `cosmo queue add` gains cycle rejection at enqueue |
| `tests/test_run_dag.py`, `test_run_breaker.py`, `test_run_quota.py`, `test_run_cost.py` | Pure-logic unit tests for each module, isolated from the store/CLI |
| `tests/test_run_loop.py` | Integration tests: `run_queue` against `FakeHarnessAdapter`+`FakeGate` over a real git repo -- the plan's own multi-task-DAG, breaker-trip, 5h-auto-resume, weekly-beyond-budget, per-task-cost-ceiling, and run-wall-clock exit-criterion scenarios, plus the cross-run-retry regression (decision 7) |
| `tests/test_cli_run_queue.py` | CLI glue: `--dry-run` rendering, cycle rejection, routing to `run_queue` (monkeypatched, same posture `test_cli_run.py` already took for `run_task`) |

### Decisions made during Phase 8

**1. `cosmo run --task <id>` and the no-`--task` DAG path stay two separate
CLI code paths, not one path routing single-task through the DAG loop
too.** The handoff explicitly asked this be decided and documented (the
same "ambiguous CLI surface" framing every previous phase used). Routing
single-task through `run_queue` would have been more uniform, but
`run_queue` owns run-level concerns (a `run_state` row, the breaker, quota,
cost, the 10h wall clock) that Phase 7's single-task command was never
specified to have and whose tests never exercised; risking that already-
green surface for uniformity's sake wasn't worth it. `cosmo run --task`
is therefore untouched byte-for-byte in its own control flow (still passes
`run_id=None` to `run_task`, exactly Phase 7's posture) -- the same
"diagnostic entry point, no store surprises" stance `cosmo validate`/
`cosmo harness probe` already take relative to the real state machine.

**2. `run_id` now threads for real through every `task_queue`/
`task_transitions`/`task_failures` write `run.loop` makes, via new optional
`run_id` parameters on `StoreWriter.queue_transition`/`queue_block`/
`queue_complete`/`queue_retry`** (defaulting to `None`, so every existing
caller -- the CLI's standalone `queue retry`/`queue block` commands, Phase
7's own single-task path -- is unaffected). `task.machine.run_task` no
longer hardcodes a local `run_id: str | None = None`; it is now a real
parameter, `None` by default. This is exactly the change Phase 7 decision
10 predicted would be needed once "Phase 8's run-level state machine
exists" -- confirmed for real when two Phase 5 tests (`test_git_merge.py`'s
`merge_task(run_id="run-1", ...)` calls) started raising
`sqlite3.IntegrityError: FOREIGN KEY constraint failed` the moment
`queue_complete`/`queue_block` began writing that value through for real;
fixed by giving those two tests a real `writer.run_create(run_id="run-1",
...)` row to reference, not by loosening the FK.

**3. `task.machine.run_task` gained two purely additive optional hooks --
`on_harness_result`/`check_run_guard` -- rather than any restructuring of
its retry/classification logic**, per the handoff's own instruction ("call
it, don't reimplement any part of it"). `on_harness_result` observes every
raw `HarnessResult` from `propose()`/`implement()` as it happens (cost
accounting, quota-signal capture); `check_run_guard` is polled at exactly
two points -- the top of `_do_proposing`'s attempt loop and the top of
`run_task`'s main `while True:` loop, i.e. immediately before every new
`PROPOSING`/`IMPLEMENTING` attempt -- and can ask the task to stop
(`RunGuardAction.BLOCK_COST` -> `_block(reason=COST)`) or hand control back
to the run loop (`RunGuardAction.REQUEUE` -> `_requeue`, a new terminal
outcome distinct from `_block`: `attempt_count` untouched, status ->
`QUEUED`). Both default to `None`; every existing call site (including
`cosmo run --task`) is unaffected. `RunGuardAction` itself lives in
`cosmo.task.types`, not `cosmo.run`, so `cosmo.task` never has to import
the package that calls it -- the dependency direction stays one-way, the
same discipline `cosmo.gate`/`cosmo.git` already established relative to
`cosmo.task`.

**4. Spec 7.1/7.2's primary quota signal now has a real field to detect,
found by rereading a fixture Phase 3 already captured but never fully
used.** `tests/fixtures/stream_json/api_retry.ndjson`'s `rate_limit_event`
line carries `rate_limit_info: {status, resetsAt, rateLimitType}` --
`rateLimitType: "five_hour"` is the first real evidence connecting spec
7.1's two named windows (five-hour rolling, weekly) to an actual wire
field; `resetsAt` is a real epoch-seconds reset ETA. `harness.claude.
stream.extract_quota_signal` normalizes this (and the `system/api_retry`
shape's `retry_after_ms`, which carries no window/ETA at all -- a short
internal backoff, not a real reset time) into `(window, resets_at_iso)`,
surfaced on `HarnessResult` as new `quota_window`/`quota_resets_at` fields
(defaulted, so every existing keyword construction of `HarnessResult`
stayed valid). `rate_limit_info.status` values other than a real denial
were never observed for real (the fixture's own call still *succeeds*,
subtype `"success"` -- the CLI's internal retry absorbed the limit), so
`cosmo.run.quota.observe_harness_result` deliberately only treats a signal
as actionable when the call it rode in on failed, never merely because a
rate-limit-shaped event was present.

**5. Spec 7.2's secondary signal (the terminal result's error subtype) has
no real captured value behind it.** No real `claude -p` invocation in this
project's history has ever actually exhausted a quota window, so there is
no known string to match `output_summary` against. `QuotaConfig.
result_error_subtypes` (default: `["error_rate_limit"]`) is a configured,
clearly-flagged best guess -- correctable the day a real capture exists,
the same posture the spec's own timeout defaults take pending real p95
data (Open Item 2). Recorded so a future session doesn't mistake the
current value for verified.

**6. A real, unstubbed 5-hour `time.sleep` -- found by running `run_queue`
directly outside pytest, not by a unit test's green.** The first version
of the circuit-breaker test (3 distinct tasks failing `environment_error`)
hung for real. Root cause: the tertiary wall-clock heuristic
(`HeuristicTracker`, spec 7.2's last resort -- "repeated immediate
failures... with no tool calls executed") and an ordinary
`environment_error` block look *identical* from the outside (both are
fast, zero-tool-call, failed calls), and the first version of `run_queue`
fed every `BLOCKED`/`QUEUED` outcome to the heuristic *before* checking
whether the breaker itself had already explained the same evidence. On the
3rd blocked task, the heuristic fired first, and the run paused for a
"confirmed-by-default" `five_hour` quota window with no injected `sleep`
stub in that test -- a real 18000-second `time.sleep` (`QuotaConfig.
default_5h_resume_delay_seconds`). Every mocked/fake-clock test in the
suite passed regardless, since none of them happened to script exactly 3
back-to-back environment failures with no explicit `sleep=` override; only
a real, unmocked invocation surfaced it, echoing Phase 7's own "check with
a real invocation" findings. Fixed by reordering: `_run_one_task` now
returns only a *confirmed* (primary/secondary) quota signal in `_TaskRun
Result.quota_signal`; the tertiary heuristic is consulted by `run_queue`
itself, and only for a `BLOCKED` outcome that the breaker did *not* already
trip on -- never for `DONE`/`QUEUED` at all. A confirmed signal still wins
outright, checked before the breaker or the heuristic get any say.

**7. Worktree/branch reuse across a within-run requeue is scoped to the
*current* run, not "does a `worktree_path` exist at all" -- found by
writing a genuine cross-run regression test, not by inspection.** A task
returned to `QUEUED` by `check_run_guard` (wall clock or quota) keeps its
`worktree_path` (`queue_transition` deliberately doesn't clear it, unlike
`queue_complete`/`queue_block`'s own terminal-state handling) so the same
attempt can resume without a redundant `git worktree add`. But a task
`BLOCKED` in one `cosmo run` invocation and later retried (`cosmo queue
retry`) by a *new* one still carries the *old* run's `worktree_path` --
different `run_id`, different path. The first version of this logic
reused any non-`None` `worktree_path` unconditionally; a dedicated
cross-run test (block in run 1, retry, drive run 2) caught it two ways in
sequence: first a `git worktree add` failure at the stale path (fixed by
comparing the recorded path against `work_dir/<CURRENT run_id>/<task_id>`
and falling through to `create_worktree` on a mismatch), then a *second*
failure -- `fatal: a branch named 'task/<spec_id>' already exists` --
since `git worktree add`'s branch name is task-scoped
(`task/<spec_id>`), not run-scoped, and spec 3.2 deliberately retains a
`BLOCKED` task's branch (not just its worktree) for inspection. Fixed by
calling `git.worktree.remove_worktree` (worktree + branch) on the stale
path before creating the fresh one -- a retry is a deliberate "start over,"
not a resurrection of the abandoned attempt.

**8. The circuit breaker is evaluated once per task's *terminal* `DONE`/
`BLOCKED` outcome, in-memory, scoped to one `cosmo run` process -- not
continuously mid-task, and not persisted/reconstructed across a restart.**
Spec 6.5 is phrased in terms of task outcomes ("N distinct tasks... land in
`BLOCKED`"), so per-task granularity is spec-faithful, not a shortcut.
`merge_conflict`/`flaky_unresolved` blocks are excluded from the tally
*entirely* (neither add to nor reset the consecutive-blocked streak) --
spec 3.4's own framing: they signal queue contention over shared files,
not a broken environment. A `PAUSED`-for-breaker run's in-memory tally is
lost on restart, but that costs nothing real: spec 6.5's own "resuming
requires manual intervention" already means a human reviews the situation
before anything continues, and the persisted `run_state.status='paused'`
row (not the tally) is what that review actually needs to see.

**9. The breaker's per-task `environment_error` weight is computed from
`task_failures`/`events`, scoped to the *current* `run_id`** (`list_task_
failures` gains an optional `run_id` filter for exactly this) **--** a
distinct task counts once (weight 1) if it had any `environment_error`
failure during this run, or `config.circuit_breaker.reap_failure_weight`
(default 2) instead if a process-reap failure occurred for it. The reap
weight is read back from `TASK_FAILED` events carrying a
`circuit_breaker_weight` payload key -- `proc.reap.cancel_and_reap`'s own
existing hook (Phase 2, explicitly built "for the breaker, once it
exists," per its own docstring), unused by anything until now.

**10. `task_cost` stays deliberately lifetime-accumulated per `task_id`,
never per-run.** `task_cost`'s schema (Phase 1, frozen -- forward-only
migrations, no down-migration) has no `run_id` column, matching spec 8's
own framing ("accumulated per-task cost", not per-task-per-run). A task
once `BLOCKED` with `blocked_reason=cost` therefore stays over its ceiling
across a later `cosmo queue retry` unless `cost.max_cost_per_task_usd`
itself is raised -- the ceiling is a real, standing budget, not something a
retry silently resets. `run_cost`, by contrast, is scoped to one `run_id`
already (the schema's own `run_cost.run_id PRIMARY KEY REFERENCES run_
state(run_id)`), so the run-level ceiling naturally resets each run.

**11. `RunGuardAction` has exactly two members, mapped to two different
task-level outcomes.** `BLOCK_COST` -> `_block(reason=COST)`: spec 7.3's
literal behavior ("that task BLOCKED..., queue continues"). `REQUEUE` ->
`_requeue` (new): spec 3.3's literal "in-flight task returns to QUEUED" for
the run-level wall clock, extended by inference to a confirmed quota
signal too -- neither is the task's own fault, and burning `attempt_count`
against either would be wrong. `ESCALATE_CIRCUIT_BREAKER` (spec 9.3's own
`NextAction` enum, called out as deferred to Phase 8 by both `gate.
validate_task`'s docstring and Phase 7 decision 6) is deliberately *not*
produced by anything in this phase: the breaker's own trip decision is a
run-level judgment made *after* a task's outcome is already known and
recorded (`task_failures.next_action` already written), not a retroactive
rewrite of that row's `next_action` -- the run-level `run.paused` event
(with `triggering_task`) is the breaker's real, separate signal instead.

**12. `EventType.RUN_COST_WARNING` added -- not in spec 9.2's own
enumerated event list.** Spec 7.3 requires "a warning event at 80% of
`max_cost_per_run_usd`" but never names one; 9.2's list has no run-level
warning event at all. Recorded as deviation 21. Emitted at most once per
run (a `cost_warned` flag), not once per task that happens to still be
over the threshold.

**13. `cosmo run --dry-run` is a separate, lightweight CLI-only code path
-- it never constructs a `StoreWriter`, an adapter, or calls `run.loop.
run_queue` at all.** It calls `run.dag.resolve_execution_order` directly
against `store.reader.list_tasks`, printing the order or a clean "cycle"
error. This mirrors `cosmo validate`/`cosmo harness probe`'s existing
"diagnostic entry point, no store side effects" posture (Phase 3/6) rather
than adding a `dry_run` flag to `run_queue` itself, which would have made
every real invocation's control flow branch around a mode that does
nothing.

**14. Cycle detection (`run.dag.find_cycle`) takes a plain `{task_id:
depends_on}` mapping, not a `TaskRow`-shaped API.** `cosmo queue add`
needs to check a cycle that includes the *not-yet-inserted* task being
added -- building a full `TaskRow` for a row that doesn't exist in the
store yet would be awkward for no benefit, since the function only ever
needs the dependency graph shape. `resolve_execution_order` builds this
same shape internally (restricted to non-`done` tasks -- a `done` task
cannot participate in a live cycle, it already ran) and calls `find_cycle`
defensively too, even though `queue add`'s own check should make a cycle
unreachable in practice; belt-and-suspenders given the run loop is where a
cycle would actually jam something.

### Things that will matter later

**No general startup sweep of stale worktrees across runs, despite spec
3.2 naming one** ("a startup sweep prunes worktrees belonging to completed
runs"). Decision 7 above fixes the one specific collision this phase's own
testing hit (a retried, previously-`BLOCKED` task's stale worktree/branch)
but does nothing for a worktree left behind by a task that was never
retried, or by a run that crashed mid-task. Phase 9/10's own territory
(deployment, long-running-process concerns) is the natural place for a
real sweep.

**Quota heuristic and secondary-signal config values are still guesses,
not tuned against a real exhaustion** (`quota.heuristic_consecutive_
threshold`/`heuristic_max_duration_seconds`/`result_error_subtypes`) --
decisions 4/5 above already flag this per-value; noted again here since
it's the kind of thing an acceptance run (Phase 10) is specifically
positioned to falsify or confirm.

**No CLI command to explicitly resume a `PAUSED` run.** A human reviewing
a breaker trip or an interrupted quota pause currently just re-invokes
`cosmo run` (no `--task`), which starts a *new* `run_id`/`run_state` row
rather than resuming the paused one -- `PAUSED` rows simply accumulate with
no resume linkage recorded. This may be the correct v1 posture (the spec
never asks for a resume subcommand, and a fresh run naturally re-resolves
the DAG from current `task_queue` state, achieving the same practical
effect), but it means `run_state` rows don't tell a complete story of "was
this run ever resumed" the way `task_queue`'s own retry history does.

**Cost ceilings are only exercised against `FakeHarnessAdapter`'s
scriptable `total_cost_usd` field, never a real per-token adapter** (none
exists yet) -- unchanged from spec 7.3's own "inert for v1" framing, just
restated here since Phase 8 is the mechanism's first real caller.

**One project/repo per `cosmo run` (DAG mode), same as Phase 7's
single-task `--repo` flag already assumed.** `task_queue` still has no
`project_id`/repo linkage (Phase 7's own carried-forward item); a
multi-project run would need either a schema column or a per-task
resolution step neither this phase nor Phase 7 built. Decided and
documented per the handoff's own request: v1 assumes one project per run.

## Phase 9 — Complete

All exit criteria met, two of the three verified by a real invocation (the
third is genuinely untestable in this environment -- see below), not just
unit-test green. `./check.sh`: 334 tests, 7 skipped (unchanged real-Docker
opt-ins), ~22s.

### What exists

| Path | Contents |
|---|---|
| `src/cosmo/watchdog.py` | `notify()` -- the `sd_notify` `AF_UNIX SOCK_DGRAM` protocol by hand, no dependency. Silent no-op unless `$NOTIFY_SOCKET` is set |
| `src/cosmo/retention.py` | `apply_log_retention` -- prunes `paths.log_dir/harness/<task_id>/*.ndjson` by the task's *current* status (`done`/`blocked`) and `config.log_retention` |
| `src/cosmo/run/loop.py` | Gains: a one-shot pre-loop disk check (`doctor.check_disk`, aborts with `stop_reason=disk_low` at `severity=critical`), `apply_log_retention` called once before `run_create`, and `watchdog.notify` calls at every run-level transition plus once per DAG loop iteration |
| `src/cosmo/config/model.py`, `defaults.toml` | New `LogRetentionConfig`/`[log_retention]` (`done_days=7`, `blocked_days=30`) |
| `src/cosmo/store/enums.py` | `StopReason.DISK_LOW` |
| `src/cosmo/store/migrations.py` | Migration 3: `run_state.stop_reason` CHECK constraint gains `'disk_low'` (recreate-copy-swap, same recipe as migration 2) |
| `src/cosmo/store/reader.py` | `latest_run_id` -- `cosmo report`'s default-run lookup |
| `src/cosmo/cli/main.py` | `cosmo report [--run <id>]` -- renders a `run_state` row plus its `run.summary` event payload |
| `deploy/cosmo-run.service`, `deploy/README.md` | The systemd unit (new territory, no existing home) plus install/rationale notes |
| `tests/test_watchdog.py`, `test_retention.py`, `test_run_disk_check.py` | New, real-socket / real-filesystem-mtime unit tests for the three new modules |
| `tests/test_store_migrations.py`, `test_cli_report.py` | Migration 3 round-trip test; `cosmo report` CLI glue tests |
| `tests/test_run_loop.py` | `_fast_config` now overrides `disk.min_free_gb` down to near-zero -- see decision 1 |

### Decisions made during Phase 9

**1. The pre-run disk check is real, not injectable, and every `run_queue`
test needed a config override because of it.** Wiring `doctor.check_disk`
straight into `run.loop.run_queue` (rather than adding yet another
injectable callable, which every previous phase's own "extend, don't
reimplement" discipline argued against for something this simple) means it
calls real `shutil.disk_usage` against wherever a test's `tmp_path`
actually lives. This host's own `/tmp` is a small tmpfs close to the spec
default 10 GB floor (`docs/handoff.md`'s own pre-existing "known
environment noise" note about `cosmo doctor`) -- every `test_run_loop.py`
test started failing with `disk_low` the moment the check went in, not
because of a bug but because the check was doing exactly its job against a
genuinely low-space host. Fixed by having `_fast_config` override
`disk.min_free_gb` to `0.001` (tests isolate from real environment state,
same discipline `retries.delay_min`/`delay_max` already gets); the check's
own real-abort mechanics get a dedicated test instead
(`test_run_disk_check.py`, which deliberately sets the floor to an
unsatisfiable 1 billion GB rather than trying to simulate a full disk).

**2. The disk check runs once, on the first iteration of the main loop, not
before `run_create`/the `RUNNING` transition.** Considered checking before
`run_create` so a failed run never even gets an `idle`→`running` row, but
that would mean the abort has no queryable `run.stopped` row to explain
itself (spec 3.1's own "a run's outcome should be a real state" framing,
already followed by the DAG-cycle-at-startup case this mirrors). Checking
inside the loop, gated by a one-shot `disk_checked` flag, keeps the abort a
first-class `RunOutcome`/`run_state` row -- same "abort, but leave a
record" posture used for the DAG-cycle-at-startup abort right below it in
the same loop.

**3. `apply_log_retention` runs once at the very top of `run_queue`, before
`run_create`, keyed off `run_id`-independent state.** It doesn't need a
`run_id` at all -- it walks `paths.log_dir/harness/<task_id>/` by task_id,
looks up each task's *current* store status, and deletes what's aged out.
Placed ahead of the disk check deliberately (pruning stale logs is itself a
disk-space action) and ahead of `run_create` since a systemd-managed loop
(Phase 9's own unit) restarts `cosmo run` as a fresh process on every
cycle -- see decision 5 below -- making "prune once per `cosmo run`
invocation" the closest thing to a periodic sweep without a separate cron
or `systemd.timer`.

**4. Playwright trace/screenshot retention (spec 9.5's other bullet) needed
no new code at all -- verified by reading, not guessed.** `gate.parsers.
parse_playwright_json` only ever appends to `StageResult.artifact_paths`
from a *failed* test's own attachments (the `else` branch of its per-test
walk); a task that reaches `DONE` has zero genuine failures on its final
gate run and therefore an empty `artifact_paths` by construction --
"retained only for failing runs" already holds without a dedicated pruning
pass. These artifacts also live inside the task's *worktree*
(`frontend_dir/playwright-report/...`), not `paths.log_dir`, so they were
never this module's territory regardless; worktree lifecycle is `git.
worktree`'s. Recorded here so a future session doesn't rebuild this by
mistake. Separately (unchanged from Phase 8, restated as a still-open
item below): `git.worktree.sweep_stale_worktrees` -- the mechanism that
would actually *remove* a `DONE` task's worktree, and thus its now-empty
`artifact_paths` directory, off disk -- is still never called from
anywhere.

**5. `deploy/cosmo-run.service` uses `Restart=on-failure` +
`RestartPreventExitStatus=1`, not a bare `Restart=always`.** `cosmo run`'s
own exit code is `0` only for `queue_empty`/`completed`; every other stop
(`PAUSED` for the breaker/a confirmed quota exhaustion -- spec 6.5's own
"resuming requires manual intervention" -- or `STOPPED` for a cost
ceiling/disk abort/startup DAG cycle) exits `1`, and none of those are
fixed by an immediate blind restart (`run_cost`/task-cost ceilings would
just be re-hit instantly since a new `run_id` resets `run_cost` to zero --
Phase 8 decision 10). `RestartPreventExitStatus=1` excludes exactly that
clean-exit-1 case from auto-restart. A genuinely wedged process, by
contrast, never reaches `sys.exit` -- systemd's own `WatchdogSec` kill is a
*signal* (`SIGABRT`), not an exit status, so it is unaffected by the
exclusion and still triggers `Restart=on-failure`. **Both halves verified
for real this session**, not just reasoned about: a throwaway `systemctl
--user` unit driving `cosmo.watchdog.notify` directly showed (a) `READY=1`
recognized (`Started ...service`), (b) `WATCHDOG=1` pings keeping it alive
past a short `WatchdogSec`, (c) going silent triggering
`Killing process ... with signal SIGABRT` / `Result: watchdog` followed by
a real restart (`Scheduled restart job, restart counter is at 1`), and (d)
a separate throwaway unit that called `notify(ready=True)` then
`sys.exit(1)` ending in `Active: failed (Result: exit-code)` with **no**
restart scheduled, confirming `RestartPreventExitStatus=1` actually
suppresses it.

**6. This host's WSL2 genuinely has systemd enabled** (`/etc/wsl.conf`'s
`[boot] systemd=true` is set; `ps -p 1 -o comm=` reports `systemd`;
`systemctl --user` works) -- checked for real per the handoff's own
instruction, rather than assumed either way. The "run under systemd
survives a restart, wedged loop caught by the watchdog" exit criterion was
therefore testable here and was tested for real (decision 5). A host
without that flag set would need a different supervision path; not
encountered this session.

**7. `WatchdogSec` is set to 10800s (3h) in the shipped unit, coarser than
ideal, and this is recorded as a known limitation rather than silently
shipped.** `watchdog.notify(watchdog=True)` is called at every run-level
state transition and once per DAG-loop iteration (i.e., roughly once per
task) -- not during a single task's own multi-hour `IMPLEMENTING`/
`VALIDATING` attempt, since that would mean reaching into `task.machine`'s
retry loop or its heartbeat-writing path, both out of this phase's own
scope (`docs/handoff.md`'s own file list names `run.loop.run_queue`'s
"natural per-task-transition point," not a deeper hook). A single task can
legitimately run for `timeouts.implementing_wall` +
`validating_wall` + `committing_wall` + `merging_wall` seconds (~2h25m at
`defaults.toml`'s shipped values) with no ping in between, so
`WatchdogSec` has to sit comfortably above that worst case to avoid
false-triggering on a healthy long task -- meaning a *genuinely* wedged
single task is only caught at the next task-boundary ping, not
immediately. Piggybacking the ping on `task_heartbeat`'s own, far more
frequent writes (spec 4/9.2) would tighten this; left for a later phase,
named explicitly in `deploy/cosmo-run.service`'s own comment too so it
isn't rediscovered as a surprise.

**8. `cosmo report` renders the latest `run.summary` event, not a live
in-progress run.** If a run is still `RUNNING`/`PAUSED` with no
`run.summary` event yet (that event is only emitted once, at the very end
of `run_queue`, mirroring Phase 8's own `_fill_summary_extras`), `cosmo
report` says so explicitly rather than fabricating partial numbers from
mid-run event counts -- `cosmo events tail --run <id>` is still the right
tool for watching a run in progress; `report` is post-run triage,
matching the handoff's own framing.

### Real invocations this session (not just unit tests)

- **OTel content-leakage check (exit criterion 3): a real `claude -p`
  probe**, `CLAUDE_CODE_ENABLE_TELEMETRY=1 OTEL_LOG_USER_PROMPTS=0
  OTEL_METRICS_EXPORTER=console OTEL_LOGS_EXPORTER=console claude -p
  "<prompt containing a unique canary string>"`, output captured and
  grepped. Result: `TELEMETRY_ENV` (`harness/claude/adapter.py`, already
  shipped since Phase 4) is correct and sufficient *as-is* -- no code
  change needed for exit criterion 3. The canary string appears nowhere in
  the captured telemetry. `claude_code.user_prompt`'s `prompt` attribute
  and `claude_code.assistant_response`'s `response` attribute are both
  literally `"<REDACTED>"` (the CLI redacts the *assistant's* response
  too, stricter than the spec's own "prompts and file contents" wording
  asked for). Every metric/log record does carry account-identifying
  resource attributes (`user.email`, `user.account_uuid`,
  `organization.id`, `session.id`) -- expected for usage attribution, not
  a content leak, but worth an operator knowing before pointing
  `OTEL_EXPORTER_OTLP_ENDPOINT` at a shared/third-party collector.
- **Watchdog/restart exit criterion (1): real `systemctl --user` units**,
  full transcript summarized in decision 5 above.
- **Pre-run disk check exit criterion (2): `test_run_disk_check.py`**
  drives the real `run.loop.run_queue` code path (not a mock of
  `check_disk`) with an unsatisfiable floor and asserts on the real
  `RunOutcome`/store/event state -- the closest a fast test can get to "a
  simulated low-disk condition aborts the run before any task starts"
  without actually filling a real disk, which no test should do.

### Fast-follow, same session: three gaps fixed before Phase 10

Reviewing this phase's own "things that will matter later" against the
plan's Phase 10 scope ("run unattended overnight," "reconstruct every
decision without reading a raw log") surfaced that three of them were not
really Phase 10's job at all -- two were already-diagnosed bugs (found and
reproduced by hand in Phase 6 and Phase 7, restated without being fixed in
both Phase 8's and this phase's own sections) that would have derailed an
overnight acceptance run on disk exhaustion rather than let it test
anything new; the third was a real gap in this very phase's own
observability tooling, found by actually trying to use it (decision 11).
Fixed here, immediately after Phase 9's own commit, rather than carried
into Phase 10 as a "finding":

**9. `git.worktree.remove_worktree` now falls back to a throwaway root
container when `shutil.rmtree` can't finish the job.** The root cause
named in Phase 6/7's own state-doc sections -- a gate container writes
build output (Maven's `backend/target/`, most commonly) as root inside the
container, which an unprivileged host-side `shutil.rmtree` can never
unlink -- was worked around by hand both times (a throwaway `alpine`
container) and never taught to the code. `remove_worktree` gains a
`docker_bin: str = "docker"` parameter (same injectable-default convention
as `gate.docker_runner`/`proc.orphans`) and, only if the directory is
still present after the existing `git worktree remove --force` /
`shutil.rmtree(ignore_errors=True)` attempts, bind-mounts the parent
directory into a disposable `alpine:3.21` container and `rm -rf`s the one
entry as root. Best-effort, same posture as the `shutil.rmtree` fallback
it extends -- a leftover directory is a disk-space problem to flag, never
a reason to fail task teardown. Verified two ways: a fast unit test with a
recording fake `docker` script asserting the right mount/argv
(`test_remove_worktree_invokes_docker_with_the_parent_mount_and_entry_
name`), and a real, opt-in (`COSMO_GATE_DOCKER_E2E=1`) test that chmods a
subdirectory to `0o000` (a fast, non-root-requiring stand-in for "the host
user genuinely cannot delete this") and confirms a *real* disposable
container actually removes it -- run for real this session, not just
asserted possible. Re-confirmed a third way afterward: `test_task_
fixture_e2e.py` -- the exact opt-in real-Docker test whose own pytest
teardown threw a genuine `PermissionError` on this in Phase 7 (decision
11 there) -- was re-run for real (`COSMO_GATE_DOCKER_E2E=1`) after this
fix landed and now passes clean, no warning, in ~3 minutes. This only
covers cleanup reached through `create_worktree`/`remove_worktree` (the
real worktree lifecycle `cosmo run` itself always uses); an ad-hoc
scratch repo built by hand outside that lifecycle gets no benefit from
this fix.

**10. `git.worktree.sweep_stale_worktrees` is now called from `run.loop.
run_queue`**, once per invocation, right after the log-retention call and
before `run_create` -- the same "no `run_id` needed, closest thing to a
periodic sweep without a separate cron/timer" placement reasoning as
`apply_log_retention` right above it. Since a `DONE` task's worktree is
already removed inline by `git.merge.merge_task` on the normal happy
path, this mainly recovers two cases spec 3.2 always named but nothing
ever exercised: a task that crashed mid-attempt (no terminal
`remove_worktree` call ever happened for it) and a worktree orphaned by a
killed/restarted process (Phase 9's own systemd-restart-as-fresh-run
design, decision 5 above). A `BLOCKED` task's worktree is still retained
for inspection, unchanged. Covered by a new integration test
(`test_run_queue_sweeps_a_stale_worktree_left_by_a_crashed_prior_process`)
that seeds an orphan directory under `work_dir` with no matching task at
all and confirms `run_queue` prunes it before its own first task runs.

**11. `cosmo events tail` gains `--payload`/`--type`, and a new `cosmo
queue failures <task-id>` command was added -- found missing by actually
trying to diagnose a real failed task through the CLI alone, not by
inspection.** Running a real `run_queue` invocation against a scripted
gate failure and then trying to answer "why did this fail" using only
`cosmo report`/`cosmo queue show`/`cosmo events tail` (the tools this
phase itself shipped) surfaced that `events tail` never printed a
payload at all, and that `task.validation_result`'s own payload
(`gate.validate._stage_payload`) carries failing *test names* but never
`StageResult.error_summary`/`.error_detail` (the actual assertion/stack
text, spec 9.3) -- that detail exists only in `task_failures`, which had
no CLI reader at all (`list_task_failures`, spec 8, previously only
consumed internally by the circuit breaker's own weight calculation).
Concretely: reconstructing *why* `willfail` blocked required opening the
sqlite file by hand and reading `task_failures.error_detail` directly --
exactly what Phase 10's own exit criterion ("reconstruct every decision
without reading a raw log") says shouldn't be necessary. Fixed with two
small, additive CLI changes, not a new event type or schema change:
`events tail --payload` prints each row's JSON body beneath the table;
`events tail --type <event_type>` filters to one type (useful paired with
`--payload`); `cosmo queue failures <task-id>` renders `task_failures`'
full per-attempt history (type, stage, summary, detail, files touched,
next_action) straight from the table `validate_task` already writes.
Verified against the same real `willfail` run used to find the gap:
`cosmo queue failures willfail` reproduced the exact `error_detail` text
(`OrderControllerTest.testCreate: AssertionError: expected 200 got 500`)
across all three recorded attempts.

### Things that will matter later

**`WatchdogSec` granularity (decision 7) is task-boundary, not
task-internal.** Tightening it means piggybacking `watchdog.notify` on
`task_heartbeat`'s writes, which lives in `task.machine`/wherever Phase
7's heartbeat writer actually is -- out of this phase's own scope as
handed off.

**No CLI command to resume a `PAUSED` run, still** (Phase 8's own
still-open item) -- `cosmo report` makes a paused run's state legible, but
doesn't add a way to act on it beyond what already existed (`cosmo run`
again, starting a fresh `run_id`).

**`MemoryMax=` deliberately left commented out in the shipped systemd
unit** -- Open Item 2's own "retune against real data" posture; no real
Phase 9/10 run has produced a usage number to size it against yet.

## v4 workflow changes — Complete

Not one of the plan's numbered phases -- see
[v4-changes-to-workflow-plan.md](v4-changes-to-workflow-plan.md) for the
full design (context, decisions already confirmed, and the exact real-code
anchors it was written against). This section records what actually got
built and every place real code made the plan more concrete than it was.
`./check.sh`: 367 tests, 8 skipped (unchanged real-Docker/real-openspec
opt-ins).

### What exists

| Path | Contents |
|---|---|
| `src/cosmo/store/enums.py` | `TaskStatus.REVIEWING`/`FINISHING`; `FailureStage.ADVERSARIAL_REVIEW` |
| `src/cosmo/store/migrations.py` | Migrations 4-6: `task_queue.status` CHECK widened (reviewing/finishing), `task_failures.failure_stage` CHECK widened (adversarial_review), `task_queue.spec_batch_id TEXT` added |
| `src/cosmo/config/model.py`, `defaults.toml` | `ReviewConfig`/`[review]` (`enabled=true`); `TimeoutConfig.reviewing_wall`/`[timeouts].reviewing_wall=900` |
| `src/cosmo/harness/base.py` | `HarnessAdapter.review(task_id, spec_path, base_branch) -> HarnessResult`, new abstract method |
| `src/cosmo/harness/fake/adapter.py`, `harness/claude/adapter.py` | `review()` implementations |
| `src/cosmo/task/review.py` | New -- the `.cosmo/review-result.json` verdict-file contract (`ReviewVerdict`, `read_review_verdict`, `review_result_path`) |
| `src/cosmo/task/machine.py` | `_do_reviewing`/`_do_finishing`, wired into `run_task`; module docstring's state list updated |
| `src/cosmo/bootstrap/openspec.py`, `bootstrap/__init__.py` | `archive_change()` -- the `FINISHING` step's own subprocess call |
| `src/cosmo/events/envelope.py` | `EventType.TASK_FINISHING_FAILED`, not in spec 9.2's own list (predates `FINISHING`) |
| `src/cosmo/spec/` | New package -- `taskfile.py` (`SpecTaskFile`, `parse_task_file`, `list_task_files`: the `*-task.md` frontmatter contract) |
| `src/cosmo/cli/main.py` | `spec_app` (`cosmo spec add`/`cosmo spec queue`); `_cycle_check`/`_insert_queued_task` extracted and shared with `queue add` |
| `src/cosmo/store/writer.py`, `reader.py` | `StoreWriter.queue_add` gains `spec_batch_id`; `TaskRow` gains `spec_batch_id` (defaulted, so every existing keyword construction stays valid) |
| `templates/harness/claude/skills/spec-enrichment/SKILL.md`, `agents/reviewer.md` | New harness-facing templates |
| `tests/test_task_reviewing.py`, `test_spec_taskfile.py`, `test_cli_spec.py` | New |
| `tests/test_task_machine.py`, `test_run_loop.py`, `test_task_fixture_e2e.py` | `_fast_config` helpers gain `review.enabled=False` (see decision below) |
| `tests/test_store_migrations.py`, `test_bootstrap_openspec.py`, `test_harness_fake.py` | New migration/`archive_change`/`review()` coverage |
| `tests/fixtures/fake_openspec.sh` | Gains an `archive <name> --yes` branch |

### Decisions made during this work

**`task_queue.status`'s own CHECK constraint needed widening too -- a real
gap in the plan document, not just an implementation detail.** The plan's
migration section named only the additive `spec_batch_id` column; it never
mentioned that `REVIEWING`/`FINISHING` need `task_queue.status`'s CHECK
constraint widened first, or every real `queue_transition` call for either
new state would fail outright before any code-level logic ran. Found by
implementing the state machine change and immediately hitting a `sqlite3.
IntegrityError` from `queue_transition`, not by re-reading the plan closely
enough beforehand. Fixed as migration 4 (recreate-copy-swap, same recipe as
migrations 2/3); migration 5 does the analogous `task_failures.
failure_stage` widening the plan *did* call out, and migration 6 is the
plan's own `spec_batch_id` column, renumbered to come after.

**A review's verdict is never read from the harness call's own output --
spec 4's "prose parsing is prohibited as a signal" discipline
(`harness.claude.stream`'s own docstring) rules that out, and `HarnessResult`
has no other harness-agnostic slot for a two-way (or three-way, including
"produced nothing usable") verdict.** Instead the reviewer writes a small
structured file, `.cosmo/review-result.json`, to the worktree -- the same
"watch a file the harness writes" shape `HarnessCapabilities.
reports_native_progress=False` already uses for `tasks.md`, just a
fixed-path single-shot file instead of a polled one. `task.review.
read_review_verdict` reads it back after `adapter.review()` returns;
`HarnessAdapter.review()` itself stays a plain, uniform `HarnessResult`
producer like every other adapter method. Not spelled out at this level of
mechanism in the plan (which only said "a new method on `HarnessAdapter`'s
ABC, mirroring `propose()`/`implement()`").

**A rejected review and a review call that never produced a usable verdict
at all are bounded by two different, independent budgets -- not the plan's
one-line "same `attempt_count`/`max_attempts` budget as a gate failure."**
Rereading the module's own established discipline (`environment_error`
never consumes the code-level retry budget, at either `IMPLEMENTING` or
`VALIDATING`) made clear that only a genuine rejection is a code-level
judgment; a crash, a timeout, or a call that completed but wrote no (or a
malformed) verdict file is an environment problem with the review contract,
not a judgment about the code. A rejection reuses the `attempt_count`/
`will_retry` judgment `VALIDATING`'s own gate-pass already computed for this
cycle (blocks with `BlockedReason.CODE_FAILURE`) -- an unusable verdict
instead shares `VALIDATING`'s own `validating_env_retries` counter, threaded
into and back out of `_do_reviewing` via a small `_ReviewStepResult`
(blocks with `BlockedReason.ENVIRONMENT`/`TIMEOUT`). Confirmed by a test
that hit `classify_harness_failure`'s own `assert not result.success` the
first time this wasn't split out correctly (`test_review_call_with_no_
verdict_file_is_an_environment_retry_not_a_rejection`).

**`REVIEWING` gets its own `timeouts.reviewing_wall` (default 900s,
`proposing_wall`'s own order of magnitude) -- not named in the plan at
all.** Every other harness-invoking state already has a wall clock
(`config over constants`, this codebase's own stated convention); leaving
`adapter.review()` unbounded would have been a real, if narrow, regression
against that convention. No stall variant, matching `proposing_wall`'s own
shape -- one bounded call, not a multi-turn session with a liveness watcher
to stall-check.

**`FINISHING` runs `openspec archive` against `repo_path` (Cosmo's own
dedicated `base_branch` checkout), never `ctx.worktree_path`.** By the time
`_do_merging` returns, `merge_task` has already removed the task's worktree
entirely (spec 3.2's own merge-success cleanup) -- there is nothing left to
run `openspec archive` against there. `git.merge`'s own "`repo_path` is
always on `base_branch`, clean" invariant is exactly what's needed instead:
by the point `FINISHING` runs, `repo_path` already holds the just-merged
commit(s). Real `openspec archive [change-name] --yes` was confirmed by
hand to have no path argument of its own at all -- it resolves `openspec/`
from `cwd`, which is why `archive_change(worktree, name)` passes
`cwd=worktree` rather than a CLI flag.

**`merge_task` (unchanged, per the plan's own "run.loop/git need zero
changes" argument) already transitions a merged task straight to `done` and
emits its own `task.completed`/`task.state_changed` -- `FINISHING` is
layered strictly *after* that, not before it.** The task_transitions trail
for a task with `FINISHING` enabled genuinely reads `..., merging, done,
finishing, done` -- a second, real `done` transition once the best-effort
archive step (successful or not) finishes. Considered and rejected:
running `FINISHING` before `merge_task`'s own completion, to get a cleaner
single `done` at the end -- rejected because it would mean archiving a
change whose merge might still fail on the ladder, and because it would
require reordering/changing `git.merge`'s already-hardened code, which the
plan explicitly didn't want touched.

**A v4-flow task's `PROPOSING` step is expected to name its own `openspec
new change <name>` the same way `run.loop._run_one_task` already derives a
task's branch name -- `Path(spec_path).stem`.** The plan cited that
derivation for `FINISHING`'s own `spec_id` but never said what a *new*
change (created lazily inside `PROPOSING` from a `*-task.md` file, which
has no pre-existing OpenSpec change to name itself after) should be called.
Naming it `Path(spec_path).stem` (the task file's own stem, e.g.
`backend-task` for `docs/specs/add-login-spec/tasks/backend-task.md`) keeps
`FINISHING`'s archive target and `_run_one_task`'s branch name in sync
without either one hardcoding the other's convention, and needs zero
changes to `run.loop` (this is a prompt-content decision inside `templates/
harness/claude/skills/openspec-workflow/SKILL.md`'s existing `openspec new
change <name>` guidance plus the new `spec-enrichment`/`implementer`
prompts, not a state-machine change) -- consistent with the plan's own
"`PROPOSING` gains a new responsibility, not a new contract" framing.

**Existing `FakeHarnessAdapter`-driven tests needed `review.enabled=False`
added to their `_fast_config` helpers.** `config.review.enabled` defaults
`true`, so every pre-existing task-machine/run-loop test now drives a real
`REVIEWING` call through `FakeHarnessAdapter`'s reused single-script
`SUCCESS` response -- which writes no verdict file, so without this change
every one of those tasks would retry to exhaustion and end `BLOCKED`
instead of `DONE`. Same shape as Phase 9's own `disk.min_free_gb` test-
isolation precedent (`docs/handoff.md`'s "Tests isolate from the developer's
environment" convention) -- a new default-on real check, so every test
exercising the real state machine needs an explicit override unless it's
specifically testing that check.

**`cosmo spec add`'s own harness call reuses `adapter.probe(prompt)`
(Phase 3's existing raw-prompt entry point), not a new ABC method.**
Decomposition doesn't fit `propose()`/`implement()`/`review()`'s shapes (no
task_id, no existing spec_path to hand in the way those expect) and
`probe()` already exists for exactly "run one raw prompt, harness-
agnostically" -- the same reasoning that justified `probe()` itself in
Phase 3 (deviation 7) applies again here rather than inventing a fourth
near-duplicate method.

**`_cycle_check`/`_insert_queued_task` extracted from `queue_add` as the
plan asked, but `spec_queue` checks its whole batch atomically before
inserting any of it, rather than calling the per-task check once per file
in a loop.** A cycle introduced by hand-editing one `*-task.md` file
between `spec add` and `spec queue` (the plan's own "the edit window is the
preview, not a separate confirmation UI") should reject the whole batch,
not queue half of it and fail partway through. `_cycle_check` therefore
takes a `dict[task_id, depends_on]` of every not-yet-inserted candidate at
once; `_insert_queued_task` (the real write + duplicate-task_id handling)
is the piece actually shared per-call between `queue add` (one candidate)
and `spec queue` (N candidates, already cycle-checked as a batch).

### Real invocations this session (not just unit tests)

- `cosmo spec add`/`cosmo spec queue`/`cosmo queue ls`/`cosmo queue show`
  run for real (`--harness fake`) against a real scratch git target repo:
  confirmed the preview table, the dependency graph round-tripping through
  real frontmatter, `spec_batch_id` landing correctly, and the CLI's own
  cycle-rejection message.
- `openspec new change`/`openspec archive --yes` run for real against a
  real scratch repo (`test_real_openspec_binary_archives_a_real_change`):
  confirmed `archive`'s cwd-relative resolution (no path argument at all)
  and that a real archive actually moves the change under `openspec/
  changes/archive/`.
- `cosmo init` run for real against a scratch target repo: confirmed the
  two new template files (`agents/reviewer.md`, `skills/spec-enrichment/
  SKILL.md`) sync into `.agent/claude/` and resolve through the `.claude`
  symlink automatically, with zero changes needed to `bootstrap.assets.
  sync_harness_assets` (already a generic whole-tree copy).

### Fast-follow, same session: `--repo` defaults to cwd, validated against registration

Not part of the original v4 plan -- user feedback on the freshly-updated
README (every example spelled out `--repo /path/to/your-project`, even
though the whole point of `cosmo init` registering a project is that Cosmo
can find it again). `run`/`spec add`/`spec queue` all took `--repo` as a
*required* option with no default, and none of them checked the resolved
path against `projects` (`cosmo init`'s own registration, spec 10.4 step
6) at all -- an unregistered or typo'd path was silently accepted and only
failed later, deep inside whatever the command tried to do with it.

Fixed with one shared helper, `cli.main._resolve_project_repo(repo, cfg)`:
`repo` defaults to `Path.cwd()` when omitted (the common case: running
`cosmo` from inside the target repo itself needs no `--repo` at all, only
invoking from somewhere else does), and either way the resolved path is
looked up via `store.reader.find_project_by_path` -- an unregistered
directory fails loudly (`"<path> is not a Cosmo-orchestrated project --
run cosmo init <path> first"`) rather than proceeding. All three commands'
`repo: Annotated[Path, ...]` became `repo: Annotated[Path | None, ...] =
None`.

**Real bug found and fixed as part of this, not a new one introduced by
it:** `cosmo run` never resolved harness via project registration at all
-- `_run_queue_cmd`/`run_cmd`'s single-task path both called
`resolve_harness_name(harness, None, cfg.harness.name)`, hardcoding the
project tier to `None` even though `resolve_harness_name`'s own docstring
states the resolution order as "--harness flag > project registration >
config default" (spec 2) and `cosmo doctor --project-path` already honored
it correctly. `_resolve_project_repo` returns the project's own registered
harness alongside the resolved path for exactly this reason; `run`/`spec
add` now both pass it through. Confirmed by a real invocation: `cosmo run
--dry-run` from inside a registered project now prints `harness: claude
(from project registration)` instead of silently falling back to `(from
config default)`.

`spec queue` doesn't invoke a harness at all, so it only gained the
repo-resolution/validation half, not the harness-tier fix.

### Things that will matter later

**No real `claude -p` review invocation has ever been run** -- `adapter.
review()`'s prompt and `agents/reviewer.md`'s instructions are written and
internally consistent (mirroring `propose()`/`implement()`'s own "thin
prompt, real policy lives in templates" precedent) but, unlike Phase 9's
`claude -p` OTel probe, not yet verified against a real session that
actually writes `.cosmo/review-result.json` in the documented shape. A
strong Phase 10 candidate: queue one real task through a real target repo
with `review.enabled=true` and confirm a real reviewer session produces a
usable verdict, both accept and reject.

**No real `docs/specs/<name>-spec.md` -> `spec-enrichment` -> real
`*-task.md` fan-out has been run either** -- `cosmo spec add`'s CLI
mechanics are verified for real (see above), but with `--harness fake`,
which writes nothing; the skill's own instructions have not yet been
exercised by a real session.

**`review.enabled`/`timeouts.reviewing_wall` are unverified guesses, the
same posture Open Item 2 already names for the original spec 3.3
defaults** -- no real review-call duration data exists yet to size
`reviewing_wall` against.

Kept here so a future spec revision can absorb them in one pass.

## Phase 10 prep — template + two real fixes — Complete

Not one of the plan's numbered phases, and not the acceptance run itself --
requested ahead of Phase 10 to have a real, deliberately simple target repo
(and a polished, project-agnostic harness) ready before queuing real work
into it. See [simple-template-handoff.md](simple-template-handoff.md) for
the original scoping of the template half. `./check.sh`: 374 tests, 8
skipped (unchanged real-Docker/real-openspec opt-ins).

A same-session follow-up pulled from `docs/old-agents-skills/` -- three
Claude Code skill/agent files from the user's pre-Cosmo workflow
(`old-enrich-skill.md`, `old-adversarial-review-skill.md`,
`old-frontend-agent.md`), kept around only as source material for this pass,
not wired into anything. Mapped each to its real Cosmo equivalent and pulled
over only what still applies there, discarding what's interactive
(clarifying questions, "ask the user before writing the file back") or
otherwise contradicts Cosmo's headless posture, and discarding
`old-frontend-agent.md`'s stack specifics entirely (Mantine/Zustand/
TanStack/react-router -- a different, unrelated app's stack, not this
template's). What survived: a concrete "what counts as enriched" checklist
in `spec-enrichment/SKILL.md`; four concrete adversarial-technique bullets
plus a risk-calibration line in `reviewer.md` (kept strictly within the
existing binary approve/reject verdict -- the old skill's severity tiers and
PASS/PASS WITH GAPS/FAIL verdict don't fit `task.review`'s two-state
contract, so those were left out); and three small additions to
`vite-react-local`'s own docs (`vitest-axe` for automated a11y checks, a
no-premature-memoization rule, "flag over-engineering as readily as
under-engineering" in the review checklist). Project templates have no
mechanism to ship their own agent file today (only `docs/` is copied by
`copy_project_docs`; `sync_harness_assets` copies the harness's `agents/`
wholesale, independent of project template) -- inventing one to host a
literal `frontend-developer` subagent would have been new scope nobody
asked for, so the frontend agent's substance went into docs instead, not a
new agent file.

A second same-session follow-up: `templates/harness/claude/settings.json`
now sets `"attribution": {"commit": ""}`, so the harness's own commits carry
no `Co-Authored-By: Claude` trailer. `attribution.commit` (an empty string
hides it) is the current key; the deprecated `includeCoAuthoredBy` boolean
still works but is documented as superseded. Confirmed against the actual
installed `claude` binary's embedded JSON-schema strings (`strings` on
`~/.local/share/claude/versions/2.1.207`), not just the docs site, after an
initial doc-site answer suggested the wrong sentinel value (`"hide"` instead
of `""`) -- verified for real rather than trusted on the first answer, same
discipline this project has followed since Phase 0. Verified end-to-end via
a real `cosmo init` that the synced `.agent/claude/settings.json` in a
scratch target repo carries the key.

A third same-session follow-up, prompted by "where/when is the git author
name/email configured, and could `cosmo init` do this": it could, and now
does. Worktree creation (Phase 5) never sets a local git identity, and a
fresh host with no global `~/.gitconfig` (the same gotcha behind deviation
11) would make the *implementer's* own ad hoc `git commit` during
IMPLEMENTING fail outright, since none of Cosmo's own `-c user.name=...`
machinery covers a commit the harness session writes itself. `cosmo init`
now has a new step (`bootstrap.git_identity`, called from `cli.main.init`
after the existing steps succeed): if the target repo already has an
effective git identity (local or global -- `git config --get` resolves
that same order on its own), warn and ask whether to define a separate one
for Cosmo to use here, prompting for name/email only if the user says yes;
if none exists at all, silently seed the target repo's *local* git config
from `config.git.commit_author_name`/`commit_author_email`, no prompt
needed since nothing conflicts. `--git-author-name`/`--git-author-email`
skip the interactive path entirely for scripted use. `GitConfig.
commit_author_email`'s default also changed from the placeholder
`cosmo@localhost` to `cosmo@entropiainversa.com` per this session's
direction. New `GitConfig.unified_identity` (default `False`) toggles
whether Cosmo's own bookkeeping commits (merge ladder, decisions-log) keep
using `commit_author_name`/`commit_author_email` as a distinct synthetic
identity (default) or drop their `-c user.name=...`/`-c user.email=...`
override entirely and inherit whatever's configured locally -- the same
identity the implementer's own commits already use, i.e. one identity for
every commit in the repo instead of two.

### What exists

| Path | Contents |
|---|---|
| `templates/projects/vite-react-local/docs/` | New project template: frontend-only Vite+React+TS+Tailwind, `localStorage` persistence, no backend, no Docker for the app itself. 7 files: `frontend/architecture.md`, `frontend/state-management.md`, `frontend/styling.md`, `persistence.md`, `data-model.md`, `testing.md`, `base-standards.md` |
| `src/cosmo/gate/runner.py` (`_e2e_stage`) | Fixed: a missing `backend_dir` no longer skips the e2e stage outright -- only a missing `frontend_dir` does. See deviation 34 |
| `templates/harness/claude/hooks/test_path_guard.py` | `PROTECTED_PATTERNS` widened: `**/*.spec.tsx`, `**/*.test.tsx`, `**/*.spec.jsx`, `**/*.test.jsx` added alongside the existing `.ts` patterns. See deviation 35 |
| `templates/harness/claude/agents/reviewer.md` | `tools: Read, Grep, Glob, Bash, Write` added to the frontmatter (no `Edit`) -- makes "the reviewer does not fix what it's reviewing" structural, not just an instruction, wherever this subagent definition is actually honored |
| `templates/harness/claude/CLAUDE.md` | Guardrail table row updated for the widened test-path patterns; the "Project knowledge" section's `docs/` file list reworded from a fixed enumeration (`docs/backend/`, `docs/frontend/`, ...) to "varies by template, skim what's actually there" -- it was quietly describing `java-spring-react`'s own shape, which the new frontend-only template breaks |
| `tests/test_gate_runner_e2e_backend_optional.py` | New -- exercises `_e2e_stage` directly against `fake_gate_docker.sh` plus a real local `http.server` standing in for the container health check (`wait_for_http` makes a real HTTP call regardless of what `docker` binary is configured), covering: frontend-only runs e2e for real, a repo with both dirs still starts backend as before, a repo with no `frontend/` still skips entirely |
| `tests/test_hooks_test_path_guard.py` | New case: a `.test.tsx` write is denied |
| `src/cosmo/bootstrap/git_identity.py` | New -- `GitIdentity`, `read_configured_identity`, `set_local_identity`: pure subprocess mechanics against a target repo's own local git config |
| `src/cosmo/cli/main.py` (`init`, `_ensure_git_identity`) | New `--git-author-name`/`--git-author-email` options; the warn/confirm/prompt flow, run after `run_init` succeeds |
| `src/cosmo/config/model.py`, `defaults.toml` | `GitConfig.unified_identity` (bool, default `False`); `commit_author_email` default changed to `cosmo@entropiainversa.com` |
| `src/cosmo/git/merge.py` | `author: tuple[str, str] \| None` throughout (`_git`/`_assert_ready`/`attempt_merge_ladder`/`merge_task`) -- `None` omits the `-c user.name=...`/`-c user.email=...` override entirely |
| `src/cosmo/task/machine.py` | `_do_merging` and `_git_commit_decisions_log` both branch on `config.git.unified_identity` to decide `None` vs. the explicit tuple |
| `tests/test_bootstrap_git_identity.py`, `tests/test_cli_init.py`, `tests/test_git_merge.py`, `tests/test_task_machine.py` | New/extended coverage for all of the above, including the pre-existing `test_rerunning_init_...` test which now needs `input="n\n"` since a second `cosmo init` against the same repo hits the new prompt |
| `templates/harness/claude/settings.json` | `permissions.allow: ["Write", "Edit", "Bash"]` added (see deviation 38) |
| `src/cosmo/harness/claude/adapter.py` (`_build_argv`) | `--allowedTools Write Edit Bash` added (see deviation 38) |
| `tests/test_harness_claude_adapter.py` | New `test_argv_carries_allowed_tools_regardless_of_settings_json` |
| `src/cosmo/cli/main.py` (`spec_add`) | Warns and asks for confirmation before re-invoking the harness when `docs/specs/<name>-spec/tasks/*.md` already exist for that spec; declining reuses the existing files and returns without touching the harness at all (see deviation 40) |
| `tests/test_cli_spec.py` | New `test_spec_add_with_existing_task_files_and_declined_confirmation_skips_the_harness`, `test_spec_add_with_existing_task_files_and_confirmed_reruns_the_harness` |
| `src/cosmo/config/defaults.toml` (`gate.frontend_image`) | Bumped `node:20.18-bookworm` -> `node:24.19-bookworm` (see deviation 41) |
| `templates/projects/vite-react-local/docs/frontend/architecture.md`, `templates/projects/java-spring-react/docs/frontend/architecture.md` | "Key dependencies" gains a Vite/Node compatibility note tied to `gate.frontend_image`; "Vite 5's preview server" generalized to "Vite's preview server" now that no major is pinned (see deviation 41) |
| `templates/harness/claude/CLAUDE.md` | New "Toolchain versions -- pin, don't take 'latest'" section: don't let a package manager resolve whatever's newest against a gate image that isn't pinned to match; always commit the real lockfile the install step produced (see deviation 42) |
| `src/cosmo/harness/claude/stream.py` (`extract_quota_signal`) | Ignores a `rate_limit_info` payload with `status: "allowed"` -- no longer a confirmed quota signal on its own (see deviation 43) |
| `src/cosmo/task/machine.py` (`_do_proposing`) | Skips the harness call and returns `PROPOSED` directly when `<worktree>/openspec/changes/<spec_id>/tasks.md` already exists (see deviation 44) |
| `src/cosmo/harness/claude/stream.py` (`describe_tool_call`), `src/cosmo/harness/claude/adapter.py` (`_relay_activity`) | Both gain an optional `cwd` parameter; the worktree-root prefix is collapsed to `.` before the activity line's length cap is applied (see deviation 45) |
| `tests/test_harness_claude_stream.py`, `tests/test_task_machine.py` | New: `test_a_lone_allowed_status_rate_limit_event_is_not_a_quota_signal`, `test_describe_tool_call_collapses_the_worktree_prefix_before_truncating`, `test_describe_tool_call_without_cwd_leaves_the_path_untouched`, `test_proposing_is_skipped_when_the_worktree_already_has_a_complete_change` |
| `tests/fixtures/stream_json/rate_limit_allowed_only.ndjson` | New fixture, built from the real capture behind deviation 43 |
| `src/cosmo/git/worktree.py` (`sweep_stale_worktrees`, new `find_last_commit_touching`, `reset_worktree_to_commit`) | Retains `QUEUED` worktrees too (see deviation 46); two new small git helpers for the soft-reset retry path (see deviation 47) |
| `src/cosmo/run/loop.py` (`_run_one_task`) | Worktree reuse no longer scoped to the current run_id -- reuses any existing `worktree_path` that's still a real directory (see deviation 46) |
| `src/cosmo/store/writer.py` (`queue_retry`), `src/cosmo/cli/main.py` (`queue_retry`) | `attempt_count` reset to 0; new `clear_worktree`/`--repo`; soft-resets to PROPOSING's own commit when found, else falls back to a full worktree+branch removal (see deviation 47) |
| `templates/harness/claude/CLAUDE.md` | New "This call is one-shot -- there is no 'later'" section, warning against `ScheduleWakeup`/background-and-end-turn (see deviation 48) |
| `tests/test_git_worktree.py`, `tests/test_run_loop.py`, `tests/test_store_writer.py`, `tests/test_cli.py` | New/updated: `test_sweep_retains_blocked_and_queued_worktrees_and_prunes_everything_else`, `test_a_gracefully_requeued_task_reuses_its_worktree_in_a_later_run`, `test_queue_retry_resets_attempt_count_and_clears_worktree_path`, `test_queue_retry_on_a_blocked_task_with_a_worktree_removes_it_for_real`, `test_queue_retry_with_an_already_proposed_change_keeps_the_worktree` |

### Decisions made during this work

**The gate's e2e stage silently no-op'd for every backend-less repo -- found
writing this template, not by running anything overnight.** `_build_stage`/
`_unit_stage` already handled a missing `backend_dir` or `frontend_dir`
independently (each side checked and run separately), but `_e2e_stage`
required *both* to exist or returned `passed: true` with no tests run at
all. A frontend-only project's Playwright suite would never have executed
through the gate -- indistinguishable from a project with no e2e suite,
which defeats spec 1.2's own "the gate is the only source of truth"
guarantee this whole harness is built around (see `CLAUDE.md`'s "one rule
that matters most"). Confirmed the user wanted this fixed now rather than
left as a documented limitation, since shipping `testing.md`'s "Playwright
is gate-enforced" claim while knowing it was false for this exact template
was worse than the smaller, well-scoped fix. Fixed by making only
`frontend_dir` the skip condition; the backend container, its health check,
and `VITE_BACKEND_URL` are all conditional on `backend_dir` existing.
Verified via the fake-docker + real-local-`http.server` technique already
established in `test_gate_docker_runner.py`'s
`test_wait_for_http_succeeds_against_a_real_local_server`, not a real Docker
run (no fixture Vite/React repo without a backend existed to run one
against; the real-Docker opt-in fixture repo remains the Java+Spring one).

**The test-path guard's literal `.spec.ts`/`.test.ts` patterns (spec 2.5's
own wording) leave every React component test unprotected in a TS+JSX
codebase.** A test file that renders JSX must itself be `.tsx`, not `.ts` --
`Widget.test.tsx` next to `Widget.tsx` is the normal React Testing Library
shape, and none of spec 2.5's four literal patterns match it. This is a
project-agnostic gap (any TypeScript+JSX project hits it, not only this
template), which is why it's a hook fix rather than something worked around
in the new template's own docs. Widened `PROTECTED_PATTERNS` to also cover
`.tsx`/`.jsx` spec/test variants; `annotation_guard.py`'s skip-annotation
patterns needed no change (they match on content, not file path, so they
already applied inside a `.tsx` file same as any other).

**`cosmo spec add` could never write files in `dontAsk` mode -- the real gap
was Claude Code's workspace-trust dialog, not a missing `permissions.allow`
entry.** Found running `cosmo spec add todo-list` for real against
`vite-react-local`'s scratch target repo: the headless session did the
enrichment correctly (invoked `spec-enrichment`, read every relevant `docs/`
file, decomposed into well-formed tasks) but every `Write`/`Edit`-create/
`Bash mkdir` call was denied with "Permission to use \<tool\> has been denied
because Claude Code is running in don't ask mode," and the session gave up
and returned the task content as prose instead of writing it. First
hypothesis -- `templates/harness/claude/settings.json` had no
`permissions.allow` block at all, so nothing matched in `dontAsk` mode --
was real but insufficient: added `permissions.allow: ["Write", "Edit",
"Bash"]` to the template, re-synced it into the scratch repo via `cosmo
init`, and reran. Identical failure. The raw CLI stderr (not part of
stream-json, easy to miss) explained why: `Ignoring 3 permissions.allow
entries from .claude/settings.json: this workspace has not been trusted.`
Claude Code gates *all* settings-file-sourced permission rules behind an
interactive trust dialog that `-p` mode explicitly skips showing (per
`claude --help`) without ever granting -- so a freshly-synced worktree,
which by construction has never been through that dialog and never can be in
an unattended run, silently loses its entire allow list every single time,
with no error surfaced anywhere in the adapter-visible stream. Confirmed the
mechanism directly against the real `claude` binary in an isolated scratch
directory outside Cosmo entirely: `permissions.allow` in `.claude/
settings.json` alone denied Write/Bash every time; the same tool names
passed as the CLI's own `--allowedTools Write Edit Bash` executed
immediately, unaffected by workspace trust, because that gate applies only
to settings-*file* permission rules, not CLI-flag ones. Fixed by adding
`--allowedTools Write Edit Bash` to `ClaudeCodeAdapter._build_argv` --
per-invocation, like `--permission-mode` and `--setting-sources` already
are, rather than a mutable global-state workaround (e.g. programmatically
marking every worktree path "trusted" in `~/.claude.json`, which would need
re-doing for every fresh worktree Phase 5 creates and depends on an
undocumented internal file format). `permissions.allow` stays in
`settings.json` too, for the case a human runs `claude` interactively in a
synced repo outside Cosmo and accepts the trust dialog themselves. Re-ran
`cosmo spec add todo-list` against the same scratch repo after the fix:
6 `*-task.md` files written for real and the preview table rendered,
confirming both this fix and the previously-unverified spec-enrichment
fan-out noted earlier this session.

**`cosmo spec add` re-invoked the harness unconditionally, even when nothing
had changed -- real, billed cost for a no-op.** Found running it a second
time against the same spec that had already produced 6 task files: the
fresh session (no memory of the prior run) read the spec, the docs, and the
existing task files, judged them still correct, and made zero `Write`
calls -- but that judgment cost ~$0.48 and 25 turns to reach, and nothing
about the CLI signaled a re-run was even happening versus a fresh one.
Fixed per deviation 40: `spec_add` now checks `tasks_dir` before resolving
the harness at all and asks first; declining reuses the existing files
(verified for real against the same repo -- zero new harness log, byte-
identical files, instant).

**A real `cosmo run` (the user's own, not this session's) surfaced two
compounding, unrelated bugs in `vite-react-local`'s scaffold task, neither
about the permissions/harness work above.** `scaffold-app` failed 3 times
and was permanently blocked: attempts 1-2 hit `npm ci`'s `EUSAGE` (no
committed `package-lock.json`) -- identical failure both times despite
`retry_context` correctly surfacing the exact npm error on attempt 2, so a
prose retry hint alone wasn't enough; attempt 3 got past the lockfile issue
but hit a real environment mismatch: `npm install` resolved current Vite
(8.2.2), whose `engines.node` (`^20.19.0 || >=22.12.0`, confirmed via
`npm view vite engines`) the then-pinned `gate.frontend_image`
(`node:20.18-bookworm`) doesn't satisfy, surfacing as an opaque `rolldown`
native-binding crash rather than a clear version-mismatch message. The
template's own `architecture.md` had already implicitly assumed Vite 5
elsewhere (its "Gate compatibility" section's Host-header guidance) without
ever pinning it, so "latest" had silently drifted two majors past what the
doc was actually written against.

First fix attempted -- pinning `Vite 5.x` explicitly in the template doc --
works and was verified for real (`npm view vite@5 engines`:
`^18.0.0 || >=20.0.0`, satisfied by 20.18), but only defers the same class
of failure to whenever Vite (or any dependency) next raises its floor.
User pushed back on shipping an aging pin as the "definitive" fix rather
than modernizing both sides -- correct call: deviation 41 instead bumps the
shared `gate.frontend_image` to `node:24.19-bookworm` and drops the
version-specific pin in favor of an explicit doc note tying the two
together, so a future Vite bump is "check `npm view vite engines` against
what the gate image is" rather than "guess and find out from a cryptic
Docker error." Verified for real, not just from registry metadata: a real
`npm create vite@latest --template react-ts && npm install && npm run
build` inside `node:24.19-bookworm` (real `docker run`, not a fixture)
built cleanly with Vite 8.2.2, and the full opt-in real-Docker gate suite
(`COSMO_GATE_DOCKER_E2E=1 pytest tests/test_gate_fixture_e2e.py
tests/test_task_fixture_e2e.py`, 7 tests, real containers throughout, not
mocked) passed end to end against the bumped image in 10m43s -- build, unit,
e2e, the diff gate, the gitleaks backstop, and flaky-test classification all
still work with the new image, not just the isolated Vite build. `java-spring-react`'s own frontend docs had
the identical unpinned-Vite gap (same shared `gate.frontend_image`) and got
the same doc fix for consistency, even though today's failure was only ever
exercised through `vite-react-local`.

Deviation 42 generalizes the lockfile half of this into
`templates/harness/claude/CLAUDE.md` rather than only `vite-react-local`'s
own docs, since "don't trust `npm install`/`pip install`/etc.'s latest
resolution against an unpinned gate image, and always commit the real
lockfile" is a project-agnostic failure class, not specific to this one
template or to Vite.

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
| 21 | `EventType.RUN_COST_WARNING` added, not in spec 9.2's own enumerated event list | §7.3, §9.2 | 8 | §7.3 requires "a warning event at 80% of max_cost_per_run_usd" but never names one; §9.2's list has no run-level warning event at all |
| 22 | `HarnessResult` gains `quota_window`/`quota_resets_at`/`tool_call_count`, all defaulted | §2.2, §7.2 | 8 | The uniform result object needs a harness-agnostic way to carry a quota/liveness signal out of the adapter layer; spec 2.2 names none |
| 23 | `rate_limit_info.rateLimitType`/`resetsAt` identified as the real field connecting spec 7.1's two named windows to an actual wire value | §7.1, §7.2 | 8 | Found by rereading `tests/fixtures/stream_json/api_retry.ndjson` (already captured in Phase 3, never fully used); the spec names "five-hour rolling"/"weekly" conceptually but no field to detect which one from |
| 24 | `task.types.RunGuardAction` (`BLOCK_COST`/`REQUEUE`) added | §7.3, §3.3, §7.1 | 8 | The minimal, purely additive seam `task.machine.run_task` needed so the run loop can stop a task's retries (cost) or hand control back (wall clock/quota) without reimplementing any of Phase 7's retry/classification logic |
| 25 | `cosmo run --task <id>` and the no-`--task` DAG path are two separate CLI code paths, not one path routing single-task through the DAG loop | §3.1, §5 | 8 | Protects Phase 7's already-tested single-task behavior (including its `run_id=None` posture) from `run_queue`'s run-level concerns (breaker/quota/cost/wall clock), which single-task mode was never specified to have |
| 26 | `StopReason.DISK_LOW` added, schema migration 3 | §9.5, §3.1 | 9 | The pre-run disk check needs its own real, queryable stop reason distinct from `manual` (already reused for the startup DAG-cycle abort) |
| 27 | `cosmo report` CLI command added | §9.5's own "post-run triage" framing | 9 | The plan's own Phase 9 summary names this explicitly; spec 9.2's `cosmo events tail` alone leaves `run.summary`'s payload as raw JSON |
| 28 | `watchdog.notify` pings at run-level transitions and once per DAG-loop iteration, not at task-internal (heartbeat) granularity | §9.5 | 9 | The handoff's own file list names `run.loop.run_queue`'s per-task-transition point as the wiring seam, not a deeper hook into `task.machine`'s retry/heartbeat loop; recorded as a known `WatchdogSec` sizing tradeoff (decision 7) rather than silently shipped |
| 29 | `task_queue.status` CHECK widened (schema migration 4), beyond what the v4 plan's own migration section named | v4 plan §4 | v4 | `REVIEWING`/`FINISHING` need `queue_transition` to accept their status values; the plan named only the additive `spec_batch_id` column, not this |
| 30 | A review verdict is delivered via a worktree file (`.cosmo/review-result.json`, `task.review`), never a `HarnessResult` field or the session's own text output | v4 plan's `REVIEWING` section | v4 | Spec 4's "prose parsing is prohibited as a signal" rules out reading the session's free-text output; `HarnessResult` has no other harness-agnostic slot for a three-way verdict |
| 31 | A rejected review and an unusable review call are bounded by two independent budgets (`attempt_count`/`will_retry` vs. a shared `validating_env_retries`), not one budget as the plan's one-line summary implied | v4 plan's `REVIEWING` section | v4 | Only a rejection is a genuine code-level judgment; a crash/timeout/malformed-verdict call is `environment_error`, which this module's own established discipline never lets consume the code-level retry budget |
| 32 | `TimeoutConfig.reviewing_wall` added (`config.timeouts`, default 900s) | v4 plan's `REVIEWING` section (not named there) | v4 | Every other harness-invoking state already has a wall clock (`config over constants`); `adapter.review()` was otherwise the one unbounded harness call in the whole state machine |
| 33 | `EventType.TASK_FINISHING_FAILED` added, not in spec 9.2's own enumerated list (predates `FINISHING`) | v4 plan's `FINISHING` section | v4 | `_do_finishing`'s best-effort archive failure needs a real, queryable warning event distinct from `task.blocked`/`task.failed` (FINISHING never blocks) |
| 34 | `_e2e_stage` only skips e2e when `frontend_dir` is missing; a missing `backend_dir` now runs a frontend-only e2e path instead of skipping the whole stage | §1.2, §6.1 | 10 prep | A backend-less repo's e2e suite used to never run through the gate at all (`passed: true`, no tests) -- indistinguishable from having no e2e suite, found writing `vite-react-local`'s `testing.md` |
| 35 | `test_path_guard.py`'s `PROTECTED_PATTERNS` gains `**/*.spec.tsx`, `**/*.test.tsx`, `**/*.spec.jsx`, `**/*.test.jsx` beyond spec 2.5's literal `.ts`-only list | §2.5 | 10 prep | Spec 2.5's literal patterns leave every React component test (`.test.tsx`) unprotected in any TS+JSX project, not only this one -- found writing `vite-react-local`'s `testing.md` |
| 36 | `cosmo init` gains a git-identity step (`bootstrap.git_identity`, `cli.main._ensure_git_identity`): warns and asks before replacing an existing target-repo git identity, seeds `config.git.commit_author_name`/`commit_author_email` when none exists, `--git-author-name`/`--git-author-email` for scripted use | §3.4, §10.4 | 10 prep | Neither worktree creation (Phase 5) nor `cosmo init` (Phase 4) ever gave the implementer's own ad hoc commits a guaranteed identity; a fresh host with no global `~/.gitconfig` would make the first IMPLEMENTING commit fail outright |
| 37 | `GitConfig.unified_identity` added (bool, default `False`) | §3.4 | 10 prep | User direction: support both "Cosmo's own bookkeeping commits use a distinct synthetic identity" (default) and "every commit in the repo uses one identity" as an explicit, durable config choice, not a one-off |
| 38 | `--allowedTools Write Edit Bash` added to the Claude adapter's argv, alongside (not instead of) `templates/harness/claude/settings.json`'s new `permissions.allow` | §2.3, §2.5 | 10 prep | Neither the spec nor `claude --help` documents that Claude Code's workspace-trust gate silently discards every settings-file `permissions.allow` entry for a directory that's never been through the interactive trust dialog -- true of every headless worktree by construction; found by hand running `cosmo spec add` for real, confirmed against the real binary in isolation |
| 39 | `git.merge`'s `author` parameter widened to `tuple[str, str] \| None` (`_git`, `_assert_ready`, `attempt_merge_ladder`, `merge_task`) | §3.4 | 10 prep | Mechanical requirement of deviation 37: `None` is how `unified_identity=True` tells `_git` to omit the `-c` override and inherit the repo's local git config instead |
| 40 | `cosmo spec add` warns and asks for confirmation before re-invoking the harness when `tasks_dir` already has files, rather than always re-running unconditionally | v4 plan's raw-spec front door | 10 prep | Found running it twice for real against the same spec: the harness re-invocation is billed, real usage every time, with no code-level idempotency check at all -- a second run with nothing changed still cost real money to re-verify (and re-confirm) the same output |
| 41 | `gate.frontend_image` bumped `node:20.18-bookworm` -> `node:24.19-bookworm`; the Vite version pin added by deviation-adjacent doc work earlier this session was reverted in favor of an explicit compatibility note instead | §1, §6.1 | 10 prep | Found running a real `cosmo run` (not this session's own harness calls -- the user's, in their own shell, on the same target repo): the scaffold task's `npm install` resolved current Vite (8.2.2, requiring Node >=20.19/22.13 per `npm view vite engines`), incompatible with the old pinned image, and failed the gate's build stage with an opaque `rolldown` native-binding error. Pinning Vite back down to an old major was the first fix attempted and works, but only defers the same class of failure; bumping the shared gate image is the actual fix, verified for real both narrowly (`npm create vite@latest --template react-ts && npm install && npm run build` inside `node:24.19-bookworm`, real Docker run, succeeded) and broadly (the opt-in real-Docker gate suite, `COSMO_GATE_DOCKER_E2E=1`, against the existing `vite@^5.4.9`-pinned fixture repo) |
| 42 | `templates/harness/claude/CLAUDE.md` gains a "Toolchain versions -- pin, don't take 'latest'" section, project-agnostic across every template | Not named in the spec | 10 prep | Same real run as deviation 41 also failed twice on a missing `package-lock.json` before the version mismatch -- the harness's retry got the exact npm error via `retry_context` and still repeated the identical mistake. Nothing told it to run a real `npm install` and commit the lockfile, or to check a gate image's pinned version before trusting a package manager's "latest." This is a reusable failure class (any template, any package manager), not specific to `vite-react-local`, so it belongs in the shared harness policy doc, not one template's own docs |
| 43 | `extract_quota_signal` (`harness/claude/stream.py`) now returns no signal at all when the lone `rate_limit_event` it saw carries `status: "allowed"` | §7.1, §7.2 | 10 prep | A real overnight run's `scaffold-app` attempt failed on `error_max_turns` (nothing to do with quota) but got reported as a *confirmed* `quota_exhausted_5h` and paused the run for ~4h -- the routine, once-per-session `rate_limit_event` every call gets (real `resetsAt`/`rateLimitType`, `status: "allowed"`) was being trusted unconditionally as evidence of exhaustion. The project's own `api_retry.ndjson` fixture happens to avoid this because its `rate_limit_event` is overwritten by a later real `api_retry` event before `extract_quota_signal` ever reads it -- last night's real capture had no such pairing. User caught it live ("I still have quota left") |
| 44 | `_do_proposing` (`task/machine.py`) skips the harness call entirely and returns `PROPOSED` directly when the (possibly reused) worktree's `openspec/changes/<spec_id>/tasks.md` already exists | Not named in the spec | 10 prep | A task requeued mid-run (quota/wall-clock guard) reuses its worktree (`run.loop._run_one_task`'s own worktree-reuse branch) but `run_task` re-ran `_do_proposing` unconditionally on every re-entry -- a second real, billed harness call to re-author a change already fully proposed and unchanged. `tasks.md` is the last artifact `propose()`'s workflow produces, so its presence is proof PROPOSING already finished in this exact worktree; a genuinely fresh worktree (a real retry, or a new run) never has the file, so this only ever skips the reused-worktree case |
| 45 | `describe_tool_call` (`harness/claude/stream.py`) collapses the task's worktree-root prefix to `.` in every activity line, before its `_MAX_ACTIVITY_LINE` truncation applies, not after | Not named in the spec (item 3, this session's own `on_activity` addition) | 10 prep | The worktree's absolute path (`/home/.../work/<run_id>/<task_id>/frontend/...`) alone was long enough to eat the entire 100-char cap on every single activity line, truncating before the actual filename ever appeared -- user's own real terminal showed exactly this. Collapsing after truncation would be too late (the useful suffix is already gone); the fix has to run on the un-truncated `detail` string |
| 46 | `sweep_stale_worktrees` (`git/worktree.py`) retains a `QUEUED` task's worktree, not only a `BLOCKED` one; `_run_one_task` (`run/loop.py`) reuses any existing `worktree_path` regardless of which run_id created it | §3.2 | 10 prep | Deviation 44's fix never got a chance to help on a real overnight retry: the user killed a falsely-paused `cosmo run` and started a fresh one, whose startup sweep deleted scaffold-app's `QUEUED`-but-not-`BLOCKED` worktree (only `BLOCKED` was ever retained) before `_run_one_task` could reuse it, wiping an already-complete PROPOSING pass. Safe now specifically because deviation 47 makes `cli.main.queue_retry` the *only* place that ever discards a worktree deliberately -- a `QUEUED` task's `worktree_path` being set is therefore unambiguous evidence of "safe to resume," never "abandoned" |
| 47 | `store.writer.queue_retry` resets `attempt_count` to 0 (previously untouched) and gains a `clear_worktree` parameter; `cli.main.queue_retry` gains `--repo` and, when the worktree's `openspec/changes/<spec_id>/tasks.md` is already committed, does a soft reset (`git.worktree.reset_worktree_to_commit`: hard-reset to that commit + `git clean -fdx`) instead of removing the worktree outright | §3.2, §6.3 | 10 prep | Two real bugs found the same night: (1) a manually retried task carried over its already-exhausted `attempt_count`, so the very next genuine failure blocked it again with zero real retries available -- confirmed live (`attempt_count: 4` against `max_attempts: 2` after exactly one post-retry attempt); (2) the user asked directly whether a retry would re-run PROPOSING needlessly -- it would have, under the deviation 44/46 fix alone, since a full worktree wipe destroys the already-valid proposal along with the failed implementation. `find_last_commit_touching` locates the commit PROPOSING left via the same `tasks.md` file deviation 44 already keys off (a structural git fact, not a commit-message string -- spec 4's "prose parsing is prohibited" applies here too even though this is CLI convenience, not classification) |
| 48 | `templates/harness/claude/CLAUDE.md` gains a "This call is one-shot -- there is no 'later'" section | Not named in the spec | 10 prep | Root-caused the actual `package-lock.json` failure behind deviations 43/44/46/47's real repro, via the raw session log: `IMPLEMENTING` launched `npm install` in the background, correctly reasoned it should wait, called `ScheduleWakeup`, and ended its turn assuming a later resumption that a one-shot `claude -p` call never provides -- the install was still running when the process exited, no lockfile was ever written, and the gate's `npm ci` failed on a generic error with no visible connection to the real cause. `node_modules` was ~249 packages deep (real progress, genuinely killed mid-flight), not a fabricated or skipped install |
| 49 | `templates/harness/claude/settings.json`'s `permissions.deny` gains `ScheduleWakeup`, `ToolSearch`, `TaskOutput` (bare tool names, no path pattern); `CLAUDE.md`'s "one-shot" section rewritten to state the denial as fact and drop the old "or if it must background, poll it yourself" escape hatch | §2.5 | 10 | Deviation 48's prose alone did not hold: a later real `IMPLEMENTING` session for `scaffold-app` backgrounded `npm install` again, then followed the *other* half of the old guidance ("poll it yourself") -- a `ps -p <pid>` wait loop, `sleep 240`, then `ToolSearch` to locate `TaskOutput` and poll the background task with it -- burning 81 turns and 5.83M cached input tokens ($2.90) on waiting instead of working before hitting `error_max_turns`. That single call is the most likely real cause of the run's next event: a *confirmed* `quota_exhausted_5h` pause. Verified for real, not assumed: two headless `claude -p` invocations against a scratch repo with the updated `settings.json` (same `--setting-sources project`/`--allowedTools Write Edit Bash` flags the adapter uses) explicitly instructed the model to force a `tool_use` call for all three tools regardless of expected outcome -- zero `tool_use` blocks for any of the three appeared in either transcript, confirming `permissions.deny` removes them from the tool list entirely rather than exposing-then-rejecting them, and that this holds even though the same workspace-trust gate behind deviation 38 separately still ignores `permissions.allow` in the same file. The already-running `scaffold-app` worktree does not get a fresh `sync_harness_assets` call on resume (`run.loop._run_one_task`'s reuse branch skips it, deviation 46) so its `.agent/claude/` was re-synced by hand from the template to actually pick the fix up before the next attempt |
| 50 | New `run.recovery` module (`acquire_run_lock`/`RunLockHeldError`, `reconcile_interrupted_tasks`), `StopReason.CRASHED`, migration 7 (`run_state.stop_reason` widened) | v5 plan part 1 | v5 | The plan's own spec; recorded here because it closes the exact "task interrupted mid-flight is lost forever" gap the plan opened with |
| 51 | `store.failure_signature.classify_failure_signature` (and its `task_failures.failure_signature` column, migration 8) lives under `cosmo.store`, not `cosmo.task` as the plan's own prose implies | v5 plan part 5 (Class 1) | v5 | `cosmo.task.__init__` imports `task.machine`, which imports `store.writer` -- putting the classifier under `cosmo.task` and importing it from `store.writer` (its one real caller, at the `record_task_failure` chokepoint) would import a partially-initialized `store.writer` module. Moving the module one layer down avoids the cycle without reshaping either package |
| 52 | `reconcile_interrupted_tasks` runs *after* the new/resumed run's own `run_state` row is created/transitioned, not "immediately alongside `sweep_stale_worktrees`" as the plan's own prose suggested | v5 plan part 1 | v5 | Found by a real test failure, not by inspection: `task_failures.run_id`/`task_transitions.run_id` both hold a real foreign key to `run_state(run_id)` (`PRAGMA foreign_keys=1`, enforced), and the new `run_id` doesn't exist as a row until `writer.run_create`/`run_transition` runs -- reconciling before that raises `sqlite3.IntegrityError` for every interrupted task found. `sweep_stale_worktrees` has no such constraint (it never writes task/run rows), so it can stay where the plan put it |
| 53 | `run.loop.run_queue` split into a thin lock-acquiring wrapper plus `_run_queue_locked` (the renamed original function body), rather than wrapping the existing ~230-line function body in a `try/finally` in place | v5 plan part 1 (pidfile lock) | v5 | Avoids reindenting the whole existing loop (and its many internal `break`s) purely to guarantee `RunLock.release()` runs on every exit path, including an exception; the wrapper is the only new indentation level |
| 54 | `QuotaDecision.status` gains `RunStatus.RUNNING` as a legal value (previously documented as "`PAUSED` or `STOPPED`, never anything else") | v5 plan part 7 | v5 | `bypass_5h_with_credits` needs `decide()` to tell the run loop "keep going, don't pause" for a confirmed `five_hour` signal -- reusing `RUNNING` (already a legal `RunStatus`) needed no new enum value, only a new legal combination and a `bypassed: bool` flag to disambiguate it from the loop's own already-`RUNNING` steady state |
| 55 | `cosmo run` converted from a leaf `@app.command("run")` into a `typer.Typer` sub-app (`run_app`) with an `invoke_without_command=True` callback, guarded on `ctx.invoked_subcommand is None` | v5 plan part 2 | v5 | The only way to add `cosmo run resume` as a true subcommand of `run` without changing `cosmo run --task`/`cosmo run --dry-run`'s existing flag-based invocation at all -- confirmed by the full existing test suite passing unchanged after the conversion |
| 56 | New `store.reader.list_events_after`/`latest_event_rowid`, keyed on SQLite's implicit `rowid`, not `events.sequence` | v5 plan parts 3 and 4 | v5 | `sequence` is scoped per `run_id` (one counter per scope, `event_sequence`), so it can't express "everything new since I last checked" *across* runs/scopes the way `cosmo notify watch` and `cosmo events tail --follow` both need; `rowid` is a stable, monotonic, insertion-ordered id every non-`WITHOUT ROWID` table gets for free |
| 57 | `notify.Sink.send(event: Event)` also receives synthetic, never-persisted `Event`s (`notify.watch._stale_event`, `event_type="watch.stale"`) alongside real ones read back from the `events` table | v5 plan part 3 | v5 | The plan's own reasoning: a staleness alert has no row to read by construction (nothing is being written by a dead process) -- the watcher has to construct the message itself, and reusing the same `Event`/`Sink` shape for it (rather than a second, parallel notification path) keeps `TelegramSink` and any future sink down to one method |
| 58 | `run.recovery.reconcile_interrupted_tasks` excludes `run_id == run_id` (the run it's reconciling *for*) from its "any `running` row is a crash" scan | v5 plan part 1 | v5 | A direct consequence of deviation 52's own ordering fix: `run.loop.run_queue` now transitions the new/resumed run's own row to `running` *before* calling this function (required by the FK constraint deviation 52 fixed), so without this guard every fresh `cosmo run` immediately marked its own brand-new run `stopped`/`crashed`. Caught by two real-invocation-shaped tests (`test_run_disk_check.py`, `test_run_loop.py`) written to verify an unrelated fix (the double-`RUN_STOPPED`-emission bug, pre-existing, also fixed this pass) -- both asserted exactly one `run.stopped` event and got two |
| 59 | `store.migrations.migrate` toggles `PRAGMA foreign_keys` OFF around each migration's own transaction (outside `executescript`'s `BEGIN`/`COMMIT`, since SQLite no-ops the pragma inside one), then ON again plus a `PRAGMA foreign_key_check` immediately after | §8.1 | 10 | Migrations 3/4/5/7 all recreate-copy-swap a table other tables hold a live FK reference to. Invisible in every existing test and on a freshly-bootstrapped real DB (both only ever insert the referencing row *after* migrating), but a real, already-running database — this project's own Phase 10 acceptance-run store, concretely — has those referencing rows in place *before* a later migration is even written, and `DROP TABLE` on such a table raises `sqlite3.IntegrityError` under `foreign_keys=ON`. Reproduced by hand on a copy of the real acceptance-run `cosmo.db`, then as a real regression test (`test_migration_7_succeeds_against_a_real_database_with_referencing_rows`) |
| 60 | New `templates/harness/claude/hooks/background_task_guard.py` (PreToolUse, denies `Bash` with `run_in_background: true`), wired into `settings.json`'s `Bash` matcher alongside `commit_integrity_guard.py` | §2.5 | 10 | Deviation 49's `permissions.deny` fix denies three *tool names* used in the two incidents found by hand so far, not the underlying behavior — a third real `scaffold-app` `IMPLEMENTING` session backgrounded `npm install` a different way (`Bash`'s own `run_in_background: true`, which none of those three tools cover), then polled the PID with ordinary already-allowed shell commands (`kill -0` loops, `tail --pid`, `sleep`+`ps`), made zero `tasks.md` progress for the whole attempt, and was killed by Cosmo's own stall timer. Confirmed via the raw harness transcript, not inferred |
| 61 | `cli.main.queue_retry`'s kept-worktree path (the `propose_commit`-found branch) now calls `sync_harness_assets` on the worktree before requeuing | §10.5 | 10 | `create_worktree` only syncs `.agent/<harness>/` once, at creation; `reset_worktree_to_commit`'s own `git clean -fdx` wipes it right back out anyway since a worktree's `.agent` is written straight to disk, never `git add`ed. Before this fix, a task retried after a harness-policy change (e.g. deviation 60's new hook) ran the retried attempt with no guardrail hooks and no `settings.json` at all — worse than merely stale ones. Found wiring the real fix for a real blocked task through this exact path |
| 62 | `cli.main.run_cmd`'s single-`--task` path now reuses an existing `task.worktree_path` (`WorktreeInfo` built directly from the DB row) instead of calling `create_worktree` unconditionally, mirroring `run.loop._run_one_task`'s own reuse rule (deviation 46) | §3.2 | 10 | `create_worktree` always names the branch `task/<spec_id>` regardless of `run_id` — Phase 7's single-task path never had the DAG loop's reuse check at all, so retrying a task that already had a kept worktree (deviation 47/61) made `cosmo run --task` collide with the still-checked-out branch and fail outright on `git worktree add`, before ever invoking the harness. Never exercised until this session actually drove a real blocked task's retry through the single-task path rather than the DAG loop |
| 63 | `gate.docker_runner.container_flags` gains `--user "{uid}:{gid}"` and `-e HOME=/tmp`, applied uniformly to every gate container (`run_container` and `run_detached_service` both) | §1.1, §1.2 | 10 | Every gate container's image defaults to root, so anything a build/e2e stage writes into the bind-mounted worktree (`node_modules`, `dist`, `target`) came back root-owned on the host — the unprivileged `IMPLEMENTING` harness session then has no way to ever remove it itself (`rm`, `sudo`, and even a cross-filesystem `mv` all fail with `Permission denied`, confirmed live). `HOME=/tmp` gives npm/Maven's cache a writable home without leaving dotfiles in the worktree. Verified by hand against the real `node`, `maven`, and `mcr.microsoft.com/playwright` images — including a real headless Chromium launch as a non-root UID (Playwright's own Docker image already runs with an equivalent `--no-sandbox` posture) |
| 64 | `git.worktree.reset_worktree_to_commit` gains a `docker_bin` parameter and, after `git clean -fdx`, force-removes whatever a dry-run `git clean -fdxn` still lists (via `_force_remove_root_owned`, the same throwaway-root-container trick `remove_worktree` already had) | §3.2 | 10 | `git clean -fdx` alone cannot remove a root-owned leftover any more than an unprivileged `rm` can (same underlying cause as deviation 63) — confirmed live: a real blocked task's `node_modules_old` survived a real `queue retry`'s `git clean -fdx` completely intact, root ownership unchanged. Defense in depth: fixes it even for a cause deviation 63 doesn't cover (a task's own Dockerfile, a future stack's build tool) |
| 65 | New `store.failure_signature.detect_repeat_block`/`RepeatBlock`, two new taxonomy entries (`secrets_stray_backup_artifact`, `playwright_image_version_mismatch`), `retries.repeat_block_threshold` config (default 2); `cli.main.queue_retry` refuses (reports every prior occurrence, requires `--force`) once a task's most recent terminal block repeats a prior one's class key (`failure_signature`, or `failure_stage:error_summary` when no signature classified) more than that many times | Not named in the spec | 10 | `attempt_count` resetting to 0 on every `queue retry` (deviation 47) has no memory of *why* a task kept blocking across separate runs — real evidence: `scaffold-app`'s own `error_max_turns` block recurred 3 times across 3 different runs in this project's real acceptance-run history, each time silently handed 2 more attempts. User's own framing: Cosmo should "halt and report instead of just keep on trying" once it recognizes a repeat |
| 66 | Migration 9 (`task_queue.resume_at_stage`), `store.writer.queue_resume_at`, `task.machine.run_task(resume_at: TaskStatus = IMPLEMENTING)`; `cli.main.queue_retry` sets `resume_at_stage` instead of resetting the worktree when the most recent block was an `environment_error` at `commit`/`merge`; `queue_transition` clears the column unconditionally on every real transition (consumed exactly once) | Not named in the spec | 10 | `COMMITTING`/`MERGING` are the only two states whose own `environment_error` gets no in-run retry at all (`_do_committing`/`_do_merging` always `will_retry=False`) — every earlier stage already retries its own `environment_error` in place (deviation 19 and `_do_reviewing`'s own dual-budget design). `queue retry`'s only recovery mechanism used to discard everything back to the `PROPOSING` commit regardless, which for these two stages meant throwing away a fully `IMPLEMENTING`+`VALIDATING`+`REVIEWING`-passed candidate to redo it identically — confirmed live: a real task's `MERGING` block (target repo had unrelated uncommitted changes) was retried the old way before this existed, discarding a build/unit/e2e-green implementation for nothing. User's own framing, generalized correctly past the original single-stage report: "should this work for all states completed on retry, to not retry the ones that finished correctly" |
| 67 | `gate.playwright_image`/`playwright_npm_version` moved from `v1.50.0-noble`/`1.50.0` down to `v1.49.0-noble`/`1.49.0` | §1.1 | 10 | Not a permanent fix by itself — see `docs/v6-project-template-aware-stuff-plan.md` for why chasing this value in Cosmo's own global config is the wrong axis long-term. Made to match what `scaffold-app`'s own `frontend/package.json` actually had pinned at the time (`docs/testing.md` in the target repo now pins `1.49.0` explicitly, closing the real gap: nothing previously told a fresh scaffold attempt which exact version to converge on, so different attempts resolved different npm versions and flip-flopped against whichever image tag was configured) |
| 68 | `cli.main._EMIT_LIFECYCLE_INFO_TYPES` gains `TASK_STATE_CHANGED`; `_print_emit` gains a `from_state -> to_state` detail line (with `task_id` and an `HH:MM:SSZ` time prefix from `event.timestamp`) for that event type, and the interpolated detail is now passed through `rich.markup.escape` | v5 plan part 6 | 10 | v5 part 6's own stated goal was to make `TASK_STATE_CHANGED`/`RUN_PAUSED`/etc. visible in the one live terminal an operator already has open — but the shipped `_EMIT_LIFECYCLE_INFO_TYPES` allowlist only ever had `RUN_STARTED`/`RUN_RESUMED`/`RUN_SUMMARY`, so `TASK_STATE_CHANGED` (always `Severity.INFO`) was silently dropped by the `WARNING`+ filter — a live `cosmo run` showed harness tool-call chatter but never which task, what state, or when it last changed, exactly the gap the user reported live. Fixing it live surfaced a second real bug: the first version interpolated `[{task_id}]` unescaped into a Rich-markup string, which Rich silently swallows as a bogus style tag (verified by hand: the task id vanished from the printed line entirely) — fixed with `rich.markup.escape`. Two new tests in `test_cli.py` call `_print_emit` directly against a captured `Console` to guard both the filter and the escaping |
| 69 | `task.machine._do_finishing` now commits `openspec archive`'s own output in `repo_path` (new `_git_commit_archive` helper, mirrors `_git_commit_decisions_log`'s scoped `git add`); separately, `cli.main.init` now calls a new `bootstrap.git_branch.commit_bootstrap_output` after `_ensure_git_identity`, committing whatever `openspec/`/`docs/`/`.agent/<harness>/`/root-symlink steps just wrote — skipped when `git_branch == SKIPPED_DIRTY` (a human's own pre-existing dirty tree, not Cosmo's to commit). Also: `deploy/cosmo-run.service`/`cosmo-notify.service` move `StartLimitIntervalSec`/`StartLimitBurst` from `[Service]` to `[Unit]` | §3.4 (finishing), §10.4 (init), §9.5 (deploy) | 10 | Found live, end to end, driving this session's acceptance-run queue to full completion (all 6 `todo-frontend-app` tasks reached `done`) and then deliberately exercising every item `docs/handoff.md` still listed as unvalidated. `_do_finishing`'s bug: confirmed by real `git status` on the target repo after `scaffold-app` completed -- `openspec archive` moved files but never committed them, so `todo-data-model`'s own `MERGING` immediately refused ("has uncommitted changes"); fixed and then confirmed clean across 4 more real task completions in the same run. `cosmo init`'s bug is a different, more fundamental instance of the identical symptom, found by deliberately reproducing `docs/handoff.md`'s finding-#7 mystery ("how did `.agent/claude/CLAUDE.md` go uncommitted") in a fresh scratch repo: `cosmo init` itself never committed anything it wrote -- confirmed by hand (`git status` immediately after a real `cosmo init` showed `openspec/`, `docs/`, `.agent/`, and every root symlink as untracked), and the very first task ever queued against that scratch repo hit the exact same `MERGING` refusal on its first attempt, before any task-level bug had a chance to dirty anything. A background investigation (see this table's own finding #7 note) additionally traced *one* recurrence of the original CLAUDE.md instance to `run_init`'s unconditional `sync_harness_assets` re-sync on an already-registered repo after the template moved on (`f3460b1`/`4b31b65` in the target repo's own history) -- `commit_bootstrap_output` closes both causes at once, since both leave real, committable diffs in the same working tree it already scans. The `[Service]`/`[Unit]` systemd bug was found installing both units for real as `systemctl --user` unit (no system-wide `sudo` available in this session) -- `journalctl` showed "Unknown key 'StartLimitIntervalSec' in section [Service], ignoring" on systemd 259 for *both* shipped units, meaning the documented restart-storm cap was silently inert; `cosmo-run.service` itself otherwise worked correctly for real (`Type=notify`'s `sd_notify` STATUS= string `"stopped: queue_empty"` visible in `systemctl status`, no restart on the clean `queue_empty` exit), and `cosmo-notify.service` refused to start exactly as documented (`notify.enabled is false -- nothing to watch`) with no Telegram credentials configured. Also confirmed for real this session, no code change needed: `task.machine.run_task(resume_at=MERGING)` resuming a real blocked task with zero new harness/gate calls; the Docker `--user`/`HOME` fix producing zero root-owned files across 5 more real full gate runs; `cli.main.queue_retry`'s repeat-block guard actually refusing a real retry and `--force` actually overriding it (exercised against a real blocked task with 3 seeded `task_failures` rows replaying `scaffold-app`'s own real `error_max_turns` history, since no task in this session's real queue happened to repeat-block 3 times on its own); and `run.recovery.reconcile_interrupted_tasks` requeuing a real `kill -9`-crashed task (`task.interrupted` event, `environment_error @ propose` failure row, `proposing -> queued -> proposing` in one real next `cosmo run`) -- confirmed only for the queue-driving `cosmo run` path, since `run.loop.acquire_run_lock`/`reconcile_interrupted_tasks` are never called from `cosmo run --task`'s single-task path at all, which a first attempt at this test surfaced by accident (a crashed `--task` invocation leaves both the task stuck and its harness subprocess orphaned, with nothing to recover either automatically) |
| 70 | `cli.main.run_cmd`'s single-`--task` path now acquires the same v5 `acquire_run_lock`, calls `git.worktree.sweep_stale_worktrees` then `run.recovery.reconcile_interrupted_tasks(run_id=None)` before its own `task.status != "queued"` check -- in that order, matching `run.loop._run_queue_locked`'s own ordering exactly. `reconcile_interrupted_tasks`'s `run_id` parameter is now `str \| None` (was `str`) so this caller can pass `None`, preserving Phase 7's "no run tracking" posture for a task-level failure/transition row with no `run_state` row to attribute to (`task_failures.run_id`/`task_transitions.run_id` are both nullable for exactly this reason) | Not named in the spec | 10 | Deviation 69's own real `kill -9` test surfaced this by accident: a crashed `cosmo run --task` process left its task stuck at a non-`queued` status forever (invisible to `run.dag.resolve_execution_order`, which only considers `queued` tasks) -- the *next* `cosmo run --task <same-id>` hit the `not queued` check and refused outright, a genuine dead end with no recovery path except `queue retry` (a full fresh start, discarding the worktree). Fixing it surfaced a second, real bug on the very next real re-run: `reconcile_interrupted_tasks` alone nulls `task_queue.worktree_path` in the DB but never touches the actual git worktree/branch the crashed attempt created, so the freshly-requeued task's own `create_worktree` call collided with the still-existing `task/<spec_id>` branch and failed outright (`fatal: a branch named 'task/trivial2' already exists`, confirmed live). `sweep_stale_worktrees` is what actually removes that branch/worktree, and its own docstring's pruning rule reads each task's *current* status -- it must run *before* reconciliation flips that status to `queued`, or an already-orphaned worktree would look "safe to resume" and never get swept. Verified end to end against two consecutive real `kill -9`s in a scratch repo: first, a stale lock file confirmed the lock is now actually acquired (absent before this fix); second, a real fresh `PROPOSING` session started cleanly with no worktree collision. Two new tests in `test_cli_run.py` cover the reconciliation-before-status-check ordering and a held-lock's clean CLI error, without needing a real process kill |
| 71 | v7 items 1+3 (`docs/v7-complete-queue-done-fixes-plan.md`): (1) new `StopReason.BLOCKED_REMAINING` (migration 10, same recreate-copy-swap recipe as migration 7) -- `run.loop.run_queue`'s `if not order:` branch now chooses it over `QUEUE_EMPTY` whenever `summary.blocked_by_reason` is non-empty, so a run stuck because every remaining task is `BLOCKED` no longer renders green/exit-0 identically to a genuinely finished queue (`cli.main._RUN_SUCCESSFUL_STOP_REASONS` simply excludes it, which already yields both the yellow styling and the nonzero exit code with no further CLI change needed). (2) new `store.writer.queue_unblock` + `run.recovery.requeue_cost_blocked_tasks`, called unconditionally at `run_queue` startup alongside `reconcile_interrupted_tasks`: re-checks every `blocked`/`cost` task against *this* invocation's config and clears the block if no longer over `max_cost_per_task_usd` (a human raised the ceiling, or disabled it, since the block happened) -- `attempt_count`/`worktree_path` both preserved, since the cost guard fires before an attempt starts and nothing failed. New `EventType.TASK_COST_REQUEUED` marks it | Not named in the spec | 10 | Design-doc-driven, not found by hand this session -- `docs/v7-complete-queue-done-fixes-plan.md` diagnosed the gap from the Phase 10 acceptance run's own real timing data (`scaffold-app`: 10h15m of its 19h37m total spent sitting `queued`/`blocked` with nobody noticing) before any code was written. Items 2 (wiring up real `cosmo notify watch` credentials) and 4 (spec-authoring parallelism) from that doc are explicitly out of scope here -- the user deferred them, one needs a real Telegram token this session doesn't have, the other isn't code. Verified: `./check.sh` green (514 passing, up from 506); a hand-rolled scratch DB confirmed migration 10 applies cleanly on top of a real schema-version-9 database and old `run_state` rows survive; new regression tests cover both the stop-reason branch (including the two *existing* cost-ceiling tests that used to assert `QUEUE_EMPTY` for what was actually this exact bug) and the auto-requeue end to end (`max_cost_per_task_usd` raised between two real `run_queue` calls against the same store, no manual `queue retry` in between) |
| 72 | `cli.main.spec_add`'s "no raw spec and no `--from`" error branch now creates `docs/specs/` (`spec_path.parent.mkdir(parents=True, exist_ok=True)`) before printing "write it there directly" -- previously only the `--from` branch created the directory, so the hand-write path told the user to write a file into a directory that didn't exist yet, with no `mkdir` step of its own | Not named in the spec | 10 | Found by the user testing a project template by hand (v6 second-stack prep): `docs/specs/` isn't part of *any* project template's own `docs/` (`bootstrap.docs.copy_project_docs` only ever mirrors `templates/projects/<name>/docs/`, and `docs/specs/` is deliberately spec-batch content, not stack boilerplate) -- true of `_blank`/`java-spring-react` too, not template-specific despite how it was first reported. New regression test (`test_spec_add_without_a_raw_spec_file_still_creates_docs_specs`) asserts the directory exists after the same error path the existing test already covers |
| 73 | `cli.main.spec_add` and `cli.main.harness_probe` both now pass `on_activity=_print_activity` to `adapter.probe(...)` | Not named in the spec | 10 | User complaint: `cosmo spec add` prints `harness: ...` then goes completely silent until it finishes, times out, or fails -- no visibility into what the harness is doing during a potentially `proposing_wall`-second-long call. `HarnessAdapter.probe`'s own `on_activity` hook already exists for exactly this (the same mechanism `cosmo run`'s live terminal uses at 3 other call sites), `spec_add` just never wired it up; `cosmo harness probe` had the identical gap, from the same copy-pasted probe+timeout dance (spec_add's own template). Fixed both. New regression tests (`test_spec_add_wires_live_activity_output_to_the_probe_call`, `test_harness_probe_wires_live_activity_output_to_the_probe_call`) monkeypatch `FakeHarnessAdapter.probe` to assert the *exact* `cli.main._print_activity` callable is passed (identity check, not a stand-in) and that calling it actually reaches the terminal -- the latter is also `cosmo harness probe`'s first CLI-level regression test at all, closing a pre-existing gap (only the adapter's own `probe` method had a direct unit test before) |
| 74 | `cli.main.spec_queue` gains `_namespace_batch` (`f"{name}-{task_id}"` for every id/`depends_on` edge in a spec batch, computed before the cycle check and insert); `_render_spec_preview` renders the namespaced ids/depends_on so `cosmo spec add`'s preview matches what `spec queue` actually inserts; `spec_queue`'s insert loop now checks `get_task` before each insert and skips (prints "already queued, skipping", does not raise) a task_id that already exists under *this same* `spec_batch_id`, still hard-failing on a genuine collision against a different batch | Not named in the spec | 10 | First real two-project collision found live: `habits-frontend-app`'s own `habit-tracker` spec batch picked `task_id: scaffold-app` for its scaffold task -- the exact id `todo-frontend-app`'s spec batch had already used and finished. `task_queue.task_id` is a single global primary key shared by every project's `cosmo.db`, but `templates/harness/claude/skills/spec-enrichment/SKILL.md` only ever promised a task_id "unique within this spec." Two real, concrete failures resulted before this was caught: `habit-date-lib`/`habit-types-and-persistence`'s `depends_on: [scaffold-app]` looked satisfied by the *other* project's `done` row even though `habits-frontend-app` was never scaffolded; and `spec_queue`'s own batch-insert loop (`for tf in task_files: _insert_queued_task(...)`, no per-item skip) hard-exited on the very first collision it hit, silently dropping every task alphabetically after it in that same invocation -- confirmed live, `cosmo spec queue habit-tracker` truncated the same way across three separate invocations before the cause was found. New tests cover the cross-project-reuse case directly, an external (non-batch) `depends_on` staying bare, and the rerun-is-a-no-op case; 4 existing tests' assertions updated (their fixtures happened to use already-namespaced-looking ids by coincidence, not by any enforced rule, so they masked this exact gap) |
| 75 | `cli.main._print_emit` adds `EventType.TASK_VALIDATION_RESULT` to `_EMIT_LIFECYCLE_INFO_TYPES`; new `_validation_result_detail` renders `passed=…, unit=pass/FAIL (Np/Nf/Ns), e2e=pass/FAIL (…)`, appending a pointer to `cosmo queue failures <task_id>` on failure | Not named in the spec | 10 | Same class of gap as deviation 68 (`TASK_STATE_CHANGED`), found the same way -- a user watching a real `cosmo run` saw `VALIDATING` run a real Docker gate for tens of seconds and print nothing at all, on either outcome. A *passing* `task.validation_result` is `severity=info` and wasn't in the allowlist at all (dropped silently, worse than deviation 68's bug); a *failing* one (`severity=warning`) did clear the severity filter but had no `detail` case of its own, printing as a bare `>> task.validation_result` with zero pass/fail breakdown -- indistinguishable from "nothing happened" at a glance. `error_summary`/`error_detail` deliberately live in `task_failures`, not this event's payload (spec 9.2), so the new detail only summarizes what the payload actually carries rather than trying to duplicate that. Two new regression tests, same style as deviation 68's own `test_print_emit_shows_task_state_changed_with_task_id_and_states` (a passing case, and a failing case asserting the `queue failures` pointer appears) |
| 76 | `task.machine._do_proposing` threads `spec_id` (`Path(ctx.spec_path).stem` -- the same value `_do_finishing`'s `openspec archive` call already assumes) into `adapter.propose(...)`'s `context` dict; `harness.claude.adapter.ClaudeCodeAdapter.propose` reads `context["spec_id"]` and pins the exact required change name into its prompt (falling back to `spec_path.stem`, matching `task_id`'s own existing fallback one line above, when the caller doesn't supply one) | Not named in the spec | 10 | `_do_finishing`'s own docstring already documented the assumption ("a v4-flow task's own PROPOSING step is expected to name its `openspec new change` the same way") but nothing enforced it -- `openspec-workflow/SKILL.md` only ever said "use a short kebab-case name," leaving the actual choice entirely to the propose session's own judgment. Confirmed live: every single task in `habits-frontend-app`'s real `habit-tracker` batch fired `TASK_FINISHING_FAILED` (`Change 'scaffold-app-task' not found. Available changes: scaffold-app`, and the identical shape for every task after it) because the propose session reasonably stripped the task file's own `-task` suffix instead of matching `Path(spec_path).stem` verbatim. Fixed at the actual source of the mismatch (an explicit, pinned instruction in the prompt) rather than trying to reverse-engineer the real name after the fact in `_do_finishing` -- the same "deterministic enforcement over prompt-following" preference this codebase already applies elsewhere (e.g. the review verdict's own structured-JSON-file convention). Two new regression tests on `ClaudeCodeAdapter.propose` pin the prompt's exact required-name instruction and its fallback behavior when `spec_id` is absent from the context |
| 77 | `bootstrap.docs.copy_project_docs` now `mkdir(parents=True, exist_ok=True)`s `docs/specs/` unconditionally at the end of its own copy loop, for every project template -- not counted in `DocsCopyResult.created`/`.skipped`, which still track template files only | Not named in the spec | 10 | Deviation 72 (`spec_add` mkdir'ing `docs/specs/` before its own "write it there directly" error) only ever fixed the *lazy* creation path; it deliberately left `cosmo init` itself not creating the directory, since `docs/specs/` isn't part of any template's own `docs/`. The user re-hit the empty-`docs/specs/`-after-init symptom against a fresh `vite-react-local` init and, on being asked, confirmed the actual want was `cosmo init` creating it proactively for discoverability, not another `spec_add` regression -- confirmed live against a real scratch `vite-react-local` init both before (directory absent) and after (directory present, empty) this fix. New regression test in `test_bootstrap_docs.py` asserts the directory exists after a `copy_project_docs` call whose fixture template ships no `specs/` of its own |
| 78 | `templates/projects/vite-react-local/docs/testing.md`'s E2E section gains two new rules, alongside the existing `BASE_URL` one: pin `@playwright/test` to exactly `1.49.0` (matching `gate.playwright_image`'s `mcr.microsoft.com/playwright:v1.49.0-noble`, `config/defaults.toml`'s already-defined but never-wired `playwright_npm_version`), and configure the `json` reporter to `playwright-report/results.json`, the exact path `gate.runner`'s e2e stage reads | Not named in the spec | 10 | Found live driving a real `cosmo run` against `pomodoro-frontend-app`'s `pomodoro-timer` batch: two of five tasks (`scaffold-app`, `timer-ui`) each burned a full failed attempt on the *same* class of e2e-stage failure before self-correcting on retry -- `scaffold-app` fixed a reporter that never wrote `playwright-report/results.json` (gate: `error_summary="playwright produced no report"`, indistinguishable from the suite never running); `timer-ui` fixed an unpinned `@playwright/test` resolving newer than the gate's `v1.49.0-noble` image has browser binaries for (`browserType.launch: Executable doesn't exist at .../chrome-headless-shell`). Both are template-level gaps, not task-level bugs -- every future project scaffolded from `vite-react-local` would rediscover both by trial and error on its own first e2e-touching task, same as `todo-frontend-app`'s Phase 10 `crypto.randomUUID()` secure-context workaround before it. Fixed at the same layer `openspec-workflow`/`spec-enrichment` SKILL.md prose already lives at (prompt-level guidance, not code) -- no test added, matching the existing untested `BASE_URL` rule's own precedent; nothing in this repo's own code path changed |
| 79 | New `events.format.event_detail`, shared by `cli.main._print_emit` (the live terminal) and `notify.telegram.format_event` (Telegram) -- one human-readable-phrase builder per event type instead of two independently-maintained copies; `notify.telegram.format_event` now uses it (falling back to an indented raw-JSON payload dump for an event type it doesn't recognize) plus a severity emoji/word header, replacing the old bare `json.dumps(payload)` dump; `notify.watch._ALWAYS_NOTIFY_TYPES` gains `TASK_COMPLETED` (previously silent at the default `warning` threshold -- only the final `run.summary` notified, never an individual task finishing); new `cosmo notify config` (`cli.main.notify_config`) is a one-shot interactive wizard -- prompts for a bot token, discovers the chat id via `notify.setup.discover_chat_id` (`getUpdates`, walking the user through messaging the bot first if none found yet), writes the `[notify]` table via new `config.loader.write_user_config_table` (round-trips the file through `tomllib`/`tomli_w`, `chmod 600` unconditionally), and sends one real test message via new `notify.setup.send_test_message` before declaring success, mirroring `cosmo doctor`'s "verify for real" posture; `notify.setup`'s two Bot API calls raise `TelegramApiError` on failure (unlike `TelegramSink.send`'s best-effort, must-never-raise posture -- a human is watching this one) | Not named in the spec | 10 | Requested directly: the user found the existing Telegram messages unreadable (a bare `json.dumps` payload dump) and setup entirely manual (hand-editing the user config file, manually polling `getUpdates` to find a chat id -- exactly what this session's own earlier manual Telegram setup had to do by hand). Also promotes `task.completed` to always-notify and sets this real deployment's own `~/.config/cosmo/config.toml` `min_severity` to `info`, both per explicit user decision during design discussion, not a default this session invented. New tests: `test_events_format.py` (12, the shared formatter directly), `test_notify_telegram.py` (+2, the human-readable path and the raw-payload fallback), `test_notify_watch.py` (+1, `TASK_COMPLETED` always-notify), `test_config.py` (+4, `write_user_config_table`: fresh file, preserves other tables, overwrites its own prior values, `0o600` permissions), `test_notify_setup.py` (6, `discover_chat_id`/`send_test_message` against a faked `urlopen`), `test_cli_notify.py` (+5, the wizard's full interactive flow: fresh setup, reuse-existing-chat-id, discovery-retry-loop, a rejected token, and a test-message failure after a successful write) |
| 80 | `.gitignore`'s blanket `data/` rule (added during the open-source release-prep "gitignore hardening" pass) matched not just the intended repo-root runtime-state dir but also `src/cosmo/gate/data/` -- the directory holding `quarantine.yml`/`quarantine-candidates.yml`, which `gate/quarantine.py` loads by path relative to its own installed location (`Path(__file__).with_name("data")`), not via a packaging manifest. Since hatchling's default wheel build excludes gitignored files, both yml files were silently dropped from every `cosmo` wheel built after that commit -- tests stayed green (they run against the source tree, where the files still physically exist) while a real `uv tool install .`-built `cosmo` crashed with `FileNotFoundError` the moment any real `cosmo run` reached the e2e gate's `load_quarantine` call. Fixed by anchoring the rule to `/data/` (repo root only) and git-adding the two now-unignored files; rebuilt the wheel and confirmed via `zipfile` inspection that `gate/data/*.yml` are present, then reinstalled the real (non-sandboxed) `uv tool` and confirmed the files exist on disk | Not named in the spec | Post-v0.1.0 (found via a real user `cosmo run`, not this session's own testing) | New regression tests in `test_gate_quarantine.py`: one loads both bundled files from their real default (`configured=None`) path rather than a `tmp_path` fixture, and one asserts neither is `git check-ignore`d -- both would have caught this before it reached an installed tool. `docs/handoff.md`'s "environment gotchas" already warned that a sandboxed `XDG_DATA_HOME` silently redirects `uv tool install` to the wrong prefix; hit that same gotcha again while reinstalling to verify the fix and had to `env -u XDG_DATA_HOME -u COSMO_CONFIG` to reach the real one |
| 81 | `notify.watch._should_notify` gains a `_NEVER_NOTIFY_TYPES` set (currently just `TASK_HEARTBEAT`), checked before `_ALWAYS_NOTIFY_TYPES` and before the `min_severity` comparison -- the symmetric counterpart to the existing "always notify regardless of severity" override, this time "never notify regardless of severity" | Not named in the spec | Post-v0.1.0 | Requested directly: the user's real `~/.config/cosmo/config.toml` sets `min_severity = "info"` (deviation 79's own explicit user decision, so `task.completed`'s always-notify promotion wasn't needed for anything else at `info`), which meant every `task.heartbeat` row -- always `severity=info`, emitted on every `progress.poll_interval_seconds` tick per `task.progress.ProgressWatcher` -- was also clearing that same threshold and reaching Telegram on every poll. New test `test_heartbeat_is_never_forwarded_even_at_min_severity_info` in `test_notify_watch.py` exercises exactly that config (`min_severity=Severity.INFO`), the one setting under which the old code would have forwarded it |

## Phase 10 — acceptance run — Complete

**The acceptance run against `/home/dev/delta/cosmo-tests/todo-frontend-app`
reached full completion this session: all six queued tasks (`scaffold-app`,
`todo-data-model`, `use-local-storage-hook`, `use-todos-hook`, `todo-ui`,
`todo-e2e`) reached `done`.** This closes out the "in progress" state the
rest of this section originally described. The earlier `bdf4ab101aee...`
run's silent-SIGTERM incident (kept below, unedited, as real historical
data about this host) predates every fix in this section and this table's
deviations 68-70 -- it was never explained, but it also never recurred
during this session's own several real `cosmo run`/`cosmo run --task`
invocations, none of which involved a multi-hour in-process `sleep()` for
a quota pause (none of this session's real attempts hit a confirmed 5h
exhaustion).

Getting there required three more real, previously-unknown bugs, on top of
the nine found earlier in this phase (deviations 59-67) -- see deviations
68-70 for full detail:

- **68**: the live `cosmo run` terminal never actually showed
  `TASK_STATE_CHANGED` events despite the v5 plan's own explicit intent to
  -- the exact gap the user reported live, watching this session's first
  `cosmo run` invocation show harness chatter but no task id, state, or
  timestamp anywhere.
- **69**: `task.machine._do_finishing`'s `openspec archive` step, and
  separately `cli.main.init`'s entire bootstrap (`openspec/`, `docs/`,
  `.agent/<harness>/`, root symlinks), both left `repo_path` permanently
  dirty by never committing their own output -- the first blocked
  `todo-data-model`'s `MERGING` immediately after `scaffold-app` finished;
  the second, reproduced fresh in a scratch repo, blocks the very *first*
  task ever queued against a newly `cosmo init`-ed repo. Both fixed. This
  is very likely the real, previously-unexplained mechanism behind last
  session's finding #7 (`.agent/claude/CLAUDE.md` found dirty) -- a
  background investigation this session additionally traced one real
  recurrence of that specific instance to `run_init`'s unconditional
  re-sync on an already-registered repo after the template moved on.
  Confirmed clean across 7 more real task completions total this session
  (5 in the main acceptance run, 2 in scratch-repo verification) after the
  fix landed.
- **70**: `cosmo run --task` never acquired the v5 process lock or ran
  startup crash reconciliation at all -- a real `kill -9` left a task
  permanently stuck outside `queued` with no recovery path except
  `queue retry`'s full fresh-start. Fixed, and fixing it surfaced a further
  bug (reconciliation alone doesn't clean up the crashed attempt's actual
  git worktree/branch, causing the next attempt to collide) -- also fixed.
  Verified against two consecutive real `kill -9`s in a scratch repo.

Also validated for real this session, no code changes required going in:
`task.machine.run_task(resume_at=MERGING)` resuming a real blocked task
with zero new harness/gate calls (deviation 66, first real exercise);
`gate.docker_runner`'s `--user`/`HOME` fix (deviation 63) producing zero
root-owned files across every real gate run this session, not just each
image in isolation; `cli.main.queue_retry`'s repeat-block guard (deviation
65) actually refusing a real retry and `--force` actually overriding it.

The rest of this section (the earlier `bdf4ab101aee...` run's own findings)
is kept as-is below for its real, still-relevant data about this host.

**A real, confirmed quota exhaustion happened and was handled correctly by
the run-level state machine.** `scaffold-app` reached real `IMPLEMENTING`,
its harness call hit `error_max_turns` (see deviation 49's root cause),
`run_task` graceful-requeued it (`implementing -> failed_retry -> queued`,
`attempt_number` untouched -- not a code failure), and the *next* thing the
run loop saw was a genuine `rate_limit_info` signal confirming 5h exhaustion
(not the deviation-43 false positive this session's earlier work fixed).
`run.loop._handle_quota_pause_or_stop` did exactly what §6.5/the code
comments say: wrote `run.paused` (`reason: quota_exhausted_5h`, `confirmed:
true`, `resume_delay_seconds` ~8716s), then called a real, uninterrupted
`sleep()` in-process intending to wake itself and continue the same loop --
this is a genuine in-process self-resume, not "no resume path exists" (an
earlier read of this same session got that wrong before checking the code).

**The process died silently during that sleep, before ever resuming.** By
the time anyone next looked (~6h after the pause), the `cosmo run` process
was gone from `ps -ef` on the same host, in the same still-open terminal,
with the same terminal reporting a bare `Terminated` (SIGTERM, not
`Killed`/SIGKILL, not a crash) -- no reboot (`uptime -s` predates the run
start), no OOM in `dmesg`, no docker container, nothing holding `cosmo.db`
open. `cosmo`'s own source has no code path that signals its own PID --
`os.killpg`/`SIGTERM`/`SIGKILL` only ever target a harness subprocess's
*group*, confirmed by reading `proc/managed.py` end to end. The only
anomaly in `journalctl` in that window is a `Clock change detected.
Flushing caches.` burst plus three `mini_init: drop_caches` events between
03:37-03:55Z -- WSL2's own signature for the VM being asked to shed memory
under host pressure -- but WSL2 doesn't surface the actual kill decision to
the Linux side, so this is the best available lead, not a proven cause.
**Open item, not yet fixed**: an unattended run that can be silently
SIGTERM'd by the host with zero record of why, and no supervisor to notice
or restart it, defeats the entire "survives overnight, unattended" premise
Phase 9's systemd unit exists for -- this is exactly the gap installing and
using `deploy/cosmo-run.service` for real (still not done on this host)
would close, since `Restart=on-failure` catches a signal-based kill even
though it deliberately does *not* catch a clean `PAUSED`/`STOPPED` exit
(see `deploy/README.md`). Worth deliberately testing (`systemctl --user`,
no sudo needed per the Phase 9 verification) before trusting an unattended
run on this host again.

**`use-local-storage-hook`'s old `blocked` (`reason: cost`) state was
resolved this session** -- `cosmo queue retry` once `scaffold-app` and its
dependents were done, then a real run drove it (and every task queued
behind it) to `done`. No longer open.

### Open items -- updated this session, several closed for real

Closed this session, with real evidence (not just code review) -- see
deviations 68-70 above for detail:

- ~~Install and actually exercise `deploy/cosmo-run.service`~~ -- done as
  a real `systemctl --user` install (no system-wide `sudo` available in
  this session; see deploy/README.md's own install instructions for the
  system-wide path a future session with `sudo` should still verify).
  `Type=notify`'s `sd_notify` STATUS= string was visible in `systemctl
  status` for real, and a real bug in the shipped unit files
  (`StartLimitIntervalSec`/`StartLimitBurst` needed `[Unit]`, not
  `[Service]` -- systemd 259 silently rejected them) was found and fixed.
- ~~`use-local-storage-hook`'s cost-blocked state~~ -- retried and
  completed for real.
- ~~A real `kill -9` of a `cosmo run` process, confirming the *next*
  `cosmo run` picks the task back up~~ -- done, for both the queue-driving
  path (already had the machinery) and `cosmo run --task` (didn't, until
  deviation 70).
- ~~`cli.main.queue_retry`'s repeat-block guard actually refusing a real
  retry~~ -- done, against a real blocked task with 3 seeded
  `task_failures` rows replaying `scaffold-app`'s own real historical
  `error_max_turns` pattern (no task in this session's own real queue
  happened to repeat-block 3 times on its own to exercise it more
  organically).

Still open:

- **A real system-wide (`sudo cp .../etc/systemd/system/`) install** of
  both units, as `deploy/README.md` actually documents for production --
  this session's install was user-scope only, for lack of `sudo` access.
- **`REVIEWING` and `VALIDATING` timeout data exists now but hasn't been
  formally retuned (Open Item 2, §3.3)**: 8 more real `REVIEWING` passes
  this session ran 33s-161s (`reviewing_wall=900s` -- comfortable margin);
  `todo-e2e`'s two failing real `VALIDATING` attempts each took ~24-25
  real minutes (`validating_wall=2700s` -- over half the budget on a
  slow-failing e2e suite, the first real data point suggesting this value
  is worth revisiting, not just guessed at). Retuning itself -- picking
  new numbers -- is still a decision nobody has made, not a fact nobody
  has gathered.
- **A real Telegram bot token/chat id actually receiving a message end to
  end** via `cosmo notify watch` -- blocked on not having credentials to
  test with this session; `cosmo-notify.service`'s *refusal* to start
  without them was confirmed for real instead (`notify.enabled is false
  -- nothing to watch`, exactly as documented).
  `TelegramSink.send`'s real HTTP call itself remains unverified.
  `cosmo notify watch`'s `stale_after_seconds=1800` default and the
  severity/allowlist rules are likewise still unconfirmed against a real
  multi-hour run with a real sink attached.
  Whether `deploy/cosmo-notify.service` survives long-term alongside
  `deploy/cosmo-run.service` also remains unconfirmed beyond this
  session's brief real check (it does start correctly; long-run
  coexistence wasn't tested).
- **A real `cosmo run resume` against a real circuit-breaker-tripped or
  quota-paused run** -- no task in this session's own real queue tripped
  either condition, so this specific command was never exercised for
  real (as opposed to `reconcile_interrupted_tasks`, which was).
- **A real `bypass_5h_with_credits=true` run** against an account whose
  usage credits are actually covering calls past a confirmed 5-hour
  window -- needs a real, deliberate quota-exhaustion window to test
  against, not something to force casually (real spend, real waiting).

## v5 improvements plan — Implemented

[v5-improvements-plan.md](v5-improvements-plan.md)'s parts 1-4, 6, and 7 are
implemented; part 5's Class 1 (the `failure_signature` taxonomy) is
implemented too. Part 5's Class 2 (whether `permissions.deny` actually
gates `ScheduleWakeup`/`ToolSearch`/`TaskOutput`) was already resolved and
shipped *before* this session, as deviation 49 above -- nothing left open
there. This session's own new deviations are 50-57 in the cumulative table.

### What exists

- **Part 1 -- crash recovery.** `run.recovery.reconcile_interrupted_tasks`:
  every `task_queue` row not `queued`/`done`/`blocked` at startup is
  requeued (never via `queue_retry` -- `attempt_count` is left untouched,
  since a crash must not consume the code-level retry budget), its
  `worktree_path` cleared (the directory is already gone by the time this
  runs -- see the ordering deviation below), a `task.interrupted` event
  emitted, and an `environment_error` `task_failures` row recorded. Any
  *other* `run_state` row still `running` is transitioned to `stopped`/
  `crashed` -- the run currently being started/resumed is excluded from
  this scan (deviation 58; its own row is legitimately `running` by the
  time this runs). `cosmo report` surfaces recovered tasks directly:
  "recovered from an interrupted run: N task(s) (ids...)" whenever any
  `task.interrupted` events exist for the run.
  `run.recovery.acquire_run_lock`/`RunLock`: a pidfile
  (`paths.data_dir/cosmo-run.lock`) written at the top of every
  `run_queue()` call (both a fresh run and `cosmo run resume`), holding the
  PID; a live PID refuses a second `cosmo run` with `RunLockHeldError`
  (surfaced by the CLI as a clean error, not a traceback); a dead PID is
  reclaimed automatically. `run.loop.run_queue` is now a thin wrapper that
  acquires the lock, calls the renamed `_run_queue_locked` (the original
  function body, otherwise untouched), and releases the lock in `finally`
  -- see deviation 53 for why it's a wrapper and not an in-place
  `try/finally`.
- **Part 2 -- `cosmo run resume [run_id]`.** `run_queue` gained an optional
  `resume_run_id` parameter: when given, it reuses that `run_id` (no
  `run_create`, `RUN_RESUMED` instead of `RUN_STARTED`) and otherwise
  proceeds exactly like a fresh run -- cost accounting picks back up for
  free (`run_cost`/`task_cost` are already keyed by `run_id`), and the
  wall-clock budget is deliberately fresh from the moment of resume (decision
  2), not an accounting of time spent paused. The CLI command resolves an
  omitted `run_id` to the most recently *updated* `paused` run
  (`store.reader.latest_paused_run_id`), renders the same context
  `cosmo report` does (`cli.main._render_run_report`, factored out of
  `report_cmd` so both share it), and prompts for confirmation unless
  `--yes`.
- **Part 3 -- notifications.** New `cosmo.notify` package: a `Sink`
  protocol (`send(event: Event) -> None`), `notify.telegram.TelegramSink`
  (stdlib `urllib`, no new dependency, best-effort on any `OSError`/
  `URLError`), and `notify.watch.run_watch_loop`/`watch_once` -- a small,
  independently-testable poll loop (`store.reader.list_events_after`,
  keyed on `rowid`, not the per-run-scoped `sequence` -- deviation 56) that
  forwards `WARNING`+ events (plus `RUN_SUMMARY`/`RUN_STOPPED` regardless
  of severity) to the sink, and raises its own synthetic staleness alert
  (`event_type="watch.stale"`, deviation 57) after
  `[notify].stale_after_seconds` of silence, exactly once per silent
  period. New `cosmo notify watch` CLI command (refuses to start if
  `notify.enabled` is false or Telegram credentials are missing) and
  `deploy/cosmo-notify.service`, documented in `deploy/README.md` alongside
  `cosmo-run.service`.
- **Part 4 -- `--follow`.** `cosmo events tail --follow` polls past the
  last-seen `rowid` at `progress.poll_interval_seconds` and prints new rows
  `tail -f`-style, filtered client-side by the same `--run`/`--task`/
  `--severity`/`--type` flags; `cosmo report --follow` re-renders until the
  run reaches `stopped`. Both exit cleanly on `KeyboardInterrupt`.
- **Part 5, Class 1 only.** `store.failure_signature.
  classify_failure_signature`: deterministic substring matching against
  `error_detail` (`missing_lockfile`, `node_engine_mismatch`,
  `enoent_node_modules`; unmatched stays `None`), computed automatically
  inside `StoreWriter.record_task_failure` (migration 8's new nullable
  `task_failures.failure_signature` column) -- every existing call site
  gets it for free, no call-site changes needed. Lives under `cosmo.store`,
  not `cosmo.task`, to avoid a real import cycle (deviation 51).
- **Part 6 -- coarse live-terminal events.** `EventEmitter` gained an
  optional `on_emit: Callable[[Event], None]` hook, called after the DB
  insert succeeds. `cli.main._print_emit` (wired into `cosmo run`,
  `cosmo run --task`, and `cosmo run resume`'s `EventEmitter` construction)
  prints `WARNING`+ events plus a small lifecycle-`INFO` allowlist
  (`RUN_STARTED`/`RUN_RESUMED`/`RUN_SUMMARY`), styled bold/severity-colored
  to stay visually distinct from `_print_activity`'s dim per-tool-call
  chatter, and renders `RUN_PAUSED`'s `resume_delay_seconds` as a computed
  wall-clock ETA rather than a raw float.
- **Part 7 -- quota bypass.** New `QuotaConfig.bypass_5h_with_credits`
  (default `False`); `quota.decide()` returns a `QuotaDecision` with
  `status=RunStatus.RUNNING, bypassed=True` for a confirmed `five_hour`
  signal when set (`weekly` is untouched, per the plan's own decision) --
  new legal value for `QuotaDecision.status`, deviation 54.
  `CosmoConfig`'s own cross-section validator refuses to load when the
  bypass is on but `cost.max_cost_per_run_usd` is left at `0.0` (decision
  7). `run.loop._handle_quota_pause_or_stop` emits `EventType.
  QUOTA_BYPASSED` (`WARNING`) instead of pausing when `bypassed` is set.

### Real invocations this session (not just unit tests)

Same discipline as every prior phase -- fake/unit coverage first, then a
real check:

- **The process lock, for real.** One process held `acquire_run_lock`
  against a scratch `data_dir` while a concurrent `cosmo run` was invoked
  against the same repo: the second one was refused with a clear
  `RunLockHeldError` message naming the real holding PID, not a traceback;
  once the holder released, the identical invocation succeeded.
- **Crash recovery, for real, end to end through the CLI.** A task was
  seeded directly into `task_queue` at `implementing` (via a real
  `StoreWriter`, not the test double), alongside a `run_state` row left
  `running` (simulating a killed prior process). The *next* real
  `cosmo run --repo ...` invocation: emitted `task.interrupted`
  (`previous_status: implementing`), transitioned the task
  `implementing -> queued` (visible in `task_transitions`/`cosmo events
  tail`), marked the seeded run `stopped`/`crashed`, and printed both
  events live via the new `on_emit` hook (part 6) -- confirming parts 1 and
  6 compose correctly, not just in isolation.
- **Migrations, for real.** A fresh `cosmo init` + `cosmo run` against a
  scratch repo applies migrations 1-8 cleanly (`schema_migrations` reaches
  version 8) with no error, on top of the existing dev environment's
  already-migrated database from prior phases.
- **Not done for real, deliberately deferred** (matches the plan's own
  "Verification" section, and this codebase's established posture of not
  faking the one thing that can't be faked): a real Telegram bot
  token/chat id receiving a real `TelegramSink.send` message (`notify.
  telegram.format_event`'s own message-shaping is unit-tested; the actual
  HTTP call is not); a real `kill -9` of a `cosmo run` process mid-`
  IMPLEMENTING`/`VALIDATING` against a real target repo (the seeded-DB-row
  check above exercises the same reconciliation code path but isn't a real
  process kill); a real `bypass_5h_with_credits=true` run against an
  account whose usage credits are actually covering calls past a real
  5-hour window.

### Two real bugs found and fixed after the first implementation pass

Both found by hand running the crash-recovery smoke test above, neither by
inspection alone -- and both are the kind of thing a fake/unit-only test
suite would never have caught (the second one *was* actually caught by the
first pass's own new unit tests, once written; the first was not caught by
any test until one was added specifically to check the row count):

- **A pre-existing double `RUN_STOPPED` emission, now fixed.** `run.loop.
  run_queue`'s `disk_low` and DAG-cycle-at-startup abort branches each used
  to emit their own detailed `RUN_STOPPED` event (severity `critical`, with
  a `detail`/`error` field) *and* fall through to the generic post-loop
  `RUN_STOPPED` emission every non-`PAUSED` `final_status` gets (severity
  `info`) -- two rows in `events` for one stop. Fixed by capturing the
  richer severity/detail in two loop-local variables (`stop_severity`,
  `stop_extra_payload`) at the abort site instead of emitting there, and
  merging them into the single post-loop emission. Pre-dated this
  session's v5 work (`git diff` confirmed neither branch had been touched
  before this fix), but fixed as part of it since it directly affects how
  `cosmo events tail`/a Telegram sink presents a `disk_low`/DAG-cycle stop.
  New test: `test_run_disk_check.py`'s existing disk-abort test now asserts
  `len(events) == 1`; `test_run_loop.py` gained a dedicated DAG-cycle-abort
  test asserting the same.
- **A new bug this session's own reconciliation ordering fix (deviation
  52) introduced, caught before it shipped.** Because `reconcile_
  interrupted_tasks` now necessarily runs *after* the new/resumed run's own
  `run_state` row is transitioned to `running` (the FK-ordering fix), its
  own "any `running` row is a crash" scan swept up that brand-new row too
  -- every fresh `cosmo run` was marking its own run `stopped`/`crashed` a
  few lines after starting it, alongside its real, correct stop event
  later. Fixed with a one-line guard: `reconcile_interrupted_tasks` skips
  `run.run_id == run_id` (the run it's reconciling *for*) in that scan.
  Caught by two new real-invocation-shaped tests written to verify the
  `RUN_STOPPED` fix above (both asserted `len(events) == 1` and got `2`),
  not by inspection -- recorded as deviation 58.
- **`cosmo report` now surfaces recovered tasks.** The plan's own prose
  named this as something worth adding; implemented directly rather than
  left as an open item -- `_render_run_report` prints "recovered from an
  interrupted run: N task(s) (ids...)" whenever any `task.interrupted`
  events exist for the run, sourced from the same `list_events` query
  every other summary line already uses.

New deviations from this fix-up pass: 58 (the reconcile self-crash guard).
The `RUN_STOPPED` double-emission fix and the `cosmo report` line are
corrections/completions of this session's own v5 work, not separate spec
deviations.

### Things that will matter later

- Part 5's Class 2 research (the session-management-tool audit beyond
  `ScheduleWakeup`/`ToolSearch`/`TaskOutput`) is still exactly as open as
  the plan itself left it -- deviation 49 closed the one diagnosed
  instance, not a general audit of every tool with the same shape.
- Every remaining open item from this work is a validation/acceptance
  task, not an implementation gap -- see the new bullets added to "Open
  items for whoever finishes Phase 10" above (Telegram send, real
  `kill -9`, real `cosmo run resume`, real credits bypass, `cosmo
  notify watch`'s tuning, `cosmo-notify.service` alongside `cosmo-run.
  service`).
