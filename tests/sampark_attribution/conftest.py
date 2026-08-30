"""Shared fixtures for tests/sampark_attribution/**.

Isolated-schema pattern, mirroring tests/audit/conftest.py's exact
precedent: each test gets its OWN schema holding a structural copy of
`attribution_credits` (from `sampark/attribution/schema_proposal.sql`,
unmodified) so tests get real, executable Postgres evidence — including
the arithmetic CHECK constraint — WITHOUT touching the human-owned
`public` schema or `sampark/schema.sql` (CLAUDE.md §3). Cleanup is
`DROP SCHEMA ... CASCADE` (DDL), never `DELETE`/`TRUNCATE`.

`attribution_credits.grant_id REFERENCES grants(grant_id)` is left
UNQUALIFIED in the isolated copy — `search_path` set to
`<schema>, public` means it resolves to `public.grants`, so a credit row
must reference a REAL grant. This mirrors tests/audit/conftest.py's own
choice to let `grants` fall through to `public` rather than duplicating
it: the FK behavior under test should be against real grant data.
"""

from __future__ import annotations

import uuid
from datetime import date as dt_date
from datetime import datetime, timezone

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
        cur.execute("SELECT to_regclass('public.grants')")
        if cur.fetchone()[0] is None:
            conn.close()
            pytest.skip("grants table does not exist on this database")
    return conn


def new_schema_name() -> str:
    return f"sampark_attribution_test_{uuid.uuid4().hex[:16]}"


def create_isolated_attribution_schema(conn: psycopg.Connection, schema_name: str) -> None:
    with conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA {schema_name}")
        cur.execute(
            f"CREATE TABLE {schema_name}.attribution_credits ("
            "credit_id UUID PRIMARY KEY, "
            "grant_id UUID NOT NULL UNIQUE REFERENCES grants (grant_id) ON DELETE RESTRICT, "
            "observed_recovered_paise BIGINT NOT NULL, "
            "natural_rate_bps INTEGER NOT NULL, "
            "expected_natural_paise BIGINT NOT NULL, "
            "credited_recovery_paise BIGINT NOT NULL, "
            "baseline_stratum TEXT NOT NULL, "
            "baseline_level TEXT NOT NULL, "
            "baseline_holdout_n INTEGER NOT NULL, "
            "holdout_fraction_bps INTEGER NOT NULL, "
            "natural_model_version INTEGER NOT NULL, "
            "observed_at TIMESTAMPTZ NOT NULL, "
            "CONSTRAINT attribution_credits_observed_non_negative CHECK (observed_recovered_paise >= 0), "
            "CONSTRAINT attribution_credits_expected_natural_non_negative CHECK (expected_natural_paise >= 0), "
            "CONSTRAINT attribution_credits_natural_rate_range CHECK (natural_rate_bps BETWEEN 0 AND 10000), "
            "CONSTRAINT attribution_credits_holdout_fraction_range CHECK (holdout_fraction_bps BETWEEN 0 AND 10000), "
            "CONSTRAINT attribution_credits_baseline_n_positive CHECK (baseline_holdout_n > 0), "
            "CONSTRAINT attribution_credits_baseline_level_valid "
            "  CHECK (baseline_level IN ('source_root_cause', 'source', 'global')), "
            "CONSTRAINT attribution_credits_arithmetic "
            "  CHECK (credited_recovery_paise = observed_recovered_paise - expected_natural_paise)"
            ")"
        )
        cur.execute(f"CREATE INDEX idx_attribution_credits_grant_id ON {schema_name}.attribution_credits (grant_id)")


def drop_isolated_attribution_schema(conn: psycopg.Connection, schema_name: str) -> None:
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
def _attribution_schema(pg_raw_conn):
    schema_name = new_schema_name()
    create_isolated_attribution_schema(pg_raw_conn, schema_name)
    with pg_raw_conn.cursor() as cur:
        cur.execute(f"SET search_path TO {schema_name}, public")
    try:
        yield schema_name
    finally:
        drop_isolated_attribution_schema(pg_raw_conn, schema_name)


@pytest.fixture()
def pg_conn(pg_raw_conn, _attribution_schema):
    return pg_raw_conn


