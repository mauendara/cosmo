# FAQ

🇬🇧 English | [🇪🇸 Español](FAQ.es.md)

## What is Cosmo, in one sentence?

An orchestrator that runs a dependency-ordered queue of spec-driven
development tasks against your repo unattended, and only merges work that
passes a real Docker build, test and e2e gate.

## How is this different from just leaving an agent running overnight?

The agent's claim that it finished is never what advances a task. A real
build, unit-test and Playwright run happens afterward, in containers Cosmo
started, outside the agent's session, after its process exited. On top of
that: per-task git worktrees, `PreToolUse` hooks that block test-file edits
before they happen, a diff gate that fails a task whose assertion count went
down, flaky-test confirm-by-rerun, and correct process-group kills.

## Which agents does it work with?

Claude Code today. The adapter interface is real and test-enforced — no
orchestration code branches on which harness is configured — but Claude Code
is the only implementation. Writing another is one class:
[write-a-new-adapter](user-docs/en/how-to/write-a-new-adapter.md).

## Does it work with my stack?

The shipped project templates and gate configuration target Java/Spring Boot
plus Vite/TypeScript/React/Tailwind with MariaDB or SQLite. The gate's
images and directories are configuration (`gate.backend_image`,
`gate.backend_dir`, and their frontend counterparts), and the template system
is just directories of markdown — neither is hard-coded to that stack.

The honest limit: the build *commands* per stage aren't configurable yet. A
Go or Rails backend can use the template system for documentation today, but
its build stages need gate work. See
[add-project-template](user-docs/en/how-to/add-project-template.md).

## Can an agent drive Cosmo through MCP?

Not yet. A thin MCP server over the same CLI contract — letting a tool
enqueue, check status, cancel and read logs — is a planned capability
distinct from Cosmo *using* an agent as a harness. **No such server exists
today.** Use the CLI.

## Does it need a subscription or an API key?

A subscription. `cosmo doctor` **fails** if `ANTHROPIC_API_KEY` is set, and
the adapter scrubs it from the child process environment. Its presence
silently switches billing from your subscription to per-token API rates,
which is an expensive thing to discover after an unattended night.

## What does it cost to run?

Cosmo doesn't set the price; your harness does. The cost ceilings default to
`0.0`, meaning no hard stop — correct for subscription billing, where the
binding constraint is rate-limit windows, not dollars. On metered billing,
set `cost.max_cost_per_run_usd` and `cost.max_cost_per_task_usd`. See
[configure-quotas](user-docs/en/how-to/configure-quotas.md).

## What happens when I hit a rate limit mid-run?

The run pauses and schedules an auto-resume: at the reported reset time, or
`quota.default_5h_resume_delay_seconds` (default 5 hours) when the signal
carries no reset time. Weekly exhaustion pauses or stops depending on whether
the run's remaining budget could outlast it.

If your account has usage credits and you'd rather spend them than wait,
`quota.bypass_5h_with_credits = true` — which Cosmo refuses to load without a
non-zero `cost.max_cost_per_run_usd`.

## Can it run tasks in parallel?

No, by design. Worktrees isolate *code*, not runtime — ports, databases and
`/dev/shm` are still shared, so concurrent tasks would contend on all three.
Parallelism means solving that first. A process lock enforces one run at a
time.

## What branch does it merge to?

`git.base_branch`, default `develop`. **Never `main` or `master`.** Promoting
to your release branch is always a human step.

## Does it push anything?

No. The guardrail hooks block `git push` in any form — that also covers every
force-push variant, since the whole subcommand is blocked. Cosmo merges
locally in its own checkout of the repo.

## Can the agent delete my tests to make the build pass?

That's the specific failure this is built around. Three layers:
`PreToolUse` hooks deny edits under protected test paths and deny introducing
`@Disabled`/`.skip(`-style annotations; the diff gate counts assertions on
added versus removed lines and fails the task if they dropped; and for a
harness that can't gate a tool call pre-execution, the same diff gate stands
alone as post-hoc detection. Full detail:
[validation-gate-and-guardrails](user-docs/en/concepts/validation-gate-and-guardrails.md).

## What about a legitimate task whose whole job is writing or changing tests?

Set `allow_test_edits` on that task — `cosmo queue add --allow-test-edits`,
or `allow_test_edits: true` in the task file's frontmatter. Without it the
guard correctly refuses every write and the agent submits nothing, which
looks like an unexplained empty implementation.

Note that adding a *new* test file is always allowed. It's modifying or
deleting an *existing* one that needs the flag — distinguishing an honest
test update from a self-serving one is exactly the judgment an unsupervised
agent can't make about its own work, so it's escalated to a human decision at
enqueue time.

