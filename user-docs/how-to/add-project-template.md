# How to add a project template

A **project template** is the `docs/` tree `cosmo init` seeds into a target
repo. Those documents are what the enrichment step reads to learn your
conventions, and what the implementing agent consults while writing code. A
template that describes your stack accurately is the cheapest way to stop
every task rediscovering the same constraints by trial and error.

Add one when you work on a stack the shipped templates don't cover.

```console
$ cosmo templates list
project templates
┏━━━━━━━━━━━━━━━━━━━┓
┃ name              ┃
┡━━━━━━━━━━━━━━━━━━━┩
│ _blank            │
│ java-spring-react │
│ vite-react-local  │
└───────────────────┘
```

## Where templates live

```
templates/
  harness/
    claude/            # harness operating policy, agents, skills, hooks
  projects/
    _blank/            # schema-only skeleton
    java-spring-react/
    vite-react-local/
      docs/            # ← everything under here is copied into the target repo
```

Templates are read from Cosmo's own checkout, not from an installed wheel —
which is why the documented install is `uv tool install --editable .`. A
non-editable install fails with a message telling you exactly that.

Adding a template means adding a directory. There is no registry to update,
no code to change: `cosmo templates list` enumerates directories under
`templates/projects/`.

## 1. Copy the skeleton

```bash
cd <your cosmo checkout>
cp -r templates/projects/_blank templates/projects/my-stack
```

`_blank` is schema-only — the right set of headings with nothing filled in.
If your stack is closer to an existing template, start from that instead:

```bash
cp -r templates/projects/vite-react-local templates/projects/my-stack
```

Confirm it's visible:

```bash
cosmo templates list
```

## 2. Understand what gets copied

Everything under `templates/projects/<name>/docs/` is copied to `docs/` in
the target repo, preserving structure. Nothing else in the template directory
is used.

The `_blank` skeleton:

```
docs/
  base-standards.md
  data-model.md
  api-spec.yml
  backend/
    architecture.md
    persistence.md
    security.md
    error-handling.md
  frontend/
    architecture.md
    state-management.md
    styling.md
```

You are not required to keep this shape. `vite-react-local`, for a
frontend-only stack, drops `backend/` and `api-spec.yml` entirely and adds
`persistence.md` and `testing.md` at the top level. Ship the documents your
stack actually has decisions about.

Two things are **deliberately not** part of any template:

- **`docs/specs/`** — that's spec-batch content, not stack boilerplate.
  `cosmo init` creates the empty directory itself.
- **`docs/decisions-log.md`** — Cosmo appends to it during `COMMITTING`, with
  a header it writes on first use.

Files are **never overwritten** by default. `docs/` belongs to the target
repo once seeded, so re-running `cosmo init` won't clobber edits made there.
`--force` overwrites, with a confirmation prompt.

## 3. Write the documents

The rule that matters: **write the constraints that would otherwise be
discovered by a failed task.** These files aren't tutorials — nobody needs
React explained. They're the specific, non-obvious, gate-enforced facts about
your stack that an agent will get wrong on its first try and every subsequent
first try.

Concrete examples from the shipped `vite-react-local` template, each of which
existed because a real task burned an attempt on it:

> Pin `@playwright/test` to exactly `1.49.0`, never `@latest` — the
> validation gate runs the e2e stage in
> `mcr.microsoft.com/playwright:v1.49.0-noble`, a container with only that
> version's browser binaries. A newer `@playwright/test` resolves to a
> browser build the container doesn't have.

> `playwright.config.ts` must configure the `json` reporter to write to
> `playwright-report/results.json` — the exact path the gate parses. A
> default or HTML-only reporter leaves that file missing, which the gate
> reports as "playwright produced no report", indistinguishable from the
> suite never running.

> `playwright.config.ts` must read its base URL from `process.env.BASE_URL`,
> not a hardcoded localhost port — the gate starts the built app as a
> container on a private Docker network and passes `BASE_URL` pointing at
> that container's hostname.

Each is two sentences and each saves a failed attempt per project, forever.
That's the standard to aim for.

Cover, at minimum:

- **Anything the gate enforces that your stack must be configured for.** The
  Playwright pinning and reporter path above are the archetype. Get these
  wrong and every project on this template rediscovers them.
- **Directory layout**, specifically where the backend and frontend live.
  `gate.backend_dir` and `gate.frontend_dir` default to `backend/` and
  `frontend/`; if yours differ, say so here *and* set them in config.
- **Test conventions** — what a test file is called, where it goes, which
  queries to use. Note that the guardrail hooks protect `src/test/**`,
  `e2e/**`, and `**/*.{test,spec}.{ts,tsx,jsx}` by default.
- **Persistence and data model** — the schema, migration approach, and how
  entities relate.
- **Error handling and security posture** — the taxonomy, and what must never
  appear in a response body.
- **Style and standards** — formatting, naming, lint rules that are enforced
  rather than suggested.

Keep them factual and current rather than narrating history. Git history and
the event log already cover what happened. And keep each file under
`knowledge.max_file_lines` (default 400) — Cosmo enforces that cap during
`COMMITTING`, so an over-long file will fail a task rather than being
silently trimmed.

## 4. Match the gate to the stack

If your stack isn't a Maven backend plus a Node frontend, the template alone
isn't enough — the gate's images and directories are configuration:

```toml
[gate]
backend_image  = "golang:1.23"
backend_dir    = "server"
frontend_image = "node:24.19-bookworm"
frontend_dir   = "web"
```

A missing `backend_dir` skips the backend stages entirely. A missing
`frontend_dir` skips the e2e stage.

`playwright_image` must be pinned to an explicit tag — config load rejects
`:latest` or a bare image name. If you change it, change
`playwright_npm_version` in the same edit and update the pinning instruction
in your template's testing document. Those three are one fact recorded in
three places, and letting them drift is exactly the failure the pin exists to
prevent.

Note that the build and test *commands* per stage are not currently
configurable. The gate runs Maven against a backend directory and
`npm ci && npm run build` against a frontend one — so a frontend template
must produce a repo with a committed lockfile and a `build` script, and a
backend template must produce a Maven project. A genuinely different stack
(Go, Rails, Python) can use the template system for its documentation today,
but its build stages will need gate work. Say so in the template rather than
letting someone discover it at 3am.

## 5. Test it end to end

```bash
mkdir /tmp/template-test
cosmo init /tmp/template-test --project-template my-stack
```

Check that `docs/` looks right, that `docs/specs/` exists, and that the
symlinks were created. Then run the gate against a real worktree of a project
built on it:

```bash
cosmo validate /path/to/a/worktree --task-id template-smoke-test
```

This runs the whole gate standalone without touching the queue — the fastest
way to find out that your `backend_dir` is wrong or your Playwright reporter
isn't writing where the parser looks.

## 6. Contribute it back

If the stack is one other people use, open a PR. See
[CONTRIBUTING.md](../../CONTRIBUTING.md). Include:

- The template directory.
- A note in the PR describing the stack it targets and any `[gate]` config it
  needs.
- Ideally, evidence you ran a real task through it — the constraints worth
  documenting are the ones a real failure taught you, and those are the ones
  the template is for.
