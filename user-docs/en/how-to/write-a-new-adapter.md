# How to write a harness adapter

A **harness** is the coding agent Cosmo drives — the thing that actually
proposes and writes code. Claude Code is the only adapter implemented today.
This document specifies the interface precisely enough that you can add
another (Codex CLI, OpenCode, an in-house agent) without asking anyone a
question.

Contributions of new adapters are explicitly welcome. See
[CONTRIBUTING.md](../../../CONTRIBUTING.md) for the PR conventions.

## What Cosmo guarantees you

- Core orchestration code **never branches on which harness is configured**.
  A test enforces that only your adapter's own module may name your binary,
  its flags, or its environment variables.
- Your adapter is never asked to validate anything. Validation bypasses the
  harness entirely — it's direct Docker invocation, so your agent cannot
  influence its own verdict.
- Your adapter is never asked to resolve a merge conflict.
- Cosmo owns timeouts, retries, the state machine, cost accounting, and every
  decision about what happens after a call returns.

Your job is narrow: **invoke the agent, and report what happened uniformly.**

## The three files you write

```
src/cosmo/harness/mytool/
  __init__.py     # export the adapter class
  adapter.py      # the implementation
```

plus one line registering it:

```python
# src/cosmo/harness/registry.py
from cosmo.harness.mytool import MyToolAdapter

_REGISTRY: dict[str, type[HarnessAdapter]] = {
    ClaudeCodeAdapter.name: ClaudeCodeAdapter,
    FakeHarnessAdapter.name: FakeHarnessAdapter,
    MyToolAdapter.name: MyToolAdapter,       # ← add this
}
```

Users then select it with `harness.name = "mytool"` in config, `--harness
mytool` on a command, or per-project via `cosmo init --harness mytool`.

## The harness template

The Python adapter is only half the job. The other half lives outside
`src/`, in a directory Cosmo treats as data, not code:

```
templates/harness/mytool/
  CLAUDE.md          # or your tool's equivalent operating policy
  settings.json       # permission mode, allow-listed tools, etc.
  agents/              # the implementer/reviewer agent definitions
  skills/              # OpenSpec workflow and spec-enrichment skills
  hooks/               # PreToolUse/PostToolUse guardrail scripts
```

Model it on `templates/harness/claude/` — that's the reference layout, not a
Claude-specific one. Whatever your tool's mechanism is for operating policy,
sub-agents, skills, and tool-call hooks goes here.

This directory is what `cosmo init` and every per-task worktree creation
sync into the target repo's `.agent/mytool/` (which `.claude`-equivalent
symlinks like `.claude/agents` and `.claude/skills` point into). The sync is
**wholesale, not a merge**: the destination is deleted and recreated from
this template on every sync, for every harness adapter alike. Nothing a user
hand-edits or hand-installs into that tree in their target repo survives the
next task. If your template is meant to package a capability (an OpenSpec
skill, a custom agent) for users, ship it here — don't tell users to add it
to the target repo themselves, because Cosmo will remove it.

Keep the operating policy and guardrails here strictly separate from the
adapter code in `src/cosmo/harness/mytool/`: the template is what the agent
reads and is told to follow; the adapter is what invokes the agent and
parses its output. Conflating them — for example, hardcoding a policy string
into `adapter.py` instead of `CLAUDE.md` — makes the policy invisible to
`cosmo doctor` and impossible for a user to audit without reading Python.

## The data types

### `HarnessCapabilities`

Class-level, declared once. Each flag names the fallback Cosmo takes when
it's `False` — so a conservative declaration is always safe, it just gets you
a weaker guarantee.

```python
@dataclass(frozen=True, slots=True)
class HarnessCapabilities:
    reports_native_progress: bool    # False -> Cosmo watches the change's tasks.md
    supports_retry_context: bool     # False -> Cosmo composes a synthetic retry prompt
    has_internal_timeout: bool       # False -> Cosmo imposes an external timeout
    reports_native_cost: bool        # False -> estimate from tokens, or disable cost stop
    supports_gating: bool            # False -> post-hoc diff inspection only (weaker)
    supports_structured_stream: bool # False -> file-mtime liveness; the stall
                                     #          timeout is then the only guard
```

**Declare honestly.** `supports_gating=True` when your harness can't actually
deny a tool call before it executes means Cosmo believes it has prevention it
doesn't have. `cosmo harness list` prints this table so users can see what
they're getting.

If your harness has no way to block a file edit before it happens, declare
`supports_gating=False`. Cosmo falls back to the diff gate alone, which is
weaker but honest.

