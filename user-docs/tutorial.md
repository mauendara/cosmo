# Tutorial: your first Cosmo run

By the end of this you will have bootstrapped a real repo, turned a rough
idea into a queued task, run it through Cosmo end to end, and inspected what
happened. It takes one sitting plus however long the task itself runs.

This is the only linear walkthrough in the docs. Everything optional lives in
the [how-to guides](how-to/); everything exhaustive lives in
[reference](reference/).

---

## 0. Prerequisites

Cosmo shells out to real tools. All of these must be on `PATH`:

| Tool | Why |
| --- | --- |
| **Python 3.12+** and [`uv`](https://docs.astral.sh/uv/) | Cosmo itself |
| **git** | worktrees, branches, the merge ladder |
| **Docker** | the validation gate runs every build and test in containers |
| **[OpenSpec](https://github.com/Fission-AI/OpenSpec) CLI** (`openspec`) | the propose/apply/archive flow Cosmo drives |
| **[gitleaks](https://github.com/gitleaks/gitleaks)** | the pre-commit secret scan, and the gate's own backstop scan |
| **A harness CLI** — today [Claude Code](https://claude.com/claude-code) (`claude`) | the agent that actually writes code |

On Windows, run everything inside WSL2 and keep the repo on the WSL2
filesystem, not `/mnt/c` — see [setup-wsl2](how-to/setup-wsl2.md).

## 1. Install Cosmo

```bash
git clone <this repo> cosmo
cd cosmo
uv sync
uv run cosmo --version
```

For a bare `cosmo` on your `PATH`:

```bash
uv tool install --editable .
```

Use `--editable` from a full checkout. Cosmo's project and harness templates
are read from the `templates/` directory in the repository, not from the
installed wheel, and a non-editable install fails with:

```
Cosmo's templates/ directory was not found at .../lib/python3.14/templates.
This requires an editable install (`uv tool install --editable .`) from a
full checkout of Cosmo's own repository.
```

The rest of this tutorial writes `cosmo`; if you skipped the tool install,
write `uv run cosmo` instead.

## 2. Check the host

```console
$ cosmo doctor
harness: claude (from config default)

core checks
┏━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃        ┃ check                  ┃ detail                                            ┃
┡━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ ok     │ python                 │ 3.12.7                                            │
│ ok     │ git                    │ /usr/bin/git                                      │
│ ok     │ docker                 │ /usr/bin/docker                                   │
│ ok     │ openspec               │ /home/you/.local/bin/openspec                     │
│ ok     │ gitleaks               │ /home/you/.local/bin/gitleaks                     │
│ ok     │ disk space             │ 84.1 GB free at /home/you/.local/share/cosmo      │
│ ok     │ state dirs writable    │ /home/you/.local/share/cosmo and siblings         │
│ ok     │ work dir filesystem    │ /home/you/.local/share/cosmo/work                 │
│ ok     │ event/state store      │ not yet created -- initializes on first write     │
│ ok     │ leaked gate containers │ none found                                        │
└────────┴────────────────────────┴───────────────────────────────────────────────────┘
harness checks (claude)
┏━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
│ ok     │ claude cli           │ /home/you/.local/bin/claude                      │
│ ok     │ subscription billing │ ANTHROPIC_API_KEY is unset (subscription billing) │
│ ok     │ permission mode      │ dontAsk                                          │
└────────┴──────────────────────┴──────────────────────────────────────────────────┘
```

Two checks worth understanding before you go further:

- **`subscription billing`** fails, hard, if `ANTHROPIC_API_KEY` is set.
  With the key present the harness silently bills per token instead of
  against your subscription — an expensive thing to discover the morning
  after an unattended run. Unset it.
- **`disk space`** fails below `disk.min_free_gb` (default 10 GB). A run
  aborts on this check rather than starting and failing every task partway
  through with a full disk.

Fix anything reported `FAIL` before continuing. `cosmo doctor` exits non-zero
when it finds a blocking problem, so it works in a pre-run script too.

## 3. Bootstrap a target repo

A **target repo** is the project you want work done on. It's a different
thing from Cosmo's own checkout.

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
git identity: set Your Name <you@example.com>
committed init bootstrap output
```

What just happened, in order:

1. `git init` if the directory wasn't a repo yet, then created and checked
   out the configured integration branch (`git.base_branch`, default
   `develop`). If the repo was already on a different branch with a dirty
   tree, Cosmo refuses to touch it and tells you to sort that out yourself.
2. `openspec init` if `openspec/` wasn't there.
3. Seeded `docs/` from the project template. **Existing files are never
   overwritten** — `docs/` belongs to your repo once seeded. `--force`
   overwrites, with a confirmation prompt.
4. Created `docs/specs/`, where your hand-written specs go.
5. Wrote the harness's operating policy, agent/skill definitions and
   guardrail hooks under `.agent/claude/`, then symlinked what the harness
   expects at the repo root (`CLAUDE.md`, `.claude`, `agents`, `skills`).
   The symlinks are relative, so the repo can move or be cloned elsewhere
   without breaking.
6. Registered the project, so `--harness` can be resolved from a path later.

Pick a template that matches your stack — `cosmo templates list` shows what's
available (`_blank`, `java-spring-react`, `vite-react-local` today). If none
fits, start from `_blank` and see
[add-project-template](how-to/add-project-template.md).

Now confirm the repo itself is ready:

```bash
cosmo doctor --project-path ~/code/my-app
```

## 4. A note on `--repo`

Every command that operates on a target repo (`spec add`, `spec queue`,
`run`, `queue retry`) takes `--repo <path>`, defaulting to the current
directory. The resolved path is checked against `cosmo init`'s registration:
a typo or an un-`init`ed directory fails loudly rather than quietly operating
somewhere wrong.

This tutorial spells `--repo` out. Once you're used to it, `cd` into the
target repo and drop the flag.

## 5. Write a rough spec

Write down what you want, in whatever shape it comes out. It can describe
several pieces of work — Cosmo's job is to break it up.

```bash
cat > ~/login-idea.md <<'EOF'
Add email + password login.

Users should be able to sign up, log in, and log out. Sessions persist
across a page reload. Show a clear error for a wrong password. Logged-out
users hitting a protected route get redirected to the login page.
EOF
```

## 6. Enrich and decompose it

```bash
cosmo spec add add-login --repo ~/code/my-app --from ./login-idea.md
```

This copies your file in as `docs/specs/add-login-spec.md` and drives the
harness through two steps: **enrichment** (reading your repo's own
`docs/backend/`, `docs/frontend/`, `docs/data-model.md`,
`docs/base-standards.md` for its conventions) and **decomposition** (splitting
the work into units with explicit dependencies).

It writes one file per unit of work under
`docs/specs/add-login-spec/tasks/`, then prints a preview table:

```console
add-login-spec tasks
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┓
┃ task_id          ┃ title                     ┃ depends_on       ┃ priority ┃ allow_test_edits ┃ file        ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━┩
│ add-login-schema │ User table and migration  │ -                │ 0        │ -                │ schema-task…│
│ add-login-api    │ Login and logout endpoints│ add-login-schema │ 0        │ -                │ api-task.md │
│ add-login-page   │ Login page and redirects  │ add-login-api    │ 0        │ -                │ page-task.md│
└──────────────────┴───────────────────────────┴──────────────────┴──────────┴──────────────────┴─────────────┘
```

Each file is YAML frontmatter plus a markdown body:

```markdown
---
task_id: api
title: Login and logout endpoints
depends_on: [schema]
priority: 0
allow_test_edits: false
---

Implement POST /api/auth/login ...
```

The preview shows *namespaced* ids (`add-login-api`) while the files on disk
carry the bare ones (`api`) — see step 7 for why.

**Nothing is queued yet.** These are real, git-tracked files in your repo,
and the window between now and the next command *is* the review step — there
is no separate approval UI. Open them. Fix scope, wording, dependencies. If
a task's entire deliverable lives under a guarded test path (an `e2e/` suite,
say), set `allow_test_edits: true` on it now — otherwise the guardrail hooks
will correctly refuse to let the agent write anything and the task will fail
for a reason that looks like nothing.

## 7. Queue it

```console
$ cosmo spec queue add-login --repo ~/code/my-app
queued add-login-schema
queued add-login-api
queued add-login-page
```

Every task id gets prefixed with the spec name at insert time
(`api` → `add-login-api`), and `depends_on` edges within the batch are
rewritten to match. `task_queue.task_id` is a single global key across every
project sharing one Cosmo database, so two projects that both decompose to a
task called `scaffold-app` would otherwise collide — and a `depends_on` edge
would resolve against the wrong project's already-finished task. A
`depends_on` entry that isn't part of this batch is left alone, so you can
still point at something queued earlier.

Re-running `spec queue` on an already-queued batch is a no-op, not an error.

```console
$ cosmo queue ls
task queue
┏━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━┓
┃ task_id                 ┃ status ┃ attempts ┃ depends_on            ┃ priority ┃ blocked_reason ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━┩
│ add-login-schema        │ queued │ 0/2      │ -                     │ 0        │ -              │
│ add-login-api           │ queued │ 0/2      │ add-login-schema      │ 0        │ -              │
│ add-login-page          │ queued │ 0/2      │ add-login-api         │ 0        │ -              │
└─────────────────────────┴────────┴──────────┴───────────────────────┴──────────┴────────────────┘
```

## 8. Preview the order

```console
$ cosmo run --repo ~/code/my-app --dry-run
harness: claude (from project registration)
1. add-login-schema
2. add-login-api
3. add-login-page
```

This resolves the DAG and prints nothing else — no harness calls, no
worktrees, no cost. Dependency cycles are rejected here (and at enqueue
time), never discovered mid-run.

## 9. Run it

```bash
cosmo run --repo ~/code/my-app
```

The queue drains strictly one task at a time until it's empty, a circuit
breaker trips, a cost or quota ceiling intervenes, or the run's wall clock
(`timeouts.run_wall`, default 10 hours) expires.

For each task, in order:

```
QUEUED → PROPOSING → PROPOSED → IMPLEMENTING → VALIDATING
       → REVIEWING → COMMITTING → MERGING → FINISHING → DONE
```

- **PROPOSING** — the harness runs OpenSpec's propose workflow, creating
  `openspec/changes/<task-id>/` inside a fresh worktree at
  `<work_dir>/<run_id>/<task_id>` on branch `task/<task-id>`.
- **IMPLEMENTING** — the harness writes the code and commits it, watched by
  a stall timer and a wall clock. Progress comes from watching the change's
  `tasks.md`, not from anything the agent claims.
- **VALIDATING** — the gate. Diff gate → gitleaks scan → Docker build →
  unit tests → e2e. This is the only thing that decides whether the task
  worked. See
  [validation-gate-and-guardrails](concepts/validation-gate-and-guardrails.md).
- **REVIEWING** — a *fresh* harness session with no memory of the
  implementation reads `git diff <base>...HEAD` and the change spec, and
  writes an approve/reject verdict to a file. Disable it with
  `review.enabled = false`.
- **COMMITTING** — enforces the knowledge-file line cap on any `docs/**/*.md`
  the task touched, and appends one line to `docs/decisions-log.md`.
- **MERGING** — merges `task/<task-id>` into your base branch via the
  conflict ladder (merge, then rebase + re-run the gate, then block).
- **FINISHING** — `openspec archive`, best-effort; a failure here is logged
  and never un-does a merge that already happened.

You'll see one line per state transition, plus per-tool-call chatter:

```
01:54:20Z >> run.started
01:54:31Z >> task.state_changed [add-login-schema] queued -> proposing
02:11:07Z >> task.state_changed [add-login-schema] implementing -> validating
02:19:44Z >> task.validation_result [add-login-schema] passed=True, unit=pass (14p/0f/0s), e2e=pass (3p/0f/0s)
02:20:12Z >> task.completed [add-login-schema]
```

The run ends with a summary and an exit code — `0` only for a clean
`completed` or `queue_empty` stop, `1` for everything else including
`blocked_remaining`:

```
stopped (queue_empty)
completed=3 blocked=0 requeued=0 retried=1
```

To drive a single already-queued task instead of the whole queue:

```bash
cosmo run --repo ~/code/my-app --task add-login-schema
```

## 10. Inspect what happened

Long after the run, from a different terminal, none of this needs the run
process to still exist.

```bash
cosmo report                    # the most recent run
cosmo report --run <run_id>     # a specific one
cosmo report --follow           # live, until the run reaches a terminal status
```

Status, stop or pause reason, completed and blocked counts broken down by
reason, cost, duration.

```bash
cosmo events tail                        # recent events across everything
cosmo events tail --run <run_id>
cosmo events tail --task add-login-api
cosmo events tail --type task.blocked
cosmo events tail --payload              # full JSON body under each row
cosmo events tail --follow               # tail -f
```

The table tells you *that* something happened; `--payload` tells you *what*.

For a task that didn't work:

```bash
cosmo queue show add-login-api        # status, attempts, last error, worktree path
cosmo queue failures add-login-api    # every attempt's full failure record
```

`queue failures` is the one that matters after an unattended night. It prints
each attempt's failure type and stage, a summary, and the **actual error
detail** — assertion messages, stack excerpts, failing test names. That text
has no other CLI surface; event payloads deliberately don't carry it.

## 11. Deal with a blocked task

A `BLOCKED` task keeps its worktree and branch on disk for you to look at.
Get the path from `cosmo queue show`.

Once you've fixed whatever caused it — a missing dependency, a bad spec, a
broken environment — put it back in the queue:

```bash
cosmo queue retry add-login-api --repo ~/code/my-app
```

`retry` resets the attempt counter and, if the worktree still has the commit
`PROPOSING` made, discards only the failed implementation and keeps the
already-valid OpenSpec change — so the next run picks up at `IMPLEMENTING`
without paying for propose again.

If the task has already blocked for the *same reason* several times, `retry`
refuses and says so rather than handing it another silent round of attempts.
`--force` overrides — use it after a human has actually addressed the
recurring cause, not to make the message go away.

## 12. Next steps

- Run it unattended overnight: [setup-vps](how-to/setup-vps.md) or
  [setup-wsl2](how-to/setup-wsl2.md).
- Get told when something breaks: `cosmo notify config` walks you through
  Telegram setup end to end, including sending a real test message.
- Understand what the gate actually checks:
  [validation-gate-and-guardrails](concepts/validation-gate-and-guardrails.md).
- Tune ceilings before a long run:
  [configure-quotas](how-to/configure-quotas.md).
