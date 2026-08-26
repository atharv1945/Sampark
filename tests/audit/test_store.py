"""sampark.audit.store query correctness — real PostgreSQL.

Post-U-1 (Phase 5B): now that the migration is owner-applied, these
tests insert through the REAL `sampark.audit.chain.append()` rather than
a raw-SQL bypass. The pre-U-1 version of this file built each event's
`prev_hash` by hand (`model_copy(update={"prev_hash": "0"*64})` for
EVERY row) precisely because `append()` was blocked — but once
`UNIQUE(prev_hash)` became real, giving every row the SAME hard-coded
GENESIS hash meant only the FIRST insert in any given test could ever
succeed; every subsequent one collided. Using `append()` directly
removes the whole problem: `emit.event_for_*` already produces a draft
with `prev_hash=PENDING_PREV_HASH`, exactly what `append()` requires,
and `append()` derives the correct real value from the current head
itself — no test needs to know or compute a single hash by hand.

Runs against the isolated per-test schema (conftest.py) — `grants` still
resolves to the real `public.grants` via search_path fallthrough, but
none of these tests touch it (they exercise store.py's targeted reads,
not verify_chain's reconciliation — see test_failure_semantics.py's T-18
for that).

Covers the read half of T-25 (competitor reconstruction) against real
rows, complementing tests/audit/test_explain.py's in-memory version.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from sampark.allocator.candidate import build_candidate
from sampark.allocator.outcomes import AllocationOutcome, OutcomeKind
from sampark.audit import emit, store
from sampark.audit.chain import Appended, append
from sampark.audit.explain import explain_contested_window, explain_request
from sampark.contracts import Grant, GrantRequest, GrantState, RiskItem

pytestmark = pytest.mark.postgres

ISSUED_AT = dt.datetime(2025, 9, 10, 6, 0, tzinfo=dt.timezone.utc)


def _request(**overrides) -> GrantRequest:
    fields = dict(
        request_id=uuid.uuid4(), agent_id="cart_recovery_agent", customer_id=f"audit-store-test-cust-{uuid.uuid4().hex[:8]}",
        risk_id=f"audit-store-test-risk-{uuid.uuid4().hex[:8]}", intent="cart_recovery",
        requested_channel="whatsapp", requested_max_incentive_bps=500, issued_at=ISSUED_AT, signature="sig",
    )
    fields.update(overrides)
    return GrantRequest(**fields)


def _candidate(request):
    item = RiskItem(risk_id=request.risk_id, source="abandoned_checkout", amount_paise=500_000,
                     root_cause="price_hesitation", detected_at=ISSUED_AT)
    return build_candidate(request, item, request.customer_id, dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc))


def _append(conn, event) -> None:
    result = append(conn, event)
    assert isinstance(result, Appended), f"expected a fresh append, got {result!r}"


def test_events_for_request_returns_the_full_timeline_in_order(pg_conn):
    request = _request()
    candidate = _candidate(request)
    grant = Grant(
        grant_id=uuid.uuid4(), channel="whatsapp", incentive_ceiling_paise=25_000,
        send_after=candidate.proposed_send_after, expires_at=candidate.proposed_send_after + dt.timedelta(hours=2),
        state=GrantState.RESERVED,
    )
    outcome = AllocationOutcome(
        candidate=candidate, outcome_kind=OutcomeKind.GRANTED, reason_code=None, next_eligible_at=None,
        grant=grant, fact_unavailable_reason_codes=(), score=None, rescheduled_candidate=None,
        effective_incentive_bps=500,
    )
    received = emit.event_for_request_received(request)
    reserved = emit.event_for_grant_reserved(outcome, uuid.uuid4(), uuid.uuid4())
    executing = emit.event_for_grant_executing(grant, request, grant.send_after)

    for e in (received, reserved, executing):
        _append(pg_conn, e)

    fetched = store.events_for_request(pg_conn, request.request_id)
    assert [e.event_type for e in fetched] == ["request.received", "grant.reserved", "grant.executing"]

    # And the whole thing reconstructs cleanly through explain_request —
    # proving store.py's output is exactly what sampark.audit.explain expects.
    explanation = explain_request(fetched)
    assert explanation.outcome == "GRANTED"


def test_events_for_request_is_scoped_to_one_request_id(pg_conn):
    request_a = _request()
    request_b = _request()
    _append(pg_conn, emit.event_for_request_received(request_a))
    _append(pg_conn, emit.event_for_request_received(request_b))

    fetched = store.events_for_request(pg_conn, request_a.request_id)
    assert len(fetched) == 1
    assert fetched[0].payload["request_id"] == str(request_a.request_id)


def test_events_for_grant_finds_the_reservation_and_lifecycle(pg_conn):
    request = _request()
    candidate = _candidate(request)
    grant = Grant(
        grant_id=uuid.uuid4(), channel="whatsapp", incentive_ceiling_paise=25_000,
        send_after=candidate.proposed_send_after, expires_at=candidate.proposed_send_after + dt.timedelta(hours=2),
        state=GrantState.RESERVED,
    )
    outcome = AllocationOutcome(
        candidate=candidate, outcome_kind=OutcomeKind.GRANTED, reason_code=None, next_eligible_at=None,
        grant=grant, fact_unavailable_reason_codes=(), score=None, rescheduled_candidate=None,
        effective_incentive_bps=500,
    )
    _append(pg_conn, emit.event_for_grant_reserved(outcome, uuid.uuid4(), uuid.uuid4()))
    _append(pg_conn, emit.event_for_grant_confirmed(grant, request, grant.send_after, actual_spend_paise=20_000))

    fetched = store.events_for_grant(pg_conn, grant.grant_id)
    assert [e.event_type for e in fetched] == ["grant.reserved", "grant.confirmed"]


def test_events_for_customer_window_reconstructs_a_contested_round(pg_conn):
    customer_id = f"audit-store-window-cust-{uuid.uuid4().hex[:8]}"
    window_id = dt.date(2025, 9, 10)
    send_after = dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc)

    winner_request = _request(customer_id=customer_id, agent_id="mandate_recovery_agent", intent="mandate_retry")
    loser_request = _request(customer_id=customer_id, agent_id="cart_recovery_agent")

    winner_candidate = _candidate(winner_request)
    loser_candidate = _candidate(loser_request)
    assert winner_candidate.window_id == window_id and loser_candidate.window_id == window_id

    grant = Grant(
        grant_id=uuid.uuid4(), channel="whatsapp", incentive_ceiling_paise=8_200,
        send_after=send_after, expires_at=send_after + dt.timedelta(hours=2), state=GrantState.RESERVED,
    )
    winner_outcome = AllocationOutcome(
        candidate=winner_candidate, outcome_kind=OutcomeKind.GRANTED, reason_code=None, next_eligible_at=None,
        grant=grant, fact_unavailable_reason_codes=(), score=None, rescheduled_candidate=None,
        effective_incentive_bps=200,
    )
    loser_outcome = AllocationOutcome(
        candidate=loser_candidate, outcome_kind=OutcomeKind.DEFERRED, reason_code="allocation.lost_to_higher_expected_net",
        next_eligible_at=dt.datetime(2025, 9, 11, 9, 0, tzinfo=dt.timezone.utc), grant=None,
        fact_unavailable_reason_codes=(), score=None, rescheduled_candidate=None,
    )
    _append(pg_conn, emit.event_for_grant_reserved(winner_outcome, uuid.uuid4(), uuid.uuid4()))
    _append(pg_conn, emit.event_for_decision(loser_outcome, send_after))

    fetched = store.events_for_customer_window(pg_conn, customer_id, window_id)
    assert len(fetched) == 2

    summary = explain_contested_window(fetched)
    assert summary.winner.agent_id == "mandate_recovery_agent"
    assert len(summary.losers) == 1 and summary.losers[0].agent_id == "cart_recovery_agent"


def test_events_for_agent_finds_only_that_agents_events(pg_conn):
    agent_a_request = _request(agent_id="cart_recovery_agent")
    agent_b_request = _request(agent_id="mandate_recovery_agent")
    _append(pg_conn, emit.event_for_request_received(agent_a_request))
    _append(pg_conn, emit.event_for_request_received(agent_b_request))

    fetched = store.events_for_agent(pg_conn, "cart_recovery_agent")
    assert all(e.payload["agent_id"] == "cart_recovery_agent" for e in fetched)
    assert any(e.payload["request_id"] == str(agent_a_request.request_id) for e in fetched)
    assert not any(e.payload["request_id"] == str(agent_b_request.request_id) for e in fetched)