### `HarnessResult`

The uniform return type for every method. Nothing harness-specific leaks past
this boundary.

```python
@dataclass(frozen=True, slots=True)
class HarnessResult:
    success: bool                    # required
    output_summary: str              # required: short label, from structured output
    raw_log_path: Path | None        # required: where you wrote the raw session log
    files_changed: list[str]         # required (may be empty)
    duration_seconds: float          # required
    total_cost_usd: float | None     # required (None if unknown)
    exit_code: int | None            # required (None if not process-based)
    session_id: str | None           # required (None if your harness has no concept)
    quota_window: str | None = None      # "five_hour" | "weekly" | None
    quota_resets_at: str | None = None   # UTC ISO 8601, or None
    tool_call_count: int = 0
```

Notes that matter:

- **`success` is zero-versus-nonzero exit only** for a process-based harness.
  Never branch on a *specific* exit code — Cosmo's classifier assumes a
  binary signal.
- **`output_summary` must come from structured output, not prose.** Read the
  terminal result object's own `subtype`-style field. Parsing the model's
  free-text final message is prohibited: a model asked to say "success" will
  say it.
- **`quota_window`** is your primary rate-limit signal, and only meaningful
  on a *failed* call. A rate-limit notice seen mid-stream doesn't mean the
  call failed — many CLIs retry internally and succeed anyway. If your
  harness has no such signal, leave it `None`; Cosmo degrades to its
  secondary and tertiary detectors.
