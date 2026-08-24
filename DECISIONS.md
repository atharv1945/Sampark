Phase 1 — Data Spine

- PostgreSQL 5432 conflict:
  A native PostgreSQL installation on the development machine was already
  listening on 5432. Docker PostgreSQL was therefore moved to local port 5433
  via .env rather than changing the container's internal port.

- Git Bash CRLF issue:
  Sourcing .env from Git Bash retained CRLF characters in values and caused
  authentication problems. The application itself reads environment variables
  directly, so this affected only the shell-based manual workflow.

- risk_id collision:
  Initial risk IDs used generation position only. Loading a second seed exposed
  cross-seed collisions and silent row loss through ON CONFLICT DO NOTHING.
  The identifier was changed to include the seed and the loader was changed to
  detect conflicting existing records explicitly.

  Phase 3 — Agent Registry

Registration concurrency:
PostgresAgentRepository registration currently uses a check-then-insert
flow. Concurrent first registrations for the same agent_id can race on the
unique constraint, producing a database uniqueness error rather than the
idempotent/conflict-specific repository error.

Accepted for Phase 3 because registration concurrency is not the phase's
correctness target. SERIALIZABLE transaction semantics and the mandatory
concurrency test are intentionally reserved for Phase 4 grant issuance,
where two agents can race for the last contact slot.