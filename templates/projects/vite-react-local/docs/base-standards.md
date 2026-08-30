# Base Standards

## Code style

- TypeScript strict mode -- a type error is a build failure, not a warning
  to fix later (see `frontend/architecture.md`).
- Prettier + ESLint (`typescript-eslint`), enforced in the build, not just
  advisory. `cosmo init` seeds this file; the actual `eslint.config.*`/
  `.prettierrc` lives in the target repo and is set up once, early, by
  whoever scaffolds the app -- it is not Cosmo's job to configure per task.
- Naming: `PascalCase` for components and their files (`TodoList.tsx`),
  `camelCase` for hooks (`useTodos.ts`) and plain functions, `PascalCase`
  for TypeScript types/interfaces.
- Commit messages: a short imperative summary line, body explaining *why*
  when the change isn't self-evident from the diff -- same discipline this
  repo's own commit history follows.

## Testing expectations

See `testing.md` for the full breakdown. In short: every non-trivial hook
gets a unit test, every user-facing flow gets Playwright e2e coverage, and a
bug fix ships with a regression test that reproduces the bug, added before
the fix, so the test would have failed without it.

## Input handling (the one carry-over from `security.md`)

There is no backend and no `docs/backend/security.md` in this template, but
one thing still applies to a client-only app: any user-entered text that
gets rendered back to the DOM (a todo's title, an expense's description)
must go through React's normal JSX text interpolation (`{value}`), never
`dangerouslySetInnerHTML`, so it can't be used to inject markup or scripts.
If a task genuinely needs to render user-provided rich text, that's worth
flagging rather than reaching for `dangerouslySetInnerHTML` by default.

## Review checklist

Beyond "the validation gate passed" (the gate is the only source of truth
about correctness, but passing doesn't mean a change was reviewed for
these):

- Does the change match its OpenSpec proposal's stated scope, or did it
  grow beyond it?
- Does a grid-based UI still render as DOM + CSS grid, not `<canvas>`?
- Does anything in `docs/` need updating as a result of this change (a new
  entity, a new invariant, a persistence key added)?
- Is there anything in the diff that shouldn't be there -- a stray
  `console.log`, a commented-out block, a `data-testid` added only to dodge
  a semantic query e2e test could have used instead?
- Flag over-engineering as readily as under-engineering -- a state library,
  a memoized value, or an abstraction this app doesn't need is as much a
  finding as a missing test or a dropped requirement.
