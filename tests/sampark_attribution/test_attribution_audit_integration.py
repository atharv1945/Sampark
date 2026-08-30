"""sampark.attribution.store.insert_credit + sampark.audit — Phase 7
(spec §8.9) end-to-end integration: the ACTUAL attribution operation
that creates a credit also appends the matching `recovery.credited`
audit event, real PostgreSQL, real audit chain (an isolated schema copy
of `audit_events`, layered onto the SAME connection `real_grant_id`
already uses — never touching `public.audit_events`'s real 560+ rows).

Deliberately a SINGLE minimal case (one grant, one credit), not a full
simulation run — this proves the WIRING, not the evidence at scale; the
mechanism-at-scale evidence already exists in
tests/sim_arm_b_holdout/test_arm_b_holdout_audit_wiring.py (holdout.assigned
/ contact.opt_out) and tests/sampark_attribution/test_store.py (the
ledger's own constraints). Kept minimal specifically to avoid adding
Postgres load while another evidence run may still be in flight
(Phase 7 closure session, Part 13).
"""

from __future__ import annotations

import datetime as dt

import pytest
from psycopg.rows import dict_row

from sampark.attribution.baseline import BaselineRate
from sampark.attribution.credit import compute_credit
from sampark.attribution.store import insert_credit
from sampark.audit.chain import all_events_ordered
from sampark.audit.sink import PostgresAuditSink
from sampark.contracts import GrantRequest
from tests.audit.conftest import create_isolated_audit_schema, drop_isolated_audit_schema, new_schema_name

pytestmark = pytest.mark.postgres


@pytest.fixture()
def audit_layered_conn(pg_conn):
    """Layers an isolated `audit_events` copy ONTO `pg_conn` (which
    already has the isolated `attribution_credits` copy active on its
    search_path via `real_grant_id`'s own fixture chain) — one
    connection, both isolated tables, `grants` still falling through to
    the real `public.grants` row `real_grant_id` created."""
    schema_name = new_schema_name()
    create_isolated_audit_schema(pg_conn, schema_name)
    with pg_conn.cursor() as cur:
        cur.execute("SHOW search_path")
        (current,) = cur.fetchone()
        cur.execute(f"SET search_path TO {schema_name}, {current}")
    try:
        yield pg_conn
    finally:
        drop_isolated_audit_schema(pg_conn, schema_name)


def _real_request_for_grant(conn, grant_id) -> GrantRequest:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT gr.request_id, gr.agent_id, gr.customer_id, gr.risk_id, gr.intent, "
            "gr.requested_channel, gr.requested_max_incentive_bps, gr.issued_at, gr.signature "
            "FROM grant_requests gr JOIN grants g ON g.request_id = gr.request_id "
            "WHERE g.grant_id = %s",
            (grant_id,),
        )
        row = cur.fetchone()
    return GrantRequest(
        request_id=row["request_id"], agent_id=row["agent_id"], customer_id=row["customer_id"],
        risk_id=row["risk_id"], intent=row["intent"], requested_channel=row["requested_channel"],
        requested_max_incentive_bps=row["requested_max_incentive_bps"], issued_at=row["issued_at"],
        signature=row["signature"],
    )


def test_insert_credit_emits_a_matching_recovery_credited_event(audit_layered_conn, real_grant_id):
    conn = audit_layered_conn
    request = _real_request_for_grant(conn, real_grant_id)

    baseline = BaselineRate(stratum="failed_payment.insufficient_funds", level="source_root_cause", rate=0.05, n=317)
    credit = compute_credit(
        grant_id=real_grant_id, observed_recovered_paise=100_000, amount_paise=100_000,
        baseline=baseline, holdout_fraction=0.10,
        observed_at=dt.datetime(2025, 10, 9, tzinfo=dt.timezone.utc),
    )

    sink = PostgresAuditSink(conn)
    result = insert_credit(conn, credit, request=request, audit_sink=sink)
    assert result.credit_id == credit.credit_id

    events = all_events_ordered(conn)
    credited_events = [e for e in events if e.event_type == "recovery.credited"]
    assert len(credited_events) == 1
    event = credited_events[0]
    assert event.payload["grant_id"] == str(real_grant_id)
    assert event.payload["credit_id"] == str(credit.credit_id)
    assert event.payload["credited_recovery_paise"] == credit.credited_recovery_paise
    assert event.agent_signature == request.signature


def test_retrying_insert_credit_does_not_duplicate_the_audit_event(audit_layered_conn, real_grant_id):
    conn = audit_layered_conn
    request = _real_request_for_grant(conn, real_grant_id)

    baseline = BaselineRate(stratum="global", level="global", rate=0.05, n=1000)
    credit = compute_credit(
        grant_id=real_grant_id, observed_recovered_paise=0, amount_paise=100_000,
        baseline=baseline, holdout_fraction=0.10,
        observed_at=dt.datetime(2025, 10, 9, tzinfo=dt.timezone.utc),
    )

    sink = PostgresAuditSink(conn)
    insert_credit(conn, credit, request=request, audit_sink=sink)
    insert_credit(conn, credit, request=request, audit_sink=sink)  # retry, identical credit

    events = all_events_ordered(conn)
    credited_events = [e for e in events if e.event_type == "recovery.credited"]
    assert len(credited_events) == 1  # idempotent — never a duplicate append


def test_no_audit_event_when_sink_is_omitted(audit_layered_conn, real_grant_id):
    conn = audit_layered_conn
    baseline = BaselineRate(stratum="global", level="global", rate=0.05, n=1000)
    credit = compute_credit(
        grant_id=real_grant_id, observed_recovered_paise=0, amount_paise=100_000,
        baseline=baseline, holdout_fraction=0.10,
        observed_at=dt.datetime(2025, 10, 9, tzinfo=dt.timezone.utc),
    )
    insert_credit(conn, credit)  # no request, no audit_sink — the pre-Phase-7-wiring default
    events = all_events_ordered(conn)
    assert [e for e in events if e.event_type == "recovery.credited"] == []
