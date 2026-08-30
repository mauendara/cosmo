# Backend Security

## AuthN / AuthZ

Spring Security. Record here, once decided: session vs. token-based auth,
where a token is validated, and how a role/permission check is expressed at
the controller or service layer (`@PreAuthorize`, a manual check, or a
filter). Every protected endpoint's authorization rule should be visible at
the endpoint itself, not inferred from "nothing calls this without a token
in practice."

## Secrets handling

Secrets (DB credentials, API keys, signing keys) are never committed --
enforced structurally in this repo: `.env*`, `secrets/**`, `*.pem`, and
`id_rsa*` are all denied to the harness's `Read` tool (see
`.agent/claude/settings.json`), and a `gitleaks` pre-commit hook plus a
gate-side backstop scan catch anything that slips through (spec 6.1). They
reach the running process via environment variables injected at deploy time,
never via a checked-in `application-prod.yml`.

Any secret that does reach a commit is treated as compromised on discovery
-- rotate it; removing it from history is not sufficient.

## Known-sensitive surfaces

<!-- Fill in as the project grows: which endpoints handle payment
     information, PII, or a privilege-escalation path, so a change touching
     them gets extra scrutiny in review by default. -->
