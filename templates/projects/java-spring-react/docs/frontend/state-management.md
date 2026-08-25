# Frontend State Management

## Local vs. shared state

Default to local component state (`useState`/`useReducer`). Promote state to
a shared store only when at least two components that aren't in a direct
parent/child relationship need it -- prop-drilling two or three levels is
usually still cheaper than the indirection a global store adds.

## Server state

Anything that originates from the backend (fetched data, mutation results)
is server state, not app state -- it is owned by the data-fetching layer
(TanStack Query or equivalent), not duplicated into a separate store. A
mutation invalidates/refetches the relevant query rather than the caller
hand-updating cached data, unless an optimistic update is a deliberate,
documented choice for that one interaction.

## Conventions

- A shared store (if one exists) is scoped by feature, not one global blob
  -- the same feature-folder discipline as the rest of the app.
- Derived values (a filtered list, a computed total) are computed at read
  time, not stored redundantly alongside their source -- a stored derived
  value is a second place that can silently go stale.
