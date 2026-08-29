# Cosmo

**An overnight coding agent will tell you it finished. Cosmo doesn't take its
word for it.**

Cosmo runs a queue of spec-driven development tasks against your repo while
you sleep. Every task is built in its own git worktree and has to survive a
real Docker build, unit-test, and Playwright run before a single line reaches
your branch. The agent's own report of success is treated as telemetry, not
evidence.

```console
$ cosmo init ~/code/my-app --project-template vite-react-local
harness: claude (from config default)
project template: vite-react-local
git branch: git init, then created and checked out 'develop'
openspec/ created
docs/: created 7, skipped (already exists) 0
.agent/claude/: synced (template_version=da0446ae5a99)
  created CLAUDE.md -> .agent/claude/CLAUDE.md
  created .claude -> .agent/claude
  created agents -> .agent/claude/agents
  created skills -> .agent/claude/skills
registered project my-app-997f83de

$ cosmo spec add add-login --repo ~/code/my-app --from ./login-idea.md
# ... enrichment + decomposition, then a preview of the task files written
#     under docs/specs/add-login-spec/tasks/ -- nothing is queued yet

$ cosmo spec queue add-login --repo ~/code/my-app
$ cosmo run --repo ~/code/my-app
01:54:20Z >> run.started
01:54:31Z >> task.state_changed [add-login] queued -> proposing
02:11:07Z >> task.state_changed [add-login] implementing -> validating
02:19:44Z >> task.validation_result [add-login] passed=True, unit=pass (14p/0f/0s), e2e=pass (3p/0f/0s)
02:20:12Z >> task.completed [add-login]
...
stopped (queue_empty)
completed=3 blocked=0 requeued=0 retried=1
```

## Why this exists

Four things break in a naive "leave the agent running overnight" setup, and
Cosmo is built around all four:

- **Tests get gamed.** An agent that can't make a test pass can always delete
  it, `@Disabled` it, or `.skip(` it. Cosmo blocks those edits *before they
  happen* with `PreToolUse` hooks, then counts assertions in the diff and
  fails the task if they went down. See
  [validation-gate-and-guardrails](user-docs/concepts/validation-gate-and-guardrails.md).
- **Processes leak.** A killed Maven, Node, Chromium or Docker process that
  keeps its children alive eats the host's memory until every later task
  fails for unrelated reasons. Cosmo kills the whole process group, then
  sweeps for orphaned containers and worktree holders.
- **One flaky e2e test burns the retry budget.** Cosmo reruns a failing
  non-quarantined e2e test in isolation before believing it, and keeps a
  version-controlled quarantine list where every entry must have an owner and
  an expiry date — an expired entry breaks the gate rather than silently
  protecting a dead test.
- **State bleeds between tasks.** Every task gets its own `git worktree` and
  its own branch. No branch switching, no half-applied work from the task
  before it.

## Install

```bash
git clone <this repo> cosmo && cd cosmo
uv sync
uv tool install --editable .    # optional: puts `cosmo` on your PATH
```

Then `cosmo doctor` to check the host has git, Docker, `openspec`, `gitleaks`
and a working harness. Full prerequisites and first run:
[the tutorial](user-docs/tutorial.md).

## How it works, in one pass

1. `cosmo init <repo>` bootstraps a target repo — `openspec/`, a `docs/`
   template, and the harness's operating policy and guardrail hooks under
   `.agent/<harness>/`.
2. You get work into the queue either by writing a rough spec and letting
   Cosmo enrich and decompose it (`cosmo spec add` → `cosmo spec queue`), or
   by hand-authoring an [OpenSpec](https://github.com/Fission-AI/OpenSpec)
   change and queueing it directly (`cosmo queue add`).
3. `cosmo run` drains the queue in dependency order, one task at a time:
   fresh worktree → propose → implement → **validation gate** → adversarial
   review by a session with no memory of the implementation → merge to your
   integration branch.
4. A failure retries with the real error detail fed back in; a task that
   can't be fixed lands `BLOCKED` and the queue moves on. Enough distinct
   blocks trip a circuit breaker and pause the run for a human.
5. Everything lands in local SQLite plus an append-only event log, so
   `cosmo report`, `cosmo events tail` and `cosmo queue failures` can
   reconstruct the night without you reading raw logs.

## Documentation

- **[Tutorial](user-docs/tutorial.md)** — first project, first task, start to finish.
- **How-to** — [VPS setup](user-docs/how-to/setup-vps.md) ·
  [WSL2 setup](user-docs/how-to/setup-wsl2.md) ·
  [quotas and spend](user-docs/how-to/configure-quotas.md) ·
  [add a project template](user-docs/how-to/add-project-template.md) ·
  [write a harness adapter](user-docs/how-to/write-a-new-adapter.md)
- **Reference** — [CLI](user-docs/reference/cli.md) ·
  [config schema](user-docs/reference/config-schema.md) ·
  [event schema](user-docs/reference/event-schema.md)
- **Concepts** — [architecture](user-docs/concepts/architecture-overview.md) ·
  [validation gate and guardrails](user-docs/concepts/validation-gate-and-guardrails.md) ·
  [quota and safety model](user-docs/concepts/quota-and-safety-model.md)
- [FAQ](FAQ.md) · [Troubleshooting](TROUBLESHOOTING.md) ·
  [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

## Harness-agnostic by design

Cosmo never invokes a coding-agent CLI directly. Every call goes through one
adapter interface, and no orchestration code branches on which harness is
configured. **Claude Code is the only adapter implemented today** — that's a
starting point, not the ceiling. Writing another one is a single class:
[write-a-new-adapter](user-docs/how-to/write-a-new-adapter.md).

## License

Apache License 2.0 — see [LICENSE](LICENSE).

## The name

Cosmo, from *kosmos* — order out of chaos. It's what an unattended queue of
agent work needs most, and what the validation gate is there to impose.
