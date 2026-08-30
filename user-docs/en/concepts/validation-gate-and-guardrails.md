# The validation gate and the anti-gaming guardrails

This is the document to read if you're deciding whether to trust Cosmo with
an unsupervised night.

## The premise

An agent working unattended has one reliable way to make a red suite go
green: change the suite. Delete the failing test. Add `@Disabled`. Change
`test(` to `test.skip(`. Loosen the assertion until it can't fail. None of
these are exotic — they're the shortest path from "the task isn't done" to
"the task looks done," and an agent optimizing for a checked box will find
them.

Cosmo's response is structural rather than exhortative. The agent is told not
to do this, but nothing depends on it complying:

> **The validation gate is the only source of truth about correctness.**

A checked box in `tasks.md`, a "done" in stdout, a confident final summary —
all of it is *liveness telemetry*. It tells Cosmo the process is still alive
and roughly where it is. It never advances a task toward `DONE`. The only
thing that does is a real build, a real test run, and a real Playwright pass
in a container Cosmo started itself, outside the agent's session, after the
agent's process has exited.

---

## What the gate actually runs

`VALIDATING` executes five things, serially, stopping at the first failure:

```
diff gate → gitleaks scan → build → unit tests → e2e
```

**1. The diff gate.** Runs *before any test executes*, against
`git diff <base_branch>...<task_branch>` computed fresh from the task's
worktree. Detailed below.

**2. The gitleaks scan.** A backstop for the pre-commit hook. A missing
`gitleaks` binary fails the stage rather than silently skipping it — the
scan is not optional.

**3. Build.** `mvn -B -q -DskipTests package` in `gate.backend_image` if the
repo has a `backend/`; the frontend build in `gate.frontend_image` if it has
a `frontend/`.

**4. Unit tests.** Both sides, in their own containers.

**5. E2E.** The frontend (and backend, if there is one) start as long-lived
containers on a private Docker network. Playwright runs against them by
container hostname, in the pinned `mcr.microsoft.com/playwright` image —
matching how the app is actually deployed, not relying on host networking.
Your `playwright.config.ts` must read `process.env.BASE_URL` and write a
`json` reporter to `playwright-report/results.json`, or the gate has nothing
to parse.

A repo with no `backend/` doesn't skip the e2e stage — Playwright simply
runs against the frontend alone. Skipping e2e whenever a backend is missing
would make it silently "pass" with zero tests run for every frontend-only
project, which is exactly the hole the gate exists to close.

Every container gets `--shm-size` and `--ipc=host` (Chromium falls over
without them) and runs as an unprivileged user with `HOME=/tmp`, so
`node_modules`, `dist` and `target` don't land root-owned in the bind-mounted
worktree where the unprivileged agent session can never clean them up.

Each stage has its own budget (`gate.stage_timeout_seconds`, default 30
minutes), so one hung container can't block a run forever. A stage timeout is
classified `environment_error`, which by design **does not consume the task's
code-level retry budget**.

---

## Layer 1: prevention — `PreToolUse` hooks

The strongest defense is the edit never happening. `cosmo init` installs
guardrail hooks into the target repo under `.agent/<harness>/hooks/`, wired
through the harness's own settings. They run before the tool call executes
and can deny it.

**`test_path_guard.py`** — blocks `Edit`/`Write` under protected test paths:

```
src/test/**        (repo-root anchored)
e2e/**             (repo-root anchored)
**/*.spec.ts   **/*.test.ts
**/*.spec.tsx  **/*.test.tsx
**/*.spec.jsx  **/*.test.jsx
```

The `.tsx`/`.jsx` patterns aren't decorative: a React component test that
renders JSX *must* be `.tsx`, so guarding only `**/*.test.ts` leaves every
component test in a TypeScript+JSX project unprotected.

The guard is bypassed only when the task's own queue row has
`allow_test_edits: true` — set per task at enqueue time
(`cosmo queue add --allow-test-edits`) or in the task file's frontmatter. The
hook reads that flag out of Cosmo's database directly, because a hook is a
separate OS process with no other way to ask.

**`annotation_guard.py`** — blocks *introducing* a skip or disable
annotation: `@Disabled`, `@Ignore`, `.skip(`, `.only(`, `xit(`, `xdescribe(`
and friends. Weakening a test this way is functionally identical to deleting
it, and this catches it inside files the path guard doesn't own.

"Introducing" is judged by comparing counts before and after the proposed
edit, not by a flat substring search — a file that already legitimately
contains one of these tokens must not block an unrelated edit to it.

**`commit_integrity_guard.py`** — blocks git commands that bypass integrity
controls or that are Cosmo's job:

