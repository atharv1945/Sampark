# CI PostgreSQL service (U-7)

**STATUS: owner-applied to `.github/workflows/ci.yml`, with one correction
— see "Applied, with one correction" below.** This file is retained,
unmodified in its original proposal content otherwise, as the durable
record of exactly what was reviewed and applied — the same role
`PHASE4_SCHEMA_AND_ISSUANCE_PROPOSAL.md` played for Phase 4's tables and
`sampark/audit/schema_proposal.sql` played for U-1.

`.github/workflows/**` is human-owned per Phase 4 Design Lock §17.3 ("CI
needs to run the `postgres` marker. That workflow change is owner-applied;
the agent may propose a diff but must not commit one") and Phase 5A's own
frozen-files list — this proposal was reviewed before being applied.

## Applied, with one correction

By the time this was applied, `sampark/schema.sql` had already been
updated to fold U-1's DDL in directly (a separate, subsequent owner-approved
closure step). The proposal below still shows the schema-apply step
applying **both** `sampark/schema.sql` and `sampark/audit/schema_proposal.sql`
in sequence, as originally reviewed — but with U-1 now inside
`sampark/schema.sql` itself, the second `psql` invocation would fail
(`ALTER TABLE audit_events ADD COLUMN seq ...` a second time). The applied
workflow therefore runs **only** the `sampark/schema.sql` step; everything
else below (the service block, credentials, health check, env vars) was
applied exactly as proposed.

## Why this is needed

`pyproject.toml`'s own marker text says the `postgres` marker "must run in
CI, never silently skip." Measured directly: with no `POSTGRES_*`
environment variables set (i.e. exactly what CI sees today), the full suite
runs **481 passed / 64 skipped** instead of **542 passed / 3 skipped**. The
64 silent skips include:

- `tests/test_concurrent_grant_issuance.py` — both tests, including the
  real 50-way concurrent-grant-issuance race — the test CLAUDE.md §12 names
  as **the most important test in the project**.
- `tests/audit/test_failure_semantics.py`'s reconciliation test (T-18) and
  `tests/audit/test_determinism.py`'s end-to-end determinism test (T-26) —
  Phase 5's own exit-criterion tests.
- `tests/audit/test_integration.py`'s two U-8 registration-wiring tests.
- 55 more `postgres`-marked tests across `tests/audit`, `tests/arm_b`,
  `tests/budget`, `tests/registry`, `tests/sim_generator`.

A skipped exit-criterion test reads exactly like a passing one in a CI
badge. Design Lock §17.3 and Phase 5A both name this as owner-applied,
approved (U-7), and outstanding.

## Exact proposed diff to `.github/workflows/ci.yml`

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    name: pytest (Python 3.11)
    runs-on: ubuntu-latest

    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_DB: sampark
          POSTGRES_USER: sampark
          POSTGRES_PASSWORD: sampark_ci
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U sampark -d sampark"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 10

    env:
      POSTGRES_HOST: localhost
      POSTGRES_PORT: 5432
      POSTGRES_DB: sampark
      POSTGRES_USER: sampark
      POSTGRES_PASSWORD: sampark_ci

    steps:
      - name: Check out repository
        uses: actions/checkout@v4

      # Pinned to 3.11 to match the local .venv. tests/test_environment.py
      # asserts this at runtime, so a drift here fails the build loudly rather
      # than silently testing against the wrong interpreter.
      - name: Set up Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
          cache-dependency-path: requirements.txt

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          python -m pip install -r requirements.txt

      - name: Apply schema (sampark/schema.sql, then the U-1 audit migration)
        run: |
          sudo apt-get update && sudo apt-get install -y postgresql-client
          PGPASSWORD=sampark_ci psql -h localhost -U sampark -d sampark -f sampark/schema.sql
          PGPASSWORD=sampark_ci psql -h localhost -U sampark -d sampark -f sampark/audit/schema_proposal.sql

      - name: Run tests
        run: python -m pytest
```

## Notes on the exact diff

1. **Credentials are CI-local test credentials, not real secrets** — a
   fresh, ephemeral `postgres:16` service container that exists only for
   the duration of one CI run. Nothing here touches `.env` or any real
   credential (CLAUDE.md §8).
2. **The schema-apply step runs `sampark/schema.sql` first, then
   `sampark/audit/schema_proposal.sql`.** This works *today*, without
   requiring `sampark/schema.sql` to be edited first — the two files are
   applied in sequence, exactly as the owner did by hand against the local
   database. If `sampark/schema.sql` is later updated to fold U-1 in
   directly (a separate, owner-owned action — see the stale-documentation
   note in `sampark/audit/chain.py` and `sampark/audit/schema_proposal.sql`),
   the second `psql` line becomes redundant and can be dropped then, not
   before.
3. **`postgres_data`'s named-volume-vs-ephemeral distinction (docker-compose.yml)
   does not apply here** — a GitHub Actions service container has no
   persistent volume by default, which is exactly the ephemeral-per-run
   semantics CI needs (schema applied fresh every run, no drift to chase).
4. No change to `requirements.txt`, `conftest.py`, or any Python file is
   needed for this specifically — `psycopg[binary]` is already pinned.
5. This proposal does not change what the suite tests or how — it only
   gives the existing `postgres`-marked tests a database to run against in
   CI, exactly as they already run locally.

## Final state

- `sampark/schema.sql` was updated separately (a distinct, subsequent
  owner-approved closure step) to fold U-1 in directly.
- `.github/workflows/ci.yml` now has the `postgres` service, matching env
  vars, and a single schema-apply step against `sampark/schema.sql` — see
  "Applied, with one correction" above for the one deviation from the
  diff as originally reviewed.
- This file is kept as the historical record of the reviewed proposal, not
  as a to-do.
