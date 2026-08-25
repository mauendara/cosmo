# Handoff — continue at Phase 3

You are picking up Cosmo mid-build. Phases 0, 1, and 2 are complete. Your job
is Phase 3: the harness abstraction proper and the Claude Code CLI adapter —
`propose`/`implement`/`validate`/`get_progress`/`cancel`, `FakeHarnessAdapter`,
`ClaudeCodeAdapter`, and the `stream-json` reader.

## Read these first, in this order

| Document | What it is | How to treat it |
|---|---|---|
| [v3-cosmo-autonomous-agent-spec.md](v3-cosmo-autonomous-agent-spec.md) | The authoritative specification | **Source of truth.** v1 and v2 are superseded — read them only for history |
| [v3-implementation-plan.md](v3-implementation-plan.md) | 11-phase build plan | The map. Phase 3 is your scope (§2.1-2.3, §4, §7.2) |
| [v3-implementation-state.md](v3-implementation-state.md) | What actually exists, plus decisions and gotchas | Read the "Things that will matter later" section under Phase 2 before writing code |

`v1-*` and `v2-*` in this folder are earlier spec drafts. v3 is a superset of
both. Do not implement from them.

**Do not edit the plan document.** It is the agreed scope. Record what you
build, and any decision you make along the way, in `v3-implementation-state.md`.

## Where things are

```
/home/dev/delta/cosmo/          # working branch: develop
├── docs/                       # the four documents above
├── src/cosmo/
│   ├── checks.py                # CheckResult / CheckStatus
│   ├── config/                  # typed model, defaults.toml, three-layer loader
│   ├── doctor.py                 # core preflight checks
│   ├── harness/                  # base ABC, registry, claude adapter (stub) — Phase 3 fills this in
│   ├── store/                    # SQLite schema, StoreWriter, reader queries (Phase 1)
│   ├── events/                   # envelope + EventEmitter, transactional sequence (Phase 1)
│   ├── proc/                     # ManagedProcess, timers, orphan sweep, reap (Phase 2)
│   ├── cli/main.py               # `cosmo` command: config, harness, doctor, queue, events, project
│   └── {git,gate,task,run,knowledge}/   # EMPTY — later phases
├── tests/                       # 91 passing
└── check.sh                     # ruff + format + mypy --strict + pytest
```

## Get oriented (2 minutes)

```bash
cd /home/dev/delta/cosmo
git log --oneline           # Phase 2 should be committed at HEAD
git branch --show-current   # should say develop
./check.sh                  # must be green before you change anything
cosmo doctor                # core checks + harness checks in two tables
```

If `cosmo` is not on PATH, run `uv tool install --editable .` from the repo root.
Editable means your source edits are live — no rebuild between changes.

**Known, pre-existing environment noise on this host** (not something Phase 3
broke, don't chase it): `cosmo doctor` may show `disk space: FAIL` (this WSL2
box runs close to the 10 GB floor) and `leaked gate containers: WARN, docker
ps failed` (Docker Desktop's WSL2 integration isn't enabled in this session,
so `docker` is on PATH but every invocation fails). Both are reported
accurately by the doctor checks; neither blocks anything Phase 3 needs to do,
since Phase 3's adapter work doesn't touch Docker at all.

Try the process-supervision surface Phase 2 built before touching anything —
Phase 3's adapter will lean on it directly:

```python
from pathlib import Path
from cosmo.proc import ManagedProcess

mp = ManagedProcess(["echo", "hello"], raw_log_path=Path("/tmp/probe.log"))
mp.wait(timeout=2.0)
print(Path("/tmp/probe.log").read_text())
```

## Conventions this codebase follows

- **Python 3.12+, `uv`-managed.** Add dependencies with `uv add`, not by hand.
- **`mypy --strict` passes.** Annotate everything, including test helpers.
- **Comments explain *why*, never *what*.** Existing comments cite the spec
  section that forced the decision. Match that.
- **Config over constants.** Every tunable goes in `config/model.py` and
  `config/defaults.toml`, annotated with its spec section. No magic numbers.
- **Validators catch what would fail silently.** See the existing timeout and
  playwright-tag validators for the pattern: reject at startup what would
  otherwise misbehave at 3am.