- `git commit ... --no-verify` — bypasses the pre-commit secret scan.
- `git push` in any form — pushing is Cosmo's job. Blocking the whole
  subcommand covers every force-push variant as a subset.
- `git reset --hard` — can silently discard work.

**`background_task_guard.py`** — blocks `Bash` calls with
`run_in_background: true`. This one was found by hand three times before it
existed: a session backgrounds `npm install`, then spends the rest of its
turn budget polling the PID (`kill -0` loops, `sleep`, `ps`), makes zero
progress, and gets killed by the stall timer twenty minutes later. A headless
`claude -p` call returns exactly once; there is no "later" to resume into.
Denying the scheduling *tools* never closed this — the parameter that creates
the detached job had to be denied too.

Hooks are prevention-layer defense in depth, not the only layer. They use
regex matching, not shell-aware parsing, and adversarial evasion is out of
scope for something budgeted at under two seconds.

## Layer 2: detection — the diff gate

Not every harness can gate a tool call before it executes
(`HarnessCapabilities.supports_gating`), and a hook can be bypassed. The diff
gate catches what gets through — and it runs *before the tests do*, so a
weakened suite never gets the chance to report green.

It reads the whole diff against the base branch, selects the files matching
`gate.diff_gate_test_path_patterns`, and flags five things:

| Violation | Condition |
| --- | --- |
| `test_path_deleted` | An existing test file was deleted |
| `test_path_modified` | An existing test file was modified **at all** |
| `assertion_count_decreased` | Net assertion count across the whole diff went down |
| `skip_annotation_introduced` | A `gate.diff_gate_skip_annotations` substring appears on an added line in a test file |
| `test_loc_dropped` | A test file lost more than `gate.diff_gate_loc_drop_threshold` net lines (default 20) |

Two properties are worth understanding before this surprises you.

**A newly *added* test file is not flagged.** Writing its own tests is
exactly what a well-behaved agent should do, and an earlier version of this
gate that rejected any task adding an e2e test defeated the point entirely.
New files are still subject to the assertion-count, skip-annotation and LOC
checks — an added-but-immediately-disabled test is still suspicious.

**But an existing test file being modified at all is a violation.** Not
"modified suspiciously" — modified. If a task legitimately needs to change an
existing test, that task needs `allow_test_edits`, which bypasses the diff
gate entirely for it. This is a blunt rule on purpose: distinguishing an
honest test update from a self-serving one is precisely the judgment call an
unsupervised agent can't be trusted to make on its own behalf, so it's
escalated to a human decision made at enqueue time.

Assertion counting is per-diff, not per-file: call sites are counted on added
versus removed lines across every test file — `assertThat(` (AssertJ),
`assertX(` (JUnit), `expect(` (Vitest, Playwright) — and only the net total
has to not go down.

Assertion counting is a line-count heuristic, not a real parser, and it is
deliberately biased: it only ever *under*-counts removals, never mistaking an
unrelated line for a removed assertion. The worst case is a real violation
occasionally slipping through, not honest work being blocked by a false
positive. A per-language parser is a known future improvement, not a
pretended-solved problem.

A diff gate violation is recorded with `failure_stage=test_integrity`, and
unlike an environment error it **does** consume a retry attempt.

## Layer 3: post-hoc inspection

For an adapter that reports `supports_gating: false`, the same diff gate is
the only defense — detection standing alone, with no prevention in front of
it. Strictly weaker, and stated as such: an adapter without pre-execution
gating gets a worse guarantee, and that shows up in `cosmo harness list`
rather than being papered over.

---

## Flaky tests: the other half of trusting the gate

A gate that fails on noise is a gate you'll learn to override. A single flaky
Playwright test can burn a task's whole retry budget chasing a bug that
doesn't exist, then block it for `code_failure` that was never code.

Cosmo handles this in three parts.

**Confirm by rerun.** When a non-quarantined e2e test fails, it is rerun *in
isolation*, up to `gate.flaky_rerun_limit` times (default 3). The first pass
wins: the failure is reclassified `flaky`, and — critically — **consumes no
retry attempt**. Exhausting every rerun without a pass means it's a genuine
`code_error`, and the gate treats it as one.

Scoped to e2e deliberately. Unit-test flakiness is a different problem and
this doesn't pretend to solve it.

**The quarantine list.** `quarantine.yml` names tests excluded from failing
the gate. Every entry requires an owner and an expiry:

```yaml
entries:
  - test_id: "com.example.FooTest#flakyUnderLoad"
    owner: "someone@example.com"
    expiry: "2026-12-31"
    reason: "Times out under CI contention, tracked in JIRA-1234"
```