## Does a flaky e2e test burn the retry budget?

No. A failing non-quarantined e2e test is rerun in isolation up to
`gate.flaky_rerun_limit` times (default 3). If it passes, the failure is
reclassified `flaky` and **consumes no retry attempt**. Only when every
rerun fails is it a genuine code error.

A test flagged flaky across three *distinct runs* is appended to
`quarantine-candidates.yml` for human review. Cosmo never promotes a
candidate to the quarantine list itself — that would be the same
self-weakening failure the diff gate exists to catch, just performed by the
orchestrator.

## Why does an expired quarantine entry break the gate instead of being ignored?

Because a stale quarantine entry silently protecting a dead test is exactly
the failure mode the quarantine mechanism exists to prevent. Every entry
needs an owner and an expiry, and renewing one has to be a deliberate act by
a named person. An unowned, unexpiring quarantine list is how a suite quietly
stops testing anything.

## Where does state live?

`$XDG_DATA_HOME/cosmo/` by default (`~/.local/share/cosmo`) — `cosmo.db`
holding state and events, `work/` holding per-task worktrees, `logs/` holding
raw harness logs. Config is `~/.config/cosmo/config.toml`, or `$COSMO_CONFIG`.
`cosmo config show --paths` prints the actual resolved locations.

## Does it use a vector database or embeddings for memory?

No, and that's deliberate. Cross-task continuity comes from three
deterministic sources: structured event logs, SQLite state tables, and
version-controlled markdown knowledge files in the target repo. All three are
queryable, diffable, and identical on a re-read. A retrieval layer would make
recall fuzzier exactly where an unattended loop most needs reproducibility,
and a wrong recall at 3am is a bug nobody is awake to catch.

## Can I edit the tasks before they run?

That's the intended workflow. `cosmo spec add` writes real, git-tracked task
files and prints a preview — it queues nothing. The window between that and
`cosmo spec queue` *is* the approval step; there's no separate UI. Edit
scope, wording, dependencies, or `allow_test_edits` in those files first.

## Why did my task ids get renamed?

`cosmo spec queue` prefixes every task id with the spec name
(`api` → `add-login-api`) and rewrites intra-batch `depends_on` edges to
match. `task_queue.task_id` is a single global key across every project
sharing one Cosmo database — two projects both decomposing to `scaffold-app`
would collide, and a dependency edge would resolve against the wrong
project's finished task.

## Can I queue work while a run is in progress?

Yes. The scheduler recomputes the full eligible set on every pass, so new
tasks are picked up as soon as their dependencies allow.

## What happens if the machine reboots mid-run?

At the next run's startup, every task found in a non-terminal state is
emitted as `task.interrupted` and requeued, and the abandoned `run_state` row
is closed out as `crashed`. Work in progress is lost; the queue isn't.

## Why did my run exit 1 when it said the queue was empty?

Probably `blocked_remaining`, not `queue_empty`. That stop reason is chosen
when at least one task actually blocked during the run — a run that finished
only because everything is stuck should never look like a success.
`cosmo queue ls --status blocked` will show them.

## Do I have to use OpenSpec?

Yes — the propose/apply/archive flow is what Cosmo drives. You don't have to
*author* OpenSpec changes by hand, though: `cosmo spec add` takes a rough
markdown file and produces task files, and each task creates its own OpenSpec
change lazily the first time it runs.

## Can I turn off the adversarial review?

`review.enabled = false`. It removes a harness call per task, so it's
meaningful for time and spend. It also removes the only check that reads the
diff with no memory of how it was written. Turn it off knowingly.

## How do I know what happened overnight?

```bash
cosmo report                     # how the run ended
cosmo queue ls --status blocked  # what's stuck
cosmo queue failures <task_id>   # the real error text for one task
cosmo events tail --payload      # everything, in order
```

And set up notifications — `cosmo notify config` is a one-shot wizard that
sends a real test message before declaring success.

## Is Cosmo's own code AI-written?

Substantially, yes, with human review throughout. Commits are not attributed
to an AI as author or co-author; disclosure of AI assistance goes in the PR
description or commit body instead. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## What license is it under?

Apache License 2.0 — see [LICENSE](LICENSE). That license's Sections 7 and 8
disclaim warranty and limit liability: the software is provided "AS IS," and
you assume the risk of using it.

## Where does the name come from?

*Kosmos* — order out of chaos. Which is what an unattended queue of agent
work needs most, and what the validation gate exists to impose.