- **Tests isolate from the developer's environment.** Anything touching config
  must set `COSMO_CONFIG` and `XDG_DATA_HOME` to temp paths — see the autouse
  fixture in `tests/test_cli.py`.
- **Fake the external process, test the mechanics.** Phase 2's tests never
  invoked a real `docker`; they used a recording shell-script fake
  (`tests/fixtures/fake_docker.sh`) because this sandbox's own `docker`
  doesn't actually work, and because that's the right posture for a unit
  test regardless. Phase 3's `FakeHarnessAdapter` is the same idea applied to
  `claude -p` — build it as the thing every later phase's tests target, and
  keep the real CLI invocation to the one integration exit criterion.
- **Boundary tests are load-bearing, not optional.** `test_harness_boundary.py`
  keeps harness-specific tokens out of core; `test_store_boundary.py` keeps
  `connect_writer` from leaking outside `store/writer.py` and
  `store/migrations.py`. Phase 3 is the phase most likely to trip the harness
  boundary test on purpose — `harness/claude.py` (soon `harness/claude/`) and
  `harness/registry.py` are the only modules allowed to name `"claude"`,
  `stream-json`, `--permission-mode`, `max-turns`, or
  `dangerously-skip-permissions`. If you genuinely need a new harness-aware
  module (e.g. `harness/claude/stream.py`), add it to
  `ALLOWED_HARNESS_AWARE`/`ALLOWED_WRITER_IMPORTERS` deliberately — don't
  weaken the assertion.