`test_id` matches exactly what the gate reports — `ClassName#method` for
JUnit, the full title for Playwright. `expiry` is an ISO 8601 date.

**An expired entry breaks the file.** Not "is ignored" — loading the file
raises, and the gate stops. An unowned, unexpiring quarantine list is
precisely how a test suite quietly stops testing anything, and a stale entry
silently protecting a dead test is the failure mode this whole mechanism
exists to prevent. Renewing an entry has to be a deliberate act by a named
person.

**Candidates, never auto-promotion.** A test classified flaky across
`gate.flaky_quarantine_candidate_threshold` *distinct runs* (default 3) is
appended to `quarantine-candidates.yml` for human review. Cosmo never
promotes a candidate into `quarantine.yml` itself. Doing so would be the same
self-weakening failure mode the diff gate exists to catch, just performed by
the orchestrator instead of the agent.

Distinct *runs*, not distinct events — a test flagged twice within one run
counts once.

---

## The adversarial review

Between `VALIDATING` and `COMMITTING`, if `review.enabled` is true (it is by
default), Cosmo makes one more harness call. It is a genuinely fresh session:
no session resumption, no retry context, no memory whatsoever of how the diff
came to exist. It gets exactly two things — `git diff <base>...HEAD` and the
OpenSpec change describing what was asked — and judges only those.

This matters because the alternative, asking the implementing session to
review its own work, is a session grading its own homework with full memory
of every shortcut it justified along the way.

The verdict is not read from the session's final message. Parsing prose for a
signal is prohibited: a model asked to say "approved" will find its way to
saying it. Instead the reviewer writes a structured file to the worktree:

```json
{"verdict": "approved"}
{"verdict": "rejected", "reason": "<specific enough to act on>"}
```

Cosmo reads that file back after the call returns. A missing, unreadable,
malformed, or verdict-less file is treated as an **environment problem with
the review call**, never as a rejection — a broken reviewer must not silently
approve, and must not silently condemn either.

A rejection retries against the same `retries.max_attempts` budget as a gate
failure, with the reason fed back in.

---

## What happens to a failure

Failures are classified into four types, and the classification determines
whether it costs the task an attempt:

| Type | Counts against `max_attempts`? | Typical cause |
| --- | --- | --- |
| `code_error` | **yes** | Build failure, failing test the reruns couldn't clear |
| `test_integrity` (stage) | **yes** | Diff gate violation |
| `timeout` | yes, at `IMPLEMENTING` | Wall clock or stall timer fired |
| `environment_error` | **no** | Docker unavailable, stage timeout, harness process died, review call broken |
| `flaky` | **no** | Confirmed by rerun |

`environment_error` never consuming the code budget is what makes "a broken
environment must not exhaust a task's retries" true structurally, rather than
true in the classifier's opinion. It does still get a bounded local retry
loop, because an unbounded one on a permanently broken host is worse.

Every failure is written to `task_failures` with the real detail —
assertion text, stack excerpts, files touched — and the next attempt's prompt
carries it:

```
Attempt 2 failed at stage e2e_tests (code_error): 1 test failed
  LoginPage › redirects logged-out users
  Expected URL to contain "/login", received "/dashboard"

Previous attempts:
- attempt 1 (unit_tests): 3 tests failed
```

Read it yourself with `cosmo queue failures <task_id>`. That text has no
other CLI surface — the event payloads carry failing test *names*, not their
assertion text, on purpose.

---

## Where secrets are stopped

Three independent layers, none of which trusts the one above it:

1. **`permissions.deny`** on secret-shaped paths in the harness's settings —
   `.env*`, `secrets/**`, `*.pem`, `id_rsa*`. The agent can't read them.
2. **A gitleaks pre-commit hook**, installed on every worktree creation. It
   fails closed: a missing `gitleaks` binary blocks the commit rather than
   skipping the scan. `commit_integrity_guard.py` denies the agent's own
   `--no-verify`.
3. **The gate's own gitleaks scan**, ahead of the build, catching anything
   that reached a commit anyway.

A finding is recorded with `failure_stage=secrets` — deliberately its own
stage rather than folded into `test_integrity`, so querying the failure
history later isn't ambiguous.

## What this doesn't claim

- The assertion-count heuristic can be fooled by a determined effort. It
  raises the cost of gaming; it doesn't make it impossible.
- Hook regexes aren't shell-aware and won't survive adversarial quoting.
- A test that was always wrong will keep passing. The gate proves the suite
  runs and stays as strong as it was — not that the suite is good.
- Nothing here substitutes for reading the diff before it reaches `main`.
  Cosmo merges to your integration branch, never to `main` or `master`.
