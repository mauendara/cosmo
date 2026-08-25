# Backend Persistence

## Data store

MariaDB, run via Docker for local dev and the validation gate (spec 1: the
database always runs in Docker, even when the app itself runs natively).
SQLite is an acceptable substitute for a small/single-tenant service --
whichever this project actually uses, record it here so it isn't ambiguous.

## Migration strategy

Schema changes go through a migration tool (Flyway or Liquibase -- pick one
per project and record the choice here), never hand-applied SQL and never
`hibernate.ddl-auto=update` outside a throwaway dev profile. Every migration:

- Is forward-only in the same sense Cosmo's own schema is (see the root
  repo's `store/migrations.py` for the pattern, if useful as a reference) --
  once merged, a migration's SQL doesn't change; a later fix is a new
  migration.
- Is reviewed like code, because a migration is the one artifact that's
  genuinely hard to undo once it's run against real data.

## Transaction boundaries

`@Transactional` lives on service methods, never on controllers or
repositories. A transaction should not span a network call to another
service -- if a use case needs both a DB write and an external call, decide
explicitly (and document here) which one is allowed to fail independently,
rather than leaving it to whatever Spring's default propagation happens to
do.

Lazy-loaded JPA associations must not be accessed outside an open
transaction/session (the classic `LazyInitializationException`) -- fetch
what a DTO needs inside the service method, before the transaction closes.
