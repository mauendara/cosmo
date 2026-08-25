# Backend Architecture

Java + Spring Boot. This file is the source of truth for how the backend is
organized; keep it current as decisions are made rather than letting the
codebase drift ahead of it (see the append/reconcile convention in the
repository's root `CLAUDE.md`).

## Layers

Standard three-layer split, one direction of dependency only:

- **Controller** (`@RestController`) -- HTTP concerns only: request/response
  mapping, status codes, input validation via `@Valid`. No business logic.
- **Service** (`@Service`) -- business logic and transaction boundaries
  (`@Transactional` lives here, never on a controller or repository method).
- **Repository** (`@Repository`, Spring Data JPA) -- persistence only. No
  business logic; a repository method name or query should read as a data
  question, not a business decision.

Controllers depend on services; services depend on repositories. A
controller must never inject a repository directly, and a repository must
never call back up into a service.

## Module boundaries

Package by feature (`com.<org>.<app>.<feature>`), not by layer
(`com.<org>.<app>.controller`, `...service`, ...) -- a feature's controller,
service, repository, and DTOs live together. Shared/cross-cutting code
(config, common exceptions, security) lives in a top-level `common` or
`config` package.

## Key dependencies

- **Spring Boot** -- web, data-jpa, validation, security starters as needed.
- **MariaDB** in Docker for local/dev and the validation gate; SQLite is an
  acceptable lighter-weight substitute for a small service -- record which
  one a given project actually uses in `data-model.md`, since the spec
  deliberately leaves this a per-project choice.
- **Testcontainers** may back integration tests instead of the long-lived
  dev database -- correct wiring and disposable isolation, at the cost of
  extra memory pressure; decide per-repo (see `docs/base-standards.md`).

## Cross-cutting concerns

- **Config**: `application.yml` per profile (`dev`, `test`, `prod`); secrets
  never committed (see `security.md`) -- injected via environment variables.
- **Logging**: structured, one line per event where practical; a request's
  correlation id (if introduced) should be present on every log line for
  that request.
- **Error handling**: centralized via `@ControllerAdvice` -- see
  `error-handling.md` for the actual taxonomy and response shape.
