# Data Model

## Where entities live

Every persisted entity is a `TypeScript` `interface` in `src/types/`, one
file per entity (e.g. `src/types/todo.ts`, `src/types/habit.ts`) -- this is
the single source of truth both `useLocalStorage` call sites and components
import from, not something re-declared inline wherever it's used. If an
entity needs runtime validation (checking that parsed JSON actually matches
the shape before trusting it, rather than a blind type assertion), colocate
a validator in the same file, next to the interface it validates.

## Invariants live next to the type

Every entity gets a one-line comment above its interface (or above the
specific field) for any invariant that isn't obvious from the type alone --
e.g. "streak count never negative", "a completed todo's `completedAt` is
always >= its `createdAt`", "best score is monotonically non-increasing for
a lower-is-better game, non-decreasing otherwise". A `number` field's type
doesn't tell a future task it can't go negative; the comment does.

## Ownership and lifecycle

For each entity, know and keep current: what creates it, what can mutate it,
and what (if anything) can delete it. This belongs in the same file as the
interface, not a separate document to keep in sync by hand -- e.g.:

```ts
/** A single day's completion record for one habit.
 * Invariant: `date` is a calendar day in the user's local timezone, never a
 * UTC-boundary day -- see the note on timezone handling in the habit
 * tracker's own task if this app has date-boundary logic. */
export interface HabitCompletion {
  habitId: string;
  date: string; // YYYY-MM-DD
}
```

## Known constraints

Uniqueness rules and cross-entity references (e.g. a `HabitCompletion.
habitId` must reference an existing `Habit`) are enforced in the owning hook
(see `frontend/state-management.md`), not by `localStorage` itself, which
has no schema and no referential integrity of its own. If a migration ever
needs one of these assumptions to change, update this file in the same
change, not after.