@pytest.fixture()
def real_grant_id(pg_conn):
    """Inserts one minimal, real, valid chain of rows
    (merchants -> budget_windows -> customers -> risk_items ->
    agents -> capability_scopes -> grant_requests -> grants ->
    contact_slot_claims) into PUBLIC so a credit row has a genuine
    grant_id to reference — never a fabricated UUID with no backing row,
    which the FK would reject anyway. Cleaned up via the SAME
    customer_id-scoped DELETE pattern sim/arm_b.py::_cleanup_postgres_run
    already uses (never touches identity tables beyond this fixture's
    own synthetic customer)."""
    customer_id = f"attribution-test-customer-{uuid.uuid4().hex[:12]}"
    risk_id = f"attribution-test-risk-{uuid.uuid4().hex[:12]}"
    agent_id = f"attribution-test-agent-{uuid.uuid4().hex[:12]}"
    request_id = uuid.uuid4()
    grant_id = uuid.uuid4()
    claim_id = uuid.uuid4()
    budget_window_id = uuid.uuid4()
    now = datetime(2025, 9, 10, 9, 0, tzinfo=timezone.utc)
    # A window_id far outside any real simulation month (Sept-Oct 2025),
    # so this fixture never collides with a concurrently-running Arm A/B
    # evidence sweep's own seeded budget_windows rows for 'merchant-sim' —
    # mirrors the existing inert '2099-01-01' test-fixture convention
    # already found in this repository's residue checks.
    window_id = dt_date(2099, 1, 1)

    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO merchants (merchant_id, display_name) VALUES ('merchant-sim', 'SAMPARK Simulation Merchant') "
            "ON CONFLICT (merchant_id) DO NOTHING"
        )
        cur.execute(
            "INSERT INTO budget_windows (budget_window_id, merchant_id, window_id, margin_budget_paise) "
            "VALUES (%s, 'merchant-sim', %s, 1000000000) ON CONFLICT (merchant_id, window_id) DO NOTHING",
            (budget_window_id, window_id),
        )
        cur.execute(
            "SELECT budget_window_id FROM budget_windows WHERE merchant_id = 'merchant-sim' AND window_id = %s",
            (window_id,),
        )
        (budget_window_id,) = cur.fetchone()
        cur.execute("INSERT INTO customers (customer_id) VALUES (%s)", (customer_id,))
        cur.execute(
            "INSERT INTO risk_items (risk_id, customer_id, source, amount_paise, root_cause, detected_at) "
            "VALUES (%s, %s, 'failed_payment', 100000, 'insufficient_funds', %s)",
            (risk_id, customer_id, now),
        )
        cur.execute(
            "INSERT INTO agents (agent_id, public_key, publisher, state) VALUES (%s, 'test-key', 'test', 'ACTIVE')",
            (agent_id,),
        )
        cur.execute(
            "INSERT INTO grant_requests (request_id, agent_id, customer_id, risk_id, intent, "
            "requested_channel, requested_max_incentive_bps, issued_at, signature) "
            "VALUES (%s, %s, %s, %s, 'payment_retry', 'sms', 0, %s, 'test-sig')",
            (request_id, agent_id, customer_id, risk_id, now),
        )
        cur.execute(
            "INSERT INTO grants (grant_id, request_id, channel, incentive_ceiling_paise, send_after, "
            "expires_at, state, budget_window_id) "
            "VALUES (%s, %s, 'sms', 0, %s, %s, 'CONFIRMED', %s)",
            (grant_id, request_id, now, now.replace(hour=11), budget_window_id),
        )
        cur.execute(
            "INSERT INTO contact_slot_claims (claim_id, customer_id, window_id, grant_id, state, claimed_at) "
            "VALUES (%s, %s, %s, %s, 'CONFIRMED', %s)",
            (claim_id, customer_id, window_id, grant_id, now),
        )

    try:
        yield grant_id
    finally:
        # Pytest tears down fixtures in REVERSE dependency order, so this
        # runs BEFORE _attribution_schema's DROP SCHEMA — the isolated
        # attribution_credits table (ON DELETE RESTRICT against grants)
        # still exists here. Delete any credit row FIRST (unqualified name
        # resolves via search_path, set to <schema>, public, to the
        # isolated copy), or the grants delete below would fail exactly
        # the way the Phase 6 disk-full incident's orphan-FK bug did.
        with pg_conn.cursor() as cur:
            cur.execute("DELETE FROM attribution_credits WHERE grant_id = %s", (grant_id,))
            cur.execute("DELETE FROM contact_slot_claims WHERE customer_id = %s", (customer_id,))
            cur.execute("DELETE FROM grants WHERE grant_id = %s", (grant_id,))
            cur.execute("DELETE FROM grant_requests WHERE request_id = %s", (request_id,))
            cur.execute("DELETE FROM agents WHERE agent_id = %s", (agent_id,))
            cur.execute("DELETE FROM risk_items WHERE risk_id = %s", (risk_id,))
            cur.execute("DELETE FROM customers WHERE customer_id = %s", (customer_id,))
            cur.execute("DELETE FROM budget_windows WHERE budget_window_id = %s", (budget_window_id,))
