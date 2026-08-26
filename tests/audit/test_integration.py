"""U-2 integration test — real Phase 4 execution -> real AllocationOutcome
-> real Phase 5 audit emission -> real PostgreSQL persistence -> valid
hash chain -> explain/export.

This is NOT an emitter-in-isolation test (tests/audit/test_emit.py
already covers that with hand-built objects). Every object here comes
from the REAL, unmodified Phase 4 code path:

    sampark.registry.store.PostgresAgentRepository   (real signature
                                                        verification)
    sampark.registry.store.PostgresRiskItemRepository
    sampark.budget.postgres_ledger.PostgresMediationLedger
    sampark.budget.issuance.PostgresGrantIssuer       (the real,
                                                        human-owned
                                                        SERIALIZABLE
                                                        transaction)
    sampark.mediation.service.mediate_window          (the real
                                                        decision path,
                                                        now with U-2's
                                                        audit_sink wired)

against real rows in `public.agents` / `customers` / `risk_items` /
`grants` / `contact_slot_claims` (Phase 4's own tables — untouched code,
untouched schema), with `sampark.audit.sink.PostgresAuditSink` pointed
at the isolated per-test `audit_events` schema (conftest.py's
established pattern — repeatable, leaves no residue). Phase 4's rows are
cleaned up via ordinary DELETE at teardown (those tables carry no
append-only trigger); the isolated audit schema is dropped wholesale.

A SEPARATE, one-off manual demonstration against the REAL, shared
`public.audit_events` (satisfying "real PostgreSQL persistence" /
"the live audit store" literally) is documented in the Phase 5 report
rather than run here on every test invocation — see that report for the
exact commands and their output.
"""

from __future__ import annotations

import datetime as dt
import io
import uuid

import pytest

from sampark.allocator.constants import IST
from sampark.audit import chain, export, explain, store
from sampark.audit.event_types import (
    DECISION_DEFERRED,
    GRANT_RESERVED,
    REQUEST_DENIED_ON_SCOPE,
    REQUEST_RECEIVED,
)
from sampark.audit.sink import PostgresAuditSink
from sampark.budget.postgres_ledger import PostgresMediationLedger
from sampark.budget.issuance import PostgresGrantIssuer
from sampark.contracts import Agent, AgentState, CapabilityScope, GrantRequest
from sampark.mediation.service import mediate_window
from sampark.registry.keys import generate_keypair
from sampark.registry.store import PostgresAgentRepository, PostgresRiskItemRepository

pytestmark = pytest.mark.postgres

MERCHANT_BUDGET_PAISE = 1_000_000_000
DETECTED_AT = dt.datetime(2025, 9, 10, 6, 0, tzinfo=dt.timezone.utc)
DECISION_AT = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)  # 09:00 IST-band, not quiet hours
SEND_AFTER = dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc)


@pytest.fixture()
def phase4_fixture(pg_raw_conn):
    """Real agent + customer + two risk_items in the REAL public.* Phase
    4 tables — one risk_item drives a GRANTED path, the other an
    out-of-scope (voice, never declared) request that the Registry must
    deny before the allocator ever runs. `pg_raw_conn` deliberately (not
    `pg_conn`): these writes must land in `public`, not the isolated
    audit schema."""
    conn = pg_raw_conn
    suffix = uuid.uuid4().hex[:10]
    agent_id = f"audit-it-agent-{suffix}"
    customer_id = f"audit-it-cust-{suffix}"
    granted_risk_id = f"audit-it-risk-granted-{suffix}"
    scope_denied_risk_id = f"audit-it-risk-scopedenied-{suffix}"

    keypair = generate_keypair()
    agent_repo = PostgresAgentRepository(conn)
    agent = Agent(agent_id=agent_id, public_key=keypair.public_key_b64, publisher="audit-integration-test",
                  state=AgentState.ACTIVE, strike_count=0)
    scope = CapabilityScope(
        allowed_channels=["whatsapp"], allowed_intents=["cart_recovery"],
        allowed_risk_sources=["abandoned_checkout"], max_incentive_bps=500, max_requests_per_hour=1000,
    )
    agent_repo.register(agent, scope)

    with conn.cursor() as cur:
        cur.execute("INSERT INTO customers (customer_id) VALUES (%s)", (customer_id,))
        cur.execute(
            "INSERT INTO contact_states (customer_id, contacts_24h, contacts_7d, "
            "optouts_by_channel, consent_scopes, fatigue_score) VALUES (%s, 0, 0, '{}', '{}', 0.0)",
            (customer_id,),
        )
        for risk_id in (granted_risk_id, scope_denied_risk_id):
            cur.execute(
                "INSERT INTO risk_items (risk_id, customer_id, source, amount_paise, root_cause, detected_at) "
                "VALUES (%s, %s, 'abandoned_checkout', 1000000, 'price_hesitation', %s)",
                (risk_id, customer_id, DETECTED_AT),
            )

    try:
        yield {
            "agent_id": agent_id, "customer_id": customer_id, "keypair": keypair,
            "granted_risk_id": granted_risk_id, "scope_denied_risk_id": scope_denied_risk_id,
        }
    finally:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM contact_slot_claims WHERE customer_id = %s", (customer_id,)
            )
            cur.execute(
                "DELETE FROM grants WHERE request_id IN "
                "(SELECT request_id FROM grant_requests WHERE customer_id = %s)",
                (customer_id,),
            )
            cur.execute("DELETE FROM grant_requests WHERE customer_id = %s", (customer_id,))
            cur.execute("DELETE FROM customer_margin_windows WHERE customer_id = %s", (customer_id,))
            cur.execute("DELETE FROM risk_items WHERE customer_id = %s", (customer_id,))
            cur.execute("DELETE FROM contact_states WHERE customer_id = %s", (customer_id,))
            cur.execute("DELETE FROM customers WHERE customer_id = %s", (customer_id,))
            cur.execute("DELETE FROM agents WHERE agent_id = %s", (agent_id,))  # cascades capability_scopes