- **Run `./check.sh` before committing.** All four must pass.
- **When something fails, check with a real invocation before trusting a
  unit test's green.** Phase 2's two worst bugs (a deadlock in the kill/reap
  loop, and a leaked-container false-positive) were both invisible to the
  unit tests as first written and only surfaced by manually running the
  code. `cosmo harness probe` (this phase's own exit criterion) is exactly
  that kind of check — don't skip it.

## Phase 3 scope

Spec §2.1-2.3 (harness abstraction, adapter interface, Claude Code CLI
adapter), §4 (stream-json, progress/liveness), §7.2 (quota detection).
Summary from the plan:

1. **`HarnessAdapter` base**: `propose`, `implement(retry_context=None)`,
   `validate`, `get_progress`, `cancel`; the uniform result object (§2.2)
   including `session_id` and `total_cost_usd`; the six declared capability
   flags. Note the two Phase 0 deviations already recorded: `preflight()` was
   added to this interface, and `validate()` is documented as **not**
   actually on it (§2.2 contradicts itself; validation is Phase 6's
   `cosmo.gate`, bypassing the harness entirely). Don't re-add `validate()`
   without resolving that contradiction first.
2. **`FakeHarnessAdapter`** — scriptable outcomes (success, code failure,
   environment failure, hang, rate-limit, cost overrun). Every state-machine
   test from Phase 7 onward targets this, never the real CLI.
3. **`ClaudeCodeAdapter`**:
   - `claude -p --output-format stream-json --verbose`, always with
     `--max-turns` and `--permission-mode` from config.
   - Child environment **explicitly scrubs** `ANTHROPIC_API_KEY` rather than
     assuming its absence (§2.3) — `harness/claude.py` already has a
     `BILLING_ENV_VAR` preflight check to build on.
   - Sets `CLAUDE_CODE_ENABLE_TELEMETRY=1` with content logging off (§9.4).
   - Never emits `--dangerously-skip-permissions`; a unit test must assert
     the flag can never appear in a constructed argv.
   - Branches on **zero vs non-zero exit only** (§2.3) — a test asserts no
     specific non-zero value carries meaning.
   - Headless prompt that the permission layer cannot resolve → fail as
     `environment_error`, never hang.
   - Launch the child process through Phase 2's `ManagedProcess`
     (`cosmo.proc`), not a bare `subprocess.Popen` — that's the whole reason
     Phase 2 came first. `cancel()` on this adapter should route to
     `ManagedProcess.cancel()` / `cancel_and_reap`.
4. **Stream reader**, inside `harness/claude/` (a package now, per the Phase 0
   note that this becomes `adapter.py` + `stream.py`), not in core: NDJSON
   line reader tolerant of partial lines and non-JSON noise, classifying:
   heartbeat (any event), tool-call records, `system/api_retry` → rate-limit
   state + ETA (§7.2 **primary** source), terminal `result` → `total_cost_usd`,
   `duration_ms`, `num_turns`, `session_id`.
5. **Prose parsing is prohibited** as a signal (§4). A test asserts no
   classification path greps human-readable text.

### Exit criteria

- `cosmo harness probe --prompt "print hello"` invokes the real CLI, streams
  events, and prints the parsed result object with a `session_id`.
  *(Integration; consumes a small amount of quota — run it, don't skip it;
  see the "check with a real invocation" convention above.)*
- Recorded NDJSON fixtures (normal run, `api_retry`, truncated stream,
  malformed line) replay through the reader in unit tests.

## Things to know before you start

**`ManagedProcess` (Phase 2) is what `ClaudeCodeAdapter` launches `claude -p`
through.** It already gives you: `start_new_session=True` process-group
semantics, non-blocking stdout/stderr drain to a raw log file via
`os.read(fd, ...)` (not `pipe.read()` — that blocks until it fills its
buffer, which silently defeated "non-blocking" until a test caught it), and a
`cancel(grace_s=...)` that correctly waits for the *whole process group* to
be gone, not just the direct child. The stream-json reader you're building
in Phase 3 needs to consume the same stdout stream `ManagedProcess` is
draining — decide now whether that's a second consumer of the raw log file,
a tee at the `ManagedProcess` level, or an extension to `ManagedProcess`
itself; `cosmo.proc` has no opinion yet and this is a real design decision,
not a detail.

**`cancel_and_reap` (`cosmo.proc.reap`) already does the full spec 2.4
kill→sweep→emit sequence and knows the circuit-breaker weight.** Your
adapter's `cancel()` should call into this rather than reimplementing any
part of it. It needs `run_id`, `task_id`, `worktree_path`, `config`, and an
`EventEmitter` — Phase 3 doesn't have worktrees yet (Phase 5), so decide how
`ClaudeCodeAdapter.cancel()` gets a worktree path in the meantime (a
constructor argument is probably right; don't invent worktree lifecycle
early).

**Emit through `EventEmitter`, write through `StoreWriter`.** Same rule as
every prior phase. If the stream reader needs to persist anything from a
background thread, that goes through `StoreWriter.submit()`/`drain()`, not a
second write connection — `tests/test_store_boundary.py` enforces this.

**The harness boundary test is stricter than it looks.** `"claude"` as a bare
string literal, `stream-json`, `--permission-mode`, `max-turns`, and
`dangerously-skip-permissions` may only appear in `harness/claude.py` (soon
`harness/claude/*.py`), `harness/registry.py`, and `config/defaults.toml`.
Writing a quick CLI probe command or a debug print anywhere else in
`cli/main.py` that happens to mention `stream-json` will fail
`test_harness_boundary.py`, correctly.

**Quota detection has a strict priority order (§7.2) — don't invert it.**
`system/api_retry` from the stream is primary; the terminal `result` object's
error subtype is second; a wall-clock heuristic (repeated immediate failures
with no tool calls) is last-resort, `severity=warning`, and **must never be
reported as a confirmed quota state**. The actual pause/resume logic is
Phase 8's job — Phase 3 only needs to classify and surface what it observed.

**Nothing before Phase 8 should implement circuit-breaker trip logic or run
scheduling.** Phase 3 emits events and returns results; it does not decide
what happens next.

## When you finish

1. `./check.sh` green.
2. Update `v3-implementation-state.md`: mark Phase 3 complete, list what
   exists, record every decision made and anything a future session would
   otherwise rediscover. Append any new spec deviation to the cumulative
   table at the bottom.
3. Commit to `develop` with a message explaining *why*, in the style of the
   Phase 0/1/2 commits.
4. Rewrite this handoff for Phase 4 (template system and `cosmo init`, §10 in
   full, §2.5's guardrail hooks, Open Item 4) — or delete it if the next
   session continues immediately.

Phase 4 is next: the `.claude/settings.json` deny rules and `PreToolUse`
hooks that are the actual security boundary (§2.5), the project template
system, and `cosmo init`. It depends on Phase 3's adapter existing so the
hooks have something real to gate — but the hooks themselves are a harder
requirement than they might look: nothing should run unattended against a
real repo before they exist.
