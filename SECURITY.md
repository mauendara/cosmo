# Security

Cosmo runs an LLM agent, unsupervised, with write access to a source
repository and the ability to execute shell commands, on a machine that
probably holds real credentials. That is a meaningful thing to do, and this
document is where the actual boundaries are written down rather than assumed.

## Threat model

### What Cosmo assumes

- **The host is trusted, and valuable.** It holds SSH keys, git credentials
  and a harness subscription. Blast radius is not zero, and nothing here is
  designed as if it were.
- **The target repo's code is trusted at rest.** Cosmo doesn't defend against
  a repo that was already hostile before it arrived.
- **The agent is not malicious, but is not trustworthy either.** It optimizes
  for the objective it was given, which is not always the objective you
  meant. The guardrails exist for *misaligned effort*, not for an adversary.
- **The spec is authored by a human you trust.** A spec is an instruction to
  the agent. Cosmo does not sanitize spec content, and a spec that asks for
  something harmful will be attempted.
- **`docs/` in the target repo is trusted input.** Enrichment reads it and
  the agent follows it. Whoever can write there can steer the agent.

### What Cosmo defends against

| Threat | Defense |
| --- | --- |
| Agent weakens or deletes tests to make a build pass | `PreToolUse` hooks deny the edit; diff gate fails the task; both described below |
| Agent bypasses the secret scan | `--no-verify` denied by hook; gate-side gitleaks scan as backstop |
| Agent reads credentials from the repo | Deny rules on `.env*`, `secrets/**`, `*.pem`, `id_rsa*` |
| Agent commits a secret | Pre-commit gitleaks hook that fails closed, plus a gate scan |
| Agent pushes to a remote | `git push` denied outright by hook (covers every force variant) |
| Agent discards work with `git reset --hard` | Denied by hook |
| Agent runs an unbounded background job and idles | `run_in_background` denied on `Bash`; stall timer as backstop |
| Runaway spend | Per-run and per-task cost ceilings; quota detection and pause |
| Leaked processes exhausting the host | Process-group kill, orphan sweep, circuit breaker weighted double on reap failure |
| A full disk cascading into fake code errors | Pre-run disk check aborts the run |
| Prompts and source in a telemetry backend | Content logging explicitly disabled |
| Unreviewed code reaching a release branch | Cosmo merges only to `git.base_branch`, never `main`/`master` |

### What Cosmo does *not* defend against

Stated plainly, because a threat model that only lists wins is marketing:

- **A determined adversarial agent.** Hook matching is regex over the command
  string, not shell-aware parsing. Adversarial quoting will defeat it. The
  hooks are defense in depth against misaligned effort, budgeted at under two
  seconds each — not a sandbox.
- **Arbitrary code execution on the host.** The agent runs shell commands by
  design. Tool-level allow-listing constrains *which tools*, not what a
  permitted `Bash` call can do. **If that matters to you, run Cosmo on an
  isolated machine or VM with no credentials you'd mind losing.**
- **Prompt injection through repository content.** A malicious comment,
  README or dependency in the target repo can influence the agent. The
  validation gate limits the *consequences* — nothing merges without passing
  a real build and test — but it doesn't prevent the influence.
- **Malicious dependencies.** `npm install` and `mvn` run real package
  manager code, in a container but against your repo.
- **A compromised harness CLI.** Cosmo trusts the agent binary it invokes.
- **Test suites that were always wrong.** The gate proves the suite runs and
  hasn't been weakened. It doesn't prove the suite is good.

## The permission model

Specific to the Claude Code adapter, though the posture generalizes to any
adapter and is documented as a requirement for new ones.

### Fail closed by default

`harness.permission_mode` defaults to `dontAsk`: only tool calls matching the
allow-list execute. The default is denial. There is no interactive human to
approve anything at 3am, so anything not explicitly permitted must simply not
run.

The allow-list is minimal: `Write`, `Edit`, `Bash`.

### `bypassPermissions` is never used

Not merely omitted. `--dangerously-skip-permissions` and `bypassPermissions`
are asserted absent from the constructed argv, and a separate test checks it
from the outside — so a future edit can't reintroduce it silently.

### Deny rules are absolute

They apply in every mode and are not overridable per task:

```
Read(./.env*)         Read(./secrets/**)
Read(**/*.pem)        Read(**/id_rsa*)
ScheduleWakeup        ToolSearch          TaskOutput
```

The last three are scheduling and background-task tools. A headless call
returns exactly once; a session that ends assuming it will be resumed simply
never is, leaving a half-finished state that fails confusingly later.

### The allow-list is passed twice, deliberately

Once in the project's `settings.json`, and again as a CLI flag.

Claude Code has a workspace-trust gate: in a directory that has never been
through the interactive trust dialog — which a per-task worktree, created
fresh, never can be — it **silently ignores every `permissions.allow` entry
from `settings.json`** and denies `Write`, `Edit` and `Bash`, without
surfacing anything the adapter can observe. Passing the same list as a
command-line flag is unaffected by workspace trust. The `settings.json` copy
remains for interactive use outside Cosmo.

### Only project settings are loaded

`--setting-sources project`. The operator's global `~/.claude` — arbitrary
personal hooks, plugins and MCP servers with unknown cost and side effects —
is not sourced into an unattended run. Verified by real invocation: with the
default, this host's global session hooks fired even with `cwd` in `/tmp`,
unrelated to the target repo.

### Billing

