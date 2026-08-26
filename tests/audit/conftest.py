"""Shared fixtures for tests/audit/**.

**Schema isolation (Phase 5B, post-U-1).** `audit_events` is now genuinely
append-only in production: `sampark/audit/schema_proposal.sql` (U-1) has
been owner-applied to the real database — `seq`, `UNIQUE(prev_hash)`, and
the `audit_events_no_update` / `_no_delete` / `_no_truncate` triggers all
exist on `public.audit_events` (verified live: 4 indexes, 3 triggers).
That means the PRE-U-1 test strategy this file used — insert rows, track
their `event_id`s, `DELETE ... WHERE event_id = ANY(...)` at teardown —
is now structurally impossible: the trigger rejects the DELETE, exactly
as designed, exactly as several tests now (correctly) prove.

**IMPORTANT / KNOWN ISSUE.** Before this fixture redesign, the previous
Phase 5B session's tests ran against real `public.audit_events` with no
isolation. Once U-1 landed, `chain.append()` started succeeding for
real, and the old DELETE-based teardown could no longer run — leaving
**558 real, permanent rows** in `public.audit_events` (all synthetic
`request.received` test fixtures — `audit-chain-test-*`,
`linkage-test-*`, etc.; verified via `SELECT event_type, count(*) ...
GROUP BY event_type` and a payload sample). This file's redesign
prevents any FUTURE test run from adding to that, but per this phase's
explicit instruction ("Do not delete production audit rows manually")
the existing 558 rows are NOT touched here — see the Phase 5B report
for exactly what to inspect if the owner wants to deal with them
directly (this table has no PII and no secrets — see
tests/audit/test_privacy.py — so they are inert, just untidy).

**The fix: one throwaway schema per test, dropped via `DROP SCHEMA ...
CASCADE` at teardown.** `DROP SCHEMA` is DDL, not `DELETE`/`TRUNCATE` —
it is not intercepted by a `BEFORE DELETE`/`BEFORE TRUNCATE` row/
statement trigger at all, so cleanup remains fully compatible with (and
never weakens, disables, or bypasses) the append-only invariant. The
schema gets an exact structural copy of `audit_events` — base columns
(sampark/schema.sql) plus the U-1 additions (schema_proposal.sql),
including its OWN copy of the append-only trigger function/triggers, so
every append-only guarantee under test is enforced by the SAME
mechanism as production, just in a disposable namespace. Each test's
connection gets `SET search_path TO <schema>, public`, so every
unqualified `audit_events` reference in `sampark.audit.chain` / `.store`
/ `.export` transparently resolves to the isolated copy — with ZERO
changes to that production code. `grants` (used only by
`verify_chain`'s reconciliation join) is NOT duplicated in the test
schema, so it falls through `search_path`'s second entry to the REAL
`public.grants` — deliberately: the reconciliation logic under test
should read real grant data, not a synthetic copy (see
test_failure_semantics.py's T-18 for how that's handled without
asserting anything about grants this test suite does not own).

A useful side effect: each test now gets a genuinely EMPTY chain (no
more "is `head()` None or not" branching to survive across shared
state) — several tests were simplified accordingly.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from sim.persistence import PostgresConfig, PostgresConfigError


def _connect_or_skip() -> psycopg.Connection:
    try:
        config = PostgresConfig.from_env()
    except PostgresConfigError as exc:
        pytest.skip(f"Postgres not configured: {exc}")
    try:
        conn = psycopg.connect(config.conninfo(), connect_timeout=3)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres not reachable: {exc}")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.audit_events')")
        if cur.fetchone()[0] is None:
            conn.close()
            pytest.skip("audit_events table does not exist on this database")
    return conn


def new_schema_name() -> str:
    return f"sampark_audit_test_{uuid.uuid4().hex[:16]}"


def create_isolated_audit_schema(conn: psycopg.Connection, schema_name: str) -> None:
    """Structural copy of `audit_events` AS IT EXISTS IN PRODUCTION TODAY
    (base table, sampark/schema.sql + the U-1 additions,
    sampark/audit/schema_proposal.sql) — same column set, same three
    unique-ish constructs (`event_id` PK, `seq` unique, `prev_hash`
    unique), same append-only trigger behavior. Index/trigger names are
    deliberately the SAME base names production uses (verified against
    the live database: `audit_events_pkey`, `audit_events_seq_uniq`,
    `audit_events_prev_hash_uniq`, `audit_events_no_update`, `_no_delete`,
    `_no_truncate`) so a test asserting on `exc.diag.constraint_name`
    checks the SAME name it would see against production — schema-scoped
    object names don't collide with `public`'s even when identical."""
    with conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA {schema_name}")
        cur.execute(
            f"CREATE TABLE {schema_name}.audit_events ("
            "event_id UUID PRIMARY KEY, event_type TEXT NOT NULL, "
            "occurred_at TIMESTAMPTZ NOT NULL, prev_hash TEXT NOT NULL, "
            "agent_signature TEXT, reason_code TEXT, payload JSONB NOT NULL)"
        )
        cur.execute(f"ALTER TABLE {schema_name}.audit_events ADD COLUMN seq BIGSERIAL NOT NULL")
        cur.execute(f"CREATE UNIQUE INDEX audit_events_seq_uniq ON {schema_name}.audit_events (seq)")
        cur.execute(f"CREATE UNIQUE INDEX audit_events_prev_hash_uniq ON {schema_name}.audit_events (prev_hash)")
        cur.execute(
            f"CREATE FUNCTION {schema_name}.sampark_audit_immutable() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
            "'audit_events is append-only (attempted %)', TG_OP USING ERRCODE = 'raise_exception'; END; $$"
        )
        cur.execute(
            f"CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON {schema_name}.audit_events "
            f"FOR EACH ROW EXECUTE FUNCTION {schema_name}.sampark_audit_immutable()"
        )
        cur.execute(
            f"CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON {schema_name}.audit_events "
            f"FOR EACH ROW EXECUTE FUNCTION {schema_name}.sampark_audit_immutable()"
        )
        cur.execute(
            f"CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON {schema_name}.audit_events "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {schema_name}.sampark_audit_immutable()"
        )


def drop_isolated_audit_schema(conn: psycopg.Connection, schema_name: str) -> None:
    """DDL, not DML — never intercepted by the append-only triggers this
    same schema's `audit_events` copy carries. This is the whole point:
    cleanup that is compatible with (never weakens, disables, or
    bypasses) append-only semantics."""
    with conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")


@pytest.fixture()
def pg_raw_conn():
    conn = _connect_or_skip()
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def _audit_schema(pg_raw_conn):
    """Function-scoped: every test gets its OWN fresh schema (hence its
    own genuinely empty chain — see module docstring's "useful side
    effect"). Cost is one extra CREATE SCHEMA/DROP SCHEMA round trip per
    Postgres-marked audit test; negligible at this suite's size."""
    schema_name = new_schema_name()
    create_isolated_audit_schema(pg_raw_conn, schema_name)
    with pg_raw_conn.cursor() as cur:
        cur.execute(f"SET search_path TO {schema_name}, public")
    try:
        yield schema_name
    finally:
        drop_isolated_audit_schema(pg_raw_conn, schema_name)


@pytest.fixture()
def pg_conn(pg_raw_conn, _audit_schema):
    """The connection tests use directly — `_audit_schema` has already
    created the isolated `audit_events` copy and pointed this
    connection's `search_path` at it before the test body runs."""
    return pg_raw_conn


@pytest.fixture()
def audit_schema_name(_audit_schema):
    """The isolated schema's name, for tests that open ADDITIONAL
    connections of their own (e.g. the 50-way concurrency test) and need
    to point each one at the SAME isolated `audit_events` copy `pg_conn`
    uses, rather than the real `public.audit_events`."""
    return _audit_schema