def _signed_request(fx, risk_id: str, channel: str, intent: str = "cart_recovery", bps: int = 500) -> GrantRequest:
    unsigned = GrantRequest(
        request_id=uuid.uuid4(), agent_id=fx["agent_id"], customer_id=fx["customer_id"], risk_id=risk_id,
        intent=intent, requested_channel=channel, requested_max_incentive_bps=bps, issued_at=DETECTED_AT,
        signature="placeholder",
    )
    signature = fx["keypair"].sign(unsigned.canonical_bytes())
    return unsigned.model_copy(update={"signature": signature})


def test_real_mediation_produces_matching_audit_events(pg_conn, pg_raw_conn, phase4_fixture):
    fx = phase4_fixture
    granted_request = _signed_request(fx, fx["granted_risk_id"], channel="whatsapp")
    scope_denied_request = _signed_request(fx, fx["scope_denied_risk_id"], channel="voice")  # never declared

    agent_repo = PostgresAgentRepository(pg_raw_conn)
    risk_item_repo = PostgresRiskItemRepository(pg_raw_conn)
    run_seed_risk_ids = frozenset({fx["granted_risk_id"], fx["scope_denied_risk_id"]})
    mediation_ledger = PostgresMediationLedger(pg_raw_conn, "merchant-sim", run_seed_risk_ids, MERCHANT_BUDGET_PAISE)
    issuer = PostgresGrantIssuer()
    audit_sink = PostgresAuditSink(pg_conn)  # isolated schema — see module docstring

    result = mediate_window(
        ((granted_request, SEND_AFTER), (scope_denied_request, SEND_AFTER)),
        (), agent_repo, risk_item_repo, mediation_ledger, issuer, DECISION_AT,
        conn=pg_raw_conn, run_seed_risk_ids=run_seed_risk_ids, audit_sink=audit_sink,
    )

    # --- Phase 4 behavior is exactly what it always was ---
    assert len(result.decisions) == 2
    granted_decision = next(d for d in result.decisions if d.request_id == granted_request.request_id)
    scope_decision = next(d for d in result.decisions if d.request_id == scope_denied_request.request_id)
    assert granted_decision.outcome.value == "GRANTED"
    assert granted_decision.grant is not None
    assert scope_decision.outcome.value == "DENIED"
    assert scope_decision.reason_code.startswith("scope.")

    # --- real audit events exist, matching the real decisions ---
    granted_events = store.events_for_request(pg_conn, granted_request.request_id)
    assert [e.event_type for e in granted_events] == [REQUEST_RECEIVED, GRANT_RESERVED]

    reserved_event = granted_events[1]
    assert reserved_event.payload["grant_id"] == str(granted_decision.grant.grant_id)
    assert reserved_event.payload["request_id"] == str(granted_request.request_id)
    assert reserved_event.payload["agent_id"] == fx["agent_id"]
    assert reserved_event.payload["customer_id"] == fx["customer_id"]

    scope_events = store.events_for_request(pg_conn, scope_denied_request.request_id)
    assert [e.event_type for e in scope_events] == [REQUEST_RECEIVED, REQUEST_DENIED_ON_SCOPE]
    assert scope_events[1].reason_code == scope_decision.reason_code
    # No decision.* event exists for the scope-denied request — the
    # allocator genuinely never ran for it (spec §6.2's two-tier rule),
    # not merely "we didn't emit one."
    assert not any(e.event_type not in (REQUEST_RECEIVED, REQUEST_DENIED_ON_SCOPE) for e in scope_events)

    # (real fact_unavailable_reason_codes preservation is proven below,
    # in test_real_deferred_decision_carries_real_reason_and_score —
    # grant.reserved's own payload shape carries no fact_unavailable
    # field at all, Phase 5A §3.3, so this GRANTED path can't exercise
    # it; a decision.deferred/denied event is the right place)

    # --- real budget_window_id / claim_id via the read-only lookup ---
    with pg_raw_conn.cursor() as cur:
        cur.execute(
            "SELECT g.budget_window_id, c.claim_id FROM grants g "
            "JOIN contact_slot_claims c ON c.grant_id = g.grant_id WHERE g.grant_id = %s",
            (granted_decision.grant.grant_id,),
        )
        real_budget_window_id, real_claim_id = cur.fetchone()
    assert reserved_event.payload["budget_window_id"] == str(real_budget_window_id)
    assert reserved_event.payload["claim_id"] == str(real_claim_id)

    # --- real score (U-3), not None ---
    assert reserved_event.payload["effective_incentive_bps"] == 500

    # --- explain_request reconstructs the real grant from the log alone ---
    explanation = explain.explain_request(granted_events)
    assert explanation.outcome == "GRANTED"
    assert explanation.grant is not None
    assert explanation.grant.grant_id == str(granted_decision.grant.grant_id)
    assert explanation.agent_id == fx["agent_id"]

    scope_explanation = explain.explain_request(scope_events)
    assert scope_explanation.outcome == "DENIED"
    assert scope_explanation.scope_result.passed is False

    # --- the resulting chain (this test's slice) verifies clean ---
    report = chain.verify_chain(pg_conn)
    assert report.linkage_ok
    assert report.genesis_ok

    # --- export round-trips ---
    buf = io.StringIO()
    count = export.export_jsonl(pg_conn, buf)
    assert count == report.event_count
    lines = buf.getvalue().strip().split("\n")
    assert len(lines) == count + 1  # + the trailing export_summary line
    assert '"export_summary"' in lines[-1]