`ANTHROPIC_API_KEY` being set is a **hard failure** in `cosmo doctor`, and
the adapter scrubs it from the child process environment rather than assuming
its absence. Its presence silently switches billing from the subscription to
per-token API rates — a security-adjacent problem in the sense that an
unattended overnight run makes it an expensive one.

### Telemetry

Cosmo enables the harness's native OpenTelemetry export and sets
`OTEL_LOG_USER_PROMPTS=0` **explicitly**, rather than trusting the CLI's
default. Prompts and file contents in a telemetry backend are a
data-exfiltration path for a private codebase.

## The guardrail hooks

Installed into the target repo at `cosmo init` under `.agent/<harness>/hooks/`
and re-synced into every worktree. They run before a tool call executes and
can deny it.

| Hook | Denies |
| --- | --- |
| `test_path_guard.py` | `Edit`/`Write` under `src/test/**`, `e2e/**`, `**/*.{test,spec}.{ts,tsx,jsx}` |
| `annotation_guard.py` | Introducing `@Disabled`, `@Ignore`, `.skip(`, `.only(`, `xit(`, `xdescribe(` |
| `commit_integrity_guard.py` | `git commit --no-verify`, `git push` (any form), `git reset --hard` |
| `background_task_guard.py` | `Bash` with `run_in_background: true` |

Two properties worth knowing:

- **They fail closed.** A hook that can't determine the answer denies.
- **The test-path guard reads Cosmo's database** to check the running task's
  `allow_test_edits` flag. A hook is a separate OS process with no other way
  to ask, so the adapter passes `COSMO_TASK_ID` and `COSMO_DB_PATH` in the
  child's environment.
- `annotation_guard` compares counts before and after the proposed edit
  rather than doing a flat substring search, so a file that legitimately
  contains one of these tokens doesn't block unrelated edits.

Their limits are stated in [CONTRIBUTING](CONTRIBUTING.md) and above: regex
over the command string, not shell-aware parsing.

## Secret handling

Three independent layers, none trusting the one above it:

1. **Read denial.** The agent can't read secret-shaped paths at all.
2. **Pre-commit scan.** A gitleaks hook installed on every worktree creation.
   It **fails closed**: a missing `gitleaks` binary blocks the commit rather
   than skipping the scan. `commit_integrity_guard.py` denies the agent's own
   `--no-verify`.
3. **Gate-side scan.** gitleaks runs again as part of validation, ahead of the
   build, catching anything that reached a commit anyway.

A finding is recorded with `failure_stage=secrets` — its own stage rather
than folded into `test_integrity`, so querying the failure history later
isn't ambiguous.

Your own secrets — the harness credential, the Telegram bot token — live in
Cosmo's user config file outside any repository. **Set it to mode `600`.**
`cosmo notify config` writes the token there; nothing writes it into a repo.

## Isolation

- **Per-task git worktrees.** Each task works in its own directory over one
  shared object store. No branch switching, no state bleed.
- **Gate containers, not the host.** Every build and test runs in Docker.
  Containers are labeled with `orchestrator.run_id` and
  `orchestrator.task_id` so they can be found and swept.
- **Non-root containers.** Gate containers run as an unprivileged user with
  `HOME=/tmp`, so build output in the bind-mounted worktree doesn't land
  root-owned where the agent session can never clean it up.
- **Process-group kills.** SIGTERM → `timeouts.kill_grace` → SIGKILL against
  the whole group, then a sweep for orphaned containers and worktree holders.
  Reaping isn't "done" until `killpg(pgid, 0)` proves the group is gone.

Note what *isn't* isolated: ports, databases and `/dev/shm` are shared across
tasks. This is why Cosmo runs strictly one task at a time.

## Merge boundary

Cosmo merges only to `git.base_branch` (default `develop`), in **its own
dedicated checkout** of the repo — never a directory a human works in.

Promotion to `main` or `master` is always manual. Nothing in an unattended
run should have push access to a release branch, and the hooks deny `git
push` entirely regardless.

Before anything merges, it must pass the validation gate and — unless
`review.enabled = false` — an adversarial review by a fresh session with no
memory of how the diff was written.

## Recommended deployment posture

- Run on a **dedicated VM or VPS**, not your workstation.
- Give the run user **only** what it needs: repo access, Docker group
  membership, and the harness credential. No production credentials, no
  deploy keys, no cloud provider tokens.
- Keep the config file at mode `600` and outside every repository.
- Keep `main`/`master` protected at the forge, not just by convention.
- Review the diff before promoting. Cosmo raises the floor on what reaches
  your integration branch; it doesn't replace a human reading the change.
- Watch the notifications. `min_severity = "info"` for a new deployment.

## Reporting a vulnerability

**Please don't open a public issue for a security problem.**

Report it privately to the maintainers — via GitHub's private security
advisory feature on this repository if enabled, or by contacting the
repository owner directly through the address in the git history.

Please include:

- What the issue is and what an attacker could achieve.
- Steps to reproduce, or a proof of concept.
- Affected version or commit.
- Any mitigation you've identified.

What to expect: acknowledgement that it was received, an assessment, and a
fix or a documented mitigation. This is a small project without a staffed
security team — response is best-effort and unpaid, and there is no bounty
program.

Please give a reasonable window before public disclosure, and let us know if
you intend to disclose so a fix can land first.

This project is licensed under the Apache License 2.0 — see
[LICENSE](LICENSE). No additional disclosure terms beyond what's stated
above apply.
