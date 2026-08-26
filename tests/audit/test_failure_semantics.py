"""T-16..T-18 — failure semantics (Phase 5A §8).

Post-U-1 (Phase 5B): runs against the isolated per-test schema (see
conftest.py) — the graceful "migration might be missing" scaffolding the
pre-U-1 version needed is gone.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from sampark.audit.chain import (
    PENDING_PREV_HASH,
    AlreadyAppended,
    Appended,
    append,
    head,
    verify_chain,
)
from sampark.contracts import AuditEvent

pytestmark = pytest.mark.postgres


def _draft_event(**overrides) -> AuditEvent:
    fields = dict(
        event_id=uuid.uuid4(), event_type="request.received",
        occurred_at=dt.datetime(2025, 9, 10, 9, 0, 0, tzinfo=dt.timezone.utc),
        prev_hash=PENDING_PREV_HASH, agent_signature="sig",
        reason_code=None, payload={"v": 1, "request_id": f"failure-sem-{uuid.uuid4().hex[:12]}"},
    )
    fields.update(overrides)
    return AuditEvent(**fields)


def test_rolled_back_transaction_leaves_no_audit_event(pg_conn):
    # T-16: append() participates in whatever transaction state `conn`
    # is currently in (Phase 5A §8.2's "transaction-agnostic" design).
    # Opening an explicit transaction, appending, then rolling back MUST
    # leave the chain exactly as it was — the appended event must not
    # exist and the head must be unchanged. A rollback is not a DELETE,
    # so this is unaffected by (and does not test) the append-only
    # trigger — it tests that append() never commits work it didn't mean
    # to.
    draft = _draft_event()
    before = head(pg_conn)
    assert before is None  # isolated schema starts empty

    pg_conn.autocommit = False
    try:
        try:
            append(pg_conn, draft)
        finally:
            pg_conn.rollback()
    finally:
        pg_conn.autocommit = True

    with pg_conn.cursor() as cur:
        cur.execute("SELECT 1 FROM audit_events WHERE event_id = %s", (draft.event_id,))
        assert cur.fetchone() is None, "a rolled-back append must leave no row"

    after = head(pg_conn)
    assert after is None, "the chain head must be unchanged (still empty) after a rolled-back append"


def test_retry_after_success_is_idempotent_not_a_duplicate(pg_conn):
    # T-17
    draft = _draft_event()
    first = append(pg_conn, draft)
    assert isinstance(first, Appended)

    retry = draft.model_copy(update={"prev_hash": PENDING_PREV_HASH})
    second = append(pg_conn, retry)
    assert isinstance(second, AlreadyAppended)

    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM audit_events WHERE event_id = %s", (draft.event_id,))
        assert cur.fetchone()[0] == 1


# --- T-18: grant-reservation reconciliation ---------------------------------
#
# `verify_chain`'s reconciliation joins the (isolated, test-only)
# audit_events schema against the REAL, shared `public.grants` (see
# conftest.py's module docstring — `grants` is deliberately NOT
# duplicated into the isolated schema, so unqualified references to it
# fall through to `public`). This database is mid-way through Phase 4's
# live evidence matrix, which legitimately has real grants with ZERO
# audit coverage (Phase 4 does not write any audit events yet — U-2 is
# not wired). Asserting `missing_grant_reservations == ()` globally would
# therefore be asserting a fact about production data this test suite
# does not own and cannot control — not what T-18 is supposed to prove.
#
# Instead: insert two SYNTHETIC, test-owned grants (full FK chain:
# agent, customer, risk_item, grant_request, grant — mirroring
# tests/test_concurrent_grant_issuance.py's RaceFixture pattern), give
# ONE of them a matching grant.reserved audit event via the real
# append(), and assert the reconciliation query correctly classifies
# both: the covered one excluded, the uncovered one included. This tests
# the ACTUAL reconciliation logic without asserting anything about
# grants this suite does not own. `grants`/`grant_requests`/`risk_items`/
# etc. carry NO append-only trigger (only audit_events does), so cleanup
# here is ordinary DELETE, exactly like every other Phase 4 Postgres
# fixture in this repo (tests/budget/conftest.py's `pg_env`,
# tests/test_concurrent_grant_issuance.py's `RaceFixture`).

_WINDOW_ID = dt.date(2099, 1, 1)  # far future — will not collide with any real evidence-run window
_SEND_AFTER = dt.datetime(2099, 1, 1, 12, 0, tzinfo=dt.timezone.utc)
_EXPIRES_AT = dt.datetime(2099, 1, 1, 14, 0, tzinfo=dt.timezone.utc)
_DETECTED_AT = dt.datetime(2099, 1, 1, 6, 0, tzinfo=dt.timezone.utc)


def _insert_synthetic_grant(cur, agent_id: str, customer_id: str, label: str) -> tuple[uuid.UUID, uuid.UUID]:
    risk_id = f"audit-t18-risk-{label}-{uuid.uuid4().hex[:8]}"
    request_id = uuid.uuid4()
    grant_id = uuid.uuid4()
    cur.execute(
        "INSERT INTO risk_items (risk_id, customer_id, source, amount_paise, root_cause, detected_at) "
        "VALUES (%s, %s, 'abandoned_checkout', 100000, 'price_hesitation', %s)",
        (risk_id, customer_id, _DETECTED_AT),
    )
    cur.execute(
        "INSERT INTO grant_requests (request_id, agent_id, customer_id, risk_id, intent, "
        "requested_channel, requested_max_incentive_bps, issued_at, signature) "
        "VALUES (%s, %s, %s, %s, 'cart_recovery', 'whatsapp', 0, %s, 'sig')",
        (request_id, agent_id, customer_id, risk_id, _DETECTED_AT),
    )
    cur.execute(
        "SELECT budget_window_id FROM budget_windows WHERE merchant_id = 'merchant-sim' AND window_id = %s",
        (_WINDOW_ID,),
    )
    budget_window_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO grants (grant_id, request_id, budget_window_id, channel, incentive_ceiling_paise, "
        "send_after, expires_at, state) VALUES (%s, %s, %s, 'whatsapp', 0, %s, %s, 'RESERVED')",
        (grant_id, request_id, budget_window_id, _SEND_AFTER, _EXPIRES_AT),
    )
    return grant_id, request_id


@pytest.fixture()
def synthetic_grants(pg_raw_conn):
    """Two FK-satisfying real grants in the REAL public.grants (a
    "covered" one and an "uncovered" one — see module docstring above).
    `pg_raw_conn` (not `pg_conn`) deliberately — this fixture writes to
    `public.*` Phase 4 tables directly, unaffected by search_path/schema
    isolation, and is torn down with ordinary DELETE (none of these
    tables carry an append-only trigger)."""
    conn = pg_raw_conn
    suffix = uuid.uuid4().hex[:10]
    agent_id = f"audit-t18-agent-{suffix}"
    customer_id = f"audit-t18-cust-{suffix}"

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agents (agent_id, public_key, publisher, state, strike_count) "
            "VALUES (%s, 'pk', 'audit-test', 'ACTIVE', 0)",
            (agent_id,),
        )
        cur.execute(
            "INSERT INTO capability_scopes (agent_id, allowed_channels, allowed_intents, "
            "allowed_risk_sources, max_incentive_bps, max_requests_per_hour) "
            "VALUES (%s, '[\"whatsapp\"]', '[\"cart_recovery\"]', '[\"abandoned_checkout\"]', 0, 100000)",
            (agent_id,),
        )
        cur.execute("INSERT INTO customers (customer_id) VALUES (%s)", (customer_id,))
        cur.execute(
            "INSERT INTO contact_states (customer_id, contacts_24h, contacts_7d, "
            "optouts_by_channel, consent_scopes, fatigue_score) VALUES (%s, 0, 0, '{}', '{}', 0.0)",
            (customer_id,),
        )
        cur.execute(
            "SELECT 1 FROM budget_windows WHERE merchant_id = 'merchant-sim' AND window_id = %s", (_WINDOW_ID,)
        )
        if cur.fetchone() is None:
            cur.execute(
                "INSERT INTO budget_windows (budget_window_id, merchant_id, window_id, margin_budget_paise) "
                "VALUES (%s, 'merchant-sim', %s, 1000000)",
                (uuid.uuid4(), _WINDOW_ID),
            )

        covered_grant_id, covered_request_id = _insert_synthetic_grant(cur, agent_id, customer_id, "covered")
        uncovered_grant_id, uncovered_request_id = _insert_synthetic_grant(cur, agent_id, customer_id, "uncovered")

    try:
        yield {
            "covered": (covered_grant_id, covered_request_id),
            "uncovered": (uncovered_grant_id, uncovered_request_id),
        }
    finally:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM grants WHERE grant_id = ANY(%s)", ([covered_grant_id, uncovered_grant_id],)
            )
            cur.execute(
                "DELETE FROM grant_requests WHERE request_id = ANY(%s)",
                ([covered_request_id, uncovered_request_id],),
            )
            cur.execute("DELETE FROM risk_items WHERE customer_id = %s", (customer_id,))
            cur.execute("DELETE FROM contact_states WHERE customer_id = %s", (customer_id,))
            cur.execute("DELETE FROM customers WHERE customer_id = %s", (customer_id,))
            cur.execute("DELETE FROM agents WHERE agent_id = %s", (agent_id,))  # cascades capability_scopes


def test_verify_chain_reconciliation_detects_a_specific_missing_grant_reserved_event(pg_conn, synthetic_grants):
    # T-18 — THE exit-criterion test (Phase 5A §13, condition 2), scoped
    # to two grants this test owns (see module docstring for why a
    # global `missing_grant_reservations == ()` assertion is not
    # meaningful in this environment).
    covered_grant_id, covered_request_id = synthetic_grants["covered"]
    uncovered_grant_id, _ = synthetic_grants["uncovered"]

    draft = AuditEvent(
        event_id=uuid.uuid4(), event_type="grant.reserved",
        occurred_at=_SEND_AFTER, prev_hash=PENDING_PREV_HASH, agent_signature="sig", reason_code=None,
        payload={
            "v": 1, "grant_id": str(covered_grant_id), "request_id": str(covered_request_id),
            "agent_id": "audit-t18-agent", "customer_id": "audit-t18-cust", "risk_id": "audit-t18-risk",
            "window_id": _WINDOW_ID.isoformat(), "channel": "whatsapp", "incentive_ceiling_paise": 0,
            "effective_incentive_bps": 0, "send_after": "2099-01-01T12:00:00.000000Z",
            "expires_at": "2099-01-01T14:00:00.000000Z", "budget_window_id": str(uuid.uuid4()),
            "claim_id": str(uuid.uuid4()),
        },
    )
    result = append(pg_conn, draft)
    assert isinstance(result, Appended)

    report = verify_chain(pg_conn)

    assert covered_grant_id not in report.missing_grant_reservations, (
        "the covered grant has a matching grant.reserved event and must not be reported missing"
    )
    assert uncovered_grant_id in report.missing_grant_reservations, (
        "the uncovered grant has NO audit event and MUST be reported missing — "
        "a non-empty result here is a HARD verification failure by design (Phase 5A §8.2)"
    )