- **`tool_call_count`** feeds the wall-clock quota heuristic ("failed
  instantly with zero tool calls"). `0` is fine if you can't count them.

### `CheckResult`

```python
from cosmo.checks import CheckResult, check_executable, ok, warn, fail

ok("check name", "detail")     # informational
warn("check name", "detail")   # visible, non-blocking
fail("check name", "detail")   # blocking: cosmo doctor exits non-zero
```

## The interface

```python
from cosmo.harness.base import HarnessAdapter, HarnessCapabilities, HarnessResult

class MyToolAdapter(HarnessAdapter):
    name: ClassVar[str] = "mytool"
    capabilities: ClassVar[HarnessCapabilities] = HarnessCapabilities(...)
```

`name` and `capabilities` are class-level so the registry can report them
without instantiating or running anything.

The base `__init__` takes `(config: CosmoConfig, *, cwd: Path | None = None)`
and stores both. `cwd` is the task's worktree — the directory your agent must
run in, and the path the orphan sweep checks for surviving holders. Extend
the constructor if you need more (the Claude adapter takes `binary`,
`run_id` and `emitter`), but keep those keyword-only with defaults.

### `preflight() -> list[CheckResult]`

Environmental preconditions specific to your harness, for `cosmo doctor`.

**Must be cheap and side-effect free**: a `PATH` lookup at most. No
subprocesses beyond that, no network calls. `cosmo doctor` is run before
every deployment and inside scripts.

```python
def preflight(self) -> list[CheckResult]:
    results = [check_executable("mytool cli", self._binary, "running the harness")]
    if os.environ.get("MYTOOL_API_KEY"):
        results.append(fail("billing", "MYTOOL_API_KEY switches to metered billing"))
    mode = self.config.harness.permission_mode
    if mode in MY_FORBIDDEN_MODES:
        results.append(fail("permission mode", f"{mode!r} is never permitted"))
    return results
```

Check anything that would make an unattended run silently expensive or
silently unsafe. The Claude adapter fails hard on `ANTHROPIC_API_KEY` being
set for exactly that reason.

### `probe(prompt, *, on_activity=None) -> HarnessResult`

Run one raw prompt. Backs `cosmo harness probe`, the harness-agnostic
smoke test that doesn't presuppose an OpenSpec change on disk.

Implement it as a thin wrapper over your invocation helper. It's the first
thing anyone runs when your adapter doesn't work.

### `propose(spec_path, context, *, on_activity=None) -> HarnessResult`

Drive OpenSpec's propose workflow for the change at `spec_path`.

`context` is a `dict[str, Any]` carrying at least:

| Key | Meaning |
| --- | --- |
| `task_id` | The queue task id. Falls back to `spec_path.stem`. |
| `spec_id` | **The exact name the created OpenSpec change must have.** |

**`spec_id` is not advisory.** Everything downstream — the `openspec archive`
step at `FINISHING`, the worktree-reuse check on a retry — assumes the change
is named exactly this. Pin it in the prompt, emphatically:

```python
prompt = (
    f"Run OpenSpec's propose workflow for the change at {spec_path}. "
    f"Name the change exactly {spec_id!r} (`openspec new change {spec_id}`) -- "
    f"do not pick a different name, even a shorter or more natural-looking one. "
    f"Follow this repository's operating policy for how to invoke OpenSpec."
)
```

This wording is the result of a real failure: without it, a session
reasonably stripped a task file's `-task` suffix, and every later step
silently missed the real change.

### `implement(task_id, spec_path, retry_context=None, *, on_activity=None) -> HarnessResult`

Implement the change. On a retry, `retry_context` carries the previous
attempt's real failure detail:

```
Attempt 2 failed at stage e2e_tests (code_error): 1 test failed
  LoginPage › redirects logged-out users
  Expected URL to contain "/login", received "/dashboard"

Previous attempts:
- attempt 1 (unit_tests): 3 tests failed
```

Append it to the prompt, or feed it through your harness's native retry
mechanism if it has one — that's what `supports_retry_context` declares.

### `review(task_id, spec_path, base_branch, *, on_activity=None) -> HarnessResult`

The adversarial review. **This must be a genuinely fresh call**: no session
resumption, no retry context, no memory of the implementation. That's the
whole point — otherwise it's the same session grading its own work.

The verdict is **not** returned in `HarnessResult`. It has no
harness-agnostic slot there, and reading it from the session's prose is
prohibited. Instead, instruct the reviewer to write a JSON file into the
worktree at `.cosmo/review-result.json`:

```json
{"verdict": "approved"}
{"verdict": "rejected", "reason": "<why, specific enough to act on>"}
```

```python
from cosmo.task.review import REVIEW_RESULT_RELATIVE_PATH

prompt = (
    f"Review this branch's implementation for task {task_id}. Run "
    f"`git diff {base_branch}...HEAD` to see the diff and read the OpenSpec "
    f"change at {spec_path} for what was asked -- you have no memory of the "
    f"implementation session, judge only what these show. When done, write "
    f"your verdict to `{REVIEW_RESULT_RELATIVE_PATH.as_posix()}` as JSON: "
    f'`{{"verdict": "approved"}}` or '
    f'`{{"verdict": "rejected", "reason": "<why>"}}`.'
)
```

Import the constant rather than hardcoding the path. Cosmo reads the file
back after your call returns; a missing, unreadable, malformed or
verdict-less file is treated as an **environment problem with the review
call**, never as a rejection.

### `get_progress(task_id) -> tuple[int, int]`

Completed and total subtasks. **Never a precomputed percentage** — the total
isn't constant and progress can legitimately move backwards, so numerator and
denominator are stored separately.

If you declared `reports_native_progress=False`, raise `NotImplementedError`
with a message saying so. Cosmo watches the change's `tasks.md` instead and
never calls this.

### `cancel(task_id) -> None`

Terminate the run **and its entire process group**.

This is not optional detail. On POSIX, signaling only the direct child leaves
Maven, Node, Vite, `docker` clients and Playwright's Chromium re-parented to
init, where they keep running and holding ports and memory until the host
falls over — hours after the run that spawned them ended.

Use Cosmo's own `ManagedProcess`, which handles this correctly:

```python
from cosmo.proc import ManagedProcess, cancel_and_reap

process = ManagedProcess(
    argv,
    raw_log_path=raw_log_path,
    cwd=self.cwd,
    env=env,
    on_stdout_chunk=reader.feed,   # optional: streaming callback
)
exit_code = process.wait()
```

`ManagedProcess` starts the child with `start_new_session=True` (its own
process group and session), drains stdout and stderr on separate threads
into your raw log, and on cancel escalates SIGTERM → `timeouts.kill_grace`
seconds → SIGKILL against the **process group**. It doesn't declare victory
when `Popen.wait()` returns — that only reaps your direct child — but when
`killpg(pgid, 0)` raises `ProcessLookupError`, proving the whole group is
gone.

```python
def cancel(self, task_id: str) -> None:
    with self._lock:
        process = self._running.get(task_id)
    if process is None:
        return
    if self._emitter is not None:
        cancel_and_reap(
            process, run_id=self._run_id or "", task_id=task_id,
            worktree_path=self.cwd, config=self.config, emitter=self._emitter,
        )
    else:
        process.cancel(grace_s=self.config.timeouts.kill_grace)
```

`cancel_and_reap` adds the orphan sweep — leftover Docker containers matched
by their `orchestrator.run_id`/`orchestrator.task_id` labels, and processes
still holding the worktree open — and emits a `critical` `task.failed` event
if the reap fails.

`cancel()` is called **from another thread** while your `wait()` is blocked.
Keep a lock around the running-process registry.

## The `on_activity` hook

Every call method takes `on_activity: Callable[[str], None] | None`. Call it
with one short human-readable line per notable live event — a tool call, a
session start — so a foreground `cosmo run` isn't a blank terminal for forty
minutes.

It is **display only**. No classification, retry or scheduling decision ever
reads it. Deliberately a plain string, not a harness-specific event type, so
the state machine stays harness-agnostic.

Don't relay every heartbeat; progress is already tracked separately.

## Timeouts

If you declared `has_internal_timeout=False` (the honest answer for most
CLIs), **do not impose a timeout inside your adapter.** Your adapter doesn't
know which state clock applies — `proposing_wall`, `implementing_wall` and
`validating_wall` are all different, and only the orchestration layer knows
which state it's in.

Block in `wait()`. Cosmo's orchestration layer calls `cancel()` from another
thread when the applicable clock expires, which unblocks your `wait()` by
actually killing the child.

## Structured output, not prose

Cosmo takes a hard line here, and adapters must too:

- **Never parse the model's free-text output as a signal** for success,
  failure, verdict, or classification. A model asked to indicate success will
  indicate success.
- **Do** read structured fields the CLI itself defines: a terminal result
  object's exit status, subtype, cost figure, session id, rate-limit notice.
- The distinction is authorship. A field the *tool* emits is data. A sentence
  the *model* wrote is not.

## Security posture

Whatever your harness's equivalents are:

- **Never use a "skip all permissions" mode.** Assert it absent from your
  constructed argv rather than merely omitting it, so a future edit can't
  reintroduce it silently. The Claude adapter asserts on both
  `--dangerously-skip-permissions` and `bypassPermissions`.
- **Fail closed.** Only explicitly allow-listed tools should execute.
- **Scrub billing-switching environment variables** from the child's
  environment rather than assuming their absence, and `fail()` on them in
  `preflight()`.
- **Load project settings only.** Don't source the operator's global config
  into an unattended run — arbitrary personal hooks, plugins and MCP servers
  with unknown cost and side effects.
- **If you enable telemetry, disable content logging explicitly.** Prompts
  and file contents in a telemetry backend are a data-exfiltration path for a
  private codebase.

## Testing your adapter

`FakeHarnessAdapter` (`cosmo.harness.fake`) is the reference for the
contract's *shape* — scriptable outcomes with no real subprocess. Read it
first; it's short.

For your own adapter, inject the binary path so tests can point it at a
fixture script:

```python
def __init__(self, config, *, cwd=None, binary: str = BINARY) -> None:
    super().__init__(config, cwd=cwd)
    self._binary = binary
```

Then:

1. **Boundary test.** Assert no module outside `cosmo/harness/mytool/` names
   your binary or environment variables. Existing boundary tests show the
   `ast`-based pattern.
2. **Permission test.** Assert your forbidden mode never appears in the
   constructed argv, from the outside.
3. **Cancel test.** Spawn a fixture that forks a child which ignores
   SIGTERM, cancel it, and assert the whole process group is gone. This is
   the failure that costs a host, and it's the one most easily gotten wrong.
4. **Result mapping test.** Feed recorded output through your parser and
   assert the `HarnessResult` fields.

Then, for real:

```bash
cosmo doctor --harness mytool
cosmo harness list                                # capabilities table
cosmo harness probe --harness mytool --prompt "reply with the word ok"
cosmo run --repo /tmp/test-project --harness mytool --task some-task
```

## Checklist

- [ ] `templates/harness/mytool/` written, modeled on `templates/harness/claude/`
- [ ] `name` and `capabilities` declared at class level, honestly
- [ ] `preflight()` is cheap, side-effect free, and fails on
      billing-switching env vars
- [ ] `propose()` pins the change name to `context["spec_id"]` verbatim
- [ ] `review()` is a fresh session and writes its verdict to
      `REVIEW_RESULT_RELATIVE_PATH`, imported not hardcoded
- [ ] `cancel()` kills the whole process group and is thread-safe
- [ ] No timeout inside the adapter when `has_internal_timeout=False`
- [ ] `success` derives from zero-vs-nonzero exit only
- [ ] No prose parsing anywhere
- [ ] A raw log is written and its path returned
- [ ] Registered in `registry.py`
- [ ] Boundary test passes: nothing outside your module names your binary
