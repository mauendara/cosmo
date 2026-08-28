# Testing

## Unit / component: Vitest + React Testing Library

- Every custom hook in `src/hooks/` with non-trivial logic (anything beyond
  a thin wrapper) gets a unit test -- `useLocalStorage` itself, plus every
  domain hook (`useTodos`, `useStreak`, etc.).
- Component tests use React Testing Library against the rendered DOM/
  accessibility tree (`render`, `screen.getByRole`, `screen.getByText`) --
  never test implementation details like internal state or a component's
  own function names.
- Pure functions in `src/lib/` (date-boundary math, score calculation) are
  the cheapest, most valuable tests in this app: no React rendering
  overhead, and they're exactly the code most likely to hide an off-by-one
  or a timezone bug (see the habit tracker's date-boundary concerns).
- Accessibility is checked automatically, not just asserted in prose:
  `vitest-axe` (or an equivalent automated a11y checker) runs against
  rendered components in the same test file as their other RTL assertions.
  This is how `styling.md`'s accessibility baseline (keyboard operability,
  meaningful `alt` text) actually gets enforced, rather than being a rule
  nobody checks.

## E2E: Playwright, DOM/accessibility queries only

- Use `getByRole`, `getByText`, and similar semantic queries against real
  rendered DOM -- never a pixel/screenshot assertion and never a
  `data-testid` hatch for something a semantic query can already reach. See
  `frontend/architecture.md`'s DOM-vs-canvas rule -- this is the other half
  of the same guardrail: an e2e suite is only as trustworthy as the surface
  it's actually asserting against.
- Pin `@playwright/test` to exactly `1.49.0` (`npm install -D
  @playwright/test@1.49.0`), never `@latest` -- Cosmo's validation gate runs
  the e2e stage in `mcr.microsoft.com/playwright:v1.49.0-noble`, a container
  with only that version's browser binaries installed. A newer
  `@playwright/test` resolves to a newer default browser build that
  container doesn't have (`browserType.launch: Executable doesn't exist at
  .../chrome-headless-shell`), failing every e2e run inside the gate even
  though `npx playwright test` works fine on a developer's machine with
  browsers installed locally. Confirmed live, twice, in real project
  scaffolds before this note existed.
- `playwright.config.ts` must configure the `json` reporter to write to
  `playwright-report/results.json` -- the exact path and format Cosmo's gate
  parses for pass/fail counts and failing-test detail:

  ```ts
  reporter: [["json", { outputFile: "playwright-report/results.json" }]],
  ```

  A default/HTML-only reporter (or any other output path) leaves that file
  missing, which the gate reports as "playwright produced no report" --
  indistinguishable from the suite never having run at all, even when every
  test actually passed.
- `playwright.config.ts` must read its base URL from `process.env.BASE_URL`,
  not a hardcoded `localhost` port -- Cosmo's validation gate starts the
  built app as a container on a private Docker network and passes `BASE_URL`
  pointing at that container's hostname, not `localhost`. A config that
  ignores this variable will run against nothing (or the wrong thing) inside
  the gate even though it works fine on a developer's machine:

  ```ts
  use: {
    baseURL: process.env.BASE_URL ?? "http://localhost:4173",
  }
  ```

- This template has no `backend/` directory (see `frontend/architecture.md`)
  -- Cosmo's gate e2e stage runs Playwright against the frontend container
  alone in that case, with no backend container and no `VITE_BACKEND_URL` to
  wait on. This is real, gate-enforced coverage, not a stage that gets
  silently skipped for being backend-less -- write e2e tests as if they
  matter, because they do.

## What counts as a "test path" (§6.1's guardrail)

Cosmo's `PreToolUse` hook and diff gate protect these patterns from being
edited or weakened mid-task (unless the task was explicitly queued with
`allow_test_edits`):

- `src/**/*.test.ts`, `src/**/*.test.tsx` -- unit/component tests colocated
  with the code they test.
- `e2e/**` -- the Playwright suite.

Name test files accordingly (`Foo.test.tsx` next to `Foo.tsx`, not
`Foo.spec.tsx` unless that's this app's established convention -- `.spec.`
is also protected, but pick one convention and stay consistent within the
app). A test file under a path these patterns don't cover is not protected
by the guardrail even if it's genuinely a test -- keep tests inside
`src/` or `e2e/`, not in some other top-level directory invented per-task.