def test_real_deferred_decision_carries_real_reason_and_score(pg_conn, pg_raw_conn, phase4_fixture):
    # A quiet-hours candidate — real hard-filter DEFER, never scored
    # (consistent with tests/allocator/test_greedy.py's own
    # test_quiet_hour_candidate_never_reaches_scoring) — proves
    # decision.deferred carries the REAL reason_code end-to-end, and
    # that a hard-filter defer legitimately has no score (U-3 must never
    # fabricate one).
    fx = phase4_fixture
    quiet_request = _signed_request(fx, fx["granted_risk_id"], channel="whatsapp")
    quiet_send_after = dt.datetime(2025, 9, 10, 22, 0, tzinfo=IST)  # 22:00 IST -> quiet hours
    assert quiet_send_after.astimezone(IST).hour >= 21

    agent_repo = PostgresAgentRepository(pg_raw_conn)
    risk_item_repo = PostgresRiskItemRepository(pg_raw_conn)
    run_seed_risk_ids = frozenset({fx["granted_risk_id"]})
    mediation_ledger = PostgresMediationLedger(pg_raw_conn, "merchant-sim", run_seed_risk_ids, MERCHANT_BUDGET_PAISE)
    issuer = PostgresGrantIssuer()
    audit_sink = PostgresAuditSink(pg_conn)

    result = mediate_window(
        ((quiet_request, quiet_send_after),), (), agent_repo, risk_item_repo, mediation_ledger, issuer,
        dt.datetime(2025, 9, 10, 21, 0, tzinfo=dt.timezone.utc),
        conn=pg_raw_conn, run_seed_risk_ids=run_seed_risk_ids, audit_sink=audit_sink,
    )
    assert len(result.decisions) == 1
    decision = result.decisions[0]
    assert decision.outcome.value == "DEFERRED"
    assert decision.reason_code == "policy.quiet_hours"

    events = store.events_for_request(pg_conn, quiet_request.request_id)
    deferred_events = [e for e in events if e.event_type == DECISION_DEFERRED]
    assert len(deferred_events) == 1
    assert deferred_events[0].reason_code == "policy.quiet_hours"
    assert deferred_events[0].payload["expected_net_paise"] is None  # hard-filter defer never reaches scoring

    # --- real fact_unavailable_reason_codes preserved, not fabricated ---
    # consent_scope.evaluate() unconditionally reports FACT_UNAVAILABLE
    # (sampark/policy/hard/consent_scope.py, rule #2 — evaluated before
    # quiet_hours at rule #10, since FACT_UNAVAILABLE never
    # short-circuits) — every candidate that reaches quiet_hours carries
    # it. This is REAL data from the REAL hard-filter chain flowing
    # through to the persisted payload, not asserted against a hand-built
    # AllocationOutcome (that version already exists in
    # tests/audit/test_emit.py).
    assert "fact_unavailable.consent_scope" in deferred_events[0].payload["fact_unavailable_reason_codes"]
