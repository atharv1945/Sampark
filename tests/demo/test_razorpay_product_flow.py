"""The Razorpay product flow, end to end, against real PostgreSQL.

Every test here runs in a throwaway `sampark_demo_<...>` schema created by the
production isolation code, and asserts against the real hash chain — not a
double. The Razorpay TRANSPORT is stubbed (a payment entity dict in Razorpay's
own shape); everything downstream of the adapter is the shipped system:
identity resolution, the root-cause lookup, the registry, `evaluate_scope`,
the hard policy chain, the allocator, `issue_grant`'s SERIALIZABLE
transaction, the grant lifecycle, and `sampark.audit.chain.append`.

The single most important test in this file is
`test_the_protected_public_chain_is_untouched`.
"""

from __future__ import annotations

import datetime as dt

import pytest

from sampark.audit.chain import verify_chain
from sampark.demo import isolation
from sampark.demo.provider import ProviderFailureMode
from sampark.demo.razorpay_product import AGENT_ID, ProductFlowError, RazorpayProductRun
from sampark.integrations.normalize import normalize_payment
from sampark.integrations.provenance import McpCallReceipt, Provenance

pytestmark = pytest.mark.postgres

# 06:05 UTC == 11:35 IST — inside the 09:00-21:00 sendable band, so the
# scheduled contact (detected_at + 2h = 13:35 IST) is not deferred by quiet
# hours. Fixed rather than "now", so the decision does not depend on when the
# suite runs. Quiet-hours behaviour has its own test below.
MIDDAY_IST = dt.datetime(2026, 9, 1, 6, 5, tzinfo=dt.timezone.utc)
EVENING_IST = dt.datetime(2026, 9, 1, 15, 5, tzinfo=dt.timezone.utc)  # 20:35 IST


def provenance(payment_id: str) -> Provenance:
    return Provenance.from_mcp(
        McpCallReceipt("fetch_payment", "mcp.razorpay.com", "razorpay-mcp-server", "1.0.0"),
        observed_at=MIDDAY_IST, reference=payment_id,
    )


def opportunity(payment_id="pay_FLOW0000001", amount=100_000, when=MIDDAY_IST, contact="+919876500001"):
    return normalize_payment(
        {
            "id": payment_id, "entity": "payment", "amount": amount, "currency": "INR",
            "status": "failed", "order_id": "order_FLOW01", "method": "card",
            "email": payment_id.lower() + "@example.com", "contact": contact,
            "error_code": "GATEWAY_ERROR", "error_reason": "issuer_down",
            "error_source": "bank", "error_step": "payment_authorization",
            "created_at": int(when.timestamp()),
        },
        provenance(payment_id),
        payment_link_id="plink_FLOW1",
    )


@pytest.fixture()
def flow(raw_conn, demo_schema):
    run = RazorpayProductRun(conn=raw_conn, schema=demo_schema)
    run.prepare(at=MIDDAY_IST)
    return run


def event_types(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT event_type FROM audit_events ORDER BY seq")
        return [row[0] for row in cur.fetchall()]


def reason_codes(conn) -> dict[str, str | None]:
    with conn.cursor() as cur:
        cur.execute("SELECT event_type, reason_code FROM audit_events ORDER BY seq")
        return {row[0]: row[1] for row in cur.fetchall()}


# --- safety: the property everything else rests on --------------------------


def test_the_protected_public_chain_is_untouched(raw_conn, demo_schema):
    """Phase 8's central safety guarantee, re-asserted for the product flow.
    A demo event appended into `public.audit_events` could never be removed —
    that table is append-only by trigger."""
    before = isolation.public_audit_fingerprint(raw_conn)
    run = RazorpayProductRun(conn=raw_conn, schema=demo_schema)
    run.prepare(at=MIDDAY_IST)
    run.ingest(opportunity())
    assert isolation.public_audit_fingerprint(raw_conn) == before


# --- the happy path ---------------------------------------------------------


def test_a_failed_payment_runs_the_whole_unmodified_pipeline(flow, raw_conn):
    """Above the allocator's break-even, so it is granted, executed and
    confirmed — the full decide-then-act arc."""
    outcome = flow.ingest(opportunity(amount=400_000))
    assert outcome.outcome == "GRANTED"
    assert outcome.grant_id is not None
    assert outcome.delivery is not None and outcome.delivery.delivered
    assert outcome.delivery.channel == "sms"

    types = event_types(raw_conn)
    for expected in ("agent.registered", "payment.risk_detected", "request.received",
                     "grant.reserved", "grant.executing", "grant.confirmed"):
        assert expected in types, expected + " missing from " + repr(types)
    assert "request.denied_on_scope" not in types


def test_the_grant_and_its_claim_really_exist_in_the_ledger(flow, raw_conn):
    flow.ingest(opportunity(amount=400_000))
    with raw_conn.cursor() as cur:
        cur.execute("SELECT state FROM grants")
        assert [r[0] for r in cur.fetchall()] == ["CONFIRMED"]
        cur.execute("SELECT count(*) FROM contact_slot_claims")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM grant_requests")
        assert cur.fetchone()[0] == 1


def test_the_chain_verifies_after_the_flow(flow, raw_conn):
    flow.ingest(opportunity(amount=400_000))
    report = verify_chain(raw_conn)
    assert report.ok
    assert report.genesis_ok and report.linkage_ok
    assert report.missing_grant_reservations == ()


# --- the bounded decision: the actual product argument ----------------------


def test_a_1000_rupee_payment_is_declined_on_negative_expected_net(flow, raw_conn):
    """THE product claim, asserted rather than described.

    With the FROZEN Phase 4 constants a 1,000 INR failed payment is below the
    allocator's break-even: the fatigue term prices the forward cost of
    consuming this customer's single contact slot higher than the recovery is
    worth. SAMPARK declines it. Nothing here is tuned to produce that — if a
    protected constant ever moved, this test is what would notice."""
    from sampark.integrations import gateway

    outcome = flow.ingest(opportunity(amount=gateway.demo_amount_paise()))
    assert outcome.outcome == "DENIED"
    assert outcome.reason_code == "allocation.negative_expected_net"
    assert outcome.grant_id is None
    assert outcome.delivery is None

    codes = reason_codes(raw_conn)
    assert codes["decision.denied"] == "allocation.negative_expected_net"
    with raw_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM grants")
        assert cur.fetchone()[0] == 0, "a declined opportunity must issue no grant"


def test_the_denial_event_carries_the_arithmetic_the_screen_shows(flow, raw_conn):
    """The product page renders `expected_net_paise` off this event. It must
    really be there, and it must really be negative."""
    from sampark.integrations import gateway

    flow.ingest(opportunity(amount=gateway.demo_amount_paise()))
    with raw_conn.cursor() as cur:
        cur.execute("SELECT payload FROM audit_events WHERE event_type = 'decision.denied'")
        payload = cur.fetchone()[0]
    assert payload["expected_net_paise"] < 0
    assert payload["amount_paise"] == gateway.demo_amount_paise()
    assert payload["risk_id"].startswith("rzp_pay_")


def test_the_decision_is_not_simply_the_bigger_amount_winning(flow):
    """Two payments, same failure code, different amounts — and the split is
    at the EXPECTED-NET threshold, not at "whichever is larger". Below the
    line is declined even though it is real money; above it is funded."""
    low = flow.ingest(opportunity(payment_id="pay_LOW000000001", amount=100_000,
                                  contact="+919876500011"))
    high = flow.ingest(opportunity(payment_id="pay_HIGH00000001", amount=400_000,
                                   contact="+919876500012"))
    assert low.outcome == "DENIED" and low.reason_code == "allocation.negative_expected_net"
    assert high.outcome == "GRANTED"


# --- idempotency ------------------------------------------------------------


def test_re_ingesting_one_payment_makes_no_second_decision(flow, raw_conn):
    opp = opportunity(amount=400_000)
    first = flow.ingest(opp)
    types_before = event_types(raw_conn)

    second = flow.ingest(opp)
    assert second.duplicate is True
    assert second.outcome == first.outcome
    assert second.grant_id == first.grant_id
    assert event_types(raw_conn) == types_before, "a duplicate wrote to the chain"

    with raw_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM grants")
        assert cur.fetchone()[0] == 1, "a duplicate issued a second grant"


def test_a_freshly_built_equal_opportunity_is_also_deduplicated(flow, raw_conn):
    """The guard is keyed on `payment_id`, not on object identity — which is
    what a re-delivered webhook actually looks like."""
    flow.ingest(opportunity(amount=400_000))
    count_before = len(event_types(raw_conn))
    again = flow.ingest(opportunity(amount=400_000))
    assert again.duplicate is True
    assert len(event_types(raw_conn)) == count_before


# --- policy is live, not scripted -------------------------------------------


def test_an_evening_payment_is_deferred_by_the_quiet_hours_rule(raw_conn, demo_schema):
    """A payment failing at 20:35 IST proposes a contact at 22:35 IST, inside
    the TCCCPR 21:00-09:00 blackout. The hard policy chain defers it to the
    next morning and the flow follows it there — the deferral contract being
    honoured, not worked around."""
    run = RazorpayProductRun(conn=raw_conn, schema=demo_schema)
    run.prepare(at=EVENING_IST)
    outcome = run.ingest(opportunity(payment_id="pay_EVE000000001", amount=400_000,
                                     when=EVENING_IST, contact="+919876500021"))
    codes = reason_codes(raw_conn)
    assert "decision.deferred" in codes
    assert codes["decision.deferred"] == "policy.quiet_hours"
    assert len(outcome.windows_evaluated) >= 2, "the deferred candidate was not carried forward"
    assert outcome.outcome in ("GRANTED", "ROLLED_BACK")


def test_two_payments_from_one_person_share_one_contact_budget(raw_conn, demo_schema):
    """One human is one row, and that row owns the contact budget.

    Two DIFFERENT failed payments arrive from the same phone number. Identity
    resolution unifies them onto one customer, and the shared budget then
    SPACES THE CONTACTS APART rather than sending twice: the second request is
    deferred out of the first one's window by `interlock.active_grant_in_window`,
    deferred again the next day by `budget.contact_cap_24h`, and only then
    granted.

    This is the thesis arriving from a real payment processor rather than from
    the simulator. A per-request authorizer would have permitted both contacts
    on the same day, because each is individually and correctly authorized.
    """
    run = RazorpayProductRun(conn=raw_conn, schema=demo_schema)
    run.prepare(at=MIDDAY_IST)
    first = run.ingest(opportunity(payment_id="pay_SAME00000001", amount=400_000,
                                   contact="+919876500031"))
    second = run.ingest(opportunity(payment_id="pay_SAME00000002", amount=400_000,
                                    contact="+919876500031"))

    assert first.outcome == "GRANTED"
    assert second.customer_id == first.customer_id, "identity resolution did not unify them"

    with raw_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM customers")
        assert cur.fetchone()[0] == 1, "two payments from one person created two customers"

        cur.execute("SELECT reason_code FROM audit_events WHERE event_type = 'decision.deferred' ORDER BY seq")
        deferrals = [row[0] for row in cur.fetchall()]

    assert "interlock.active_grant_in_window" in deferrals, (
        "the second contact was not held out of the first one's window"
    )
    assert "budget.contact_cap_24h" in deferrals, (
        "the 24-hour contact cap did not govern the second contact"
    )
    assert len(second.windows_evaluated) > 1, "the deferred candidate was not carried forward"
    assert second.windows_evaluated[0] == first.windows_evaluated[0]
    assert second.windows_evaluated[-1] != first.windows_evaluated[-1], (
        "both contacts landed in the same window, which is the breach this prevents"
    )


def test_the_two_contacts_land_in_different_windows_and_each_holds_its_own_slot(raw_conn, demo_schema):
    """The mechanism behind the test above, checked in the ledger rather than
    in the log: one active contact-slot claim per (customer, window)."""
    run = RazorpayProductRun(conn=raw_conn, schema=demo_schema)
    run.prepare(at=MIDDAY_IST)
    run.ingest(opportunity(payment_id="pay_SLOT00000001", amount=400_000, contact="+919876500041"))
    run.ingest(opportunity(payment_id="pay_SLOT00000002", amount=400_000, contact="+919876500041"))

    with raw_conn.cursor() as cur:
        cur.execute("SELECT customer_id, window_id FROM contact_slot_claims ORDER BY window_id")
        claims = cur.fetchall()
    assert len(claims) == 2
    assert claims[0][0] == claims[1][0], "the claims are not for one customer"
    assert claims[0][1] != claims[1][1], "two claims in one window would breach the contact cap"


# --- failure and recovery ---------------------------------------------------


def test_a_provider_failure_rolls_the_grant_back_and_releases_its_resources(flow, raw_conn):
    flow.arm_provider_failure(ProviderFailureMode.HARD_DOWN)
    outcome = flow.ingest(opportunity(amount=400_000))
    assert outcome.outcome == "ROLLED_BACK"
    assert outcome.delivery is not None and not outcome.delivery.delivered
    assert outcome.delivery.rolled_back

    types = event_types(raw_conn)
    assert "grant.rolled_back" in types
    assert "grant.confirmed" not in types
    with raw_conn.cursor() as cur:
        cur.execute("SELECT state FROM grants")
        assert [r[0] for r in cur.fetchall()] == ["ROLLED_BACK"]
    assert verify_chain(raw_conn).ok, "the chain must still verify after a rollback"


def test_a_retry_after_provider_acceptance_never_double_sends(flow):
    """spec §6.2's "no double-send on retry". The provider accepted before the
    timeout landed; the retry finds the stored receipt and returns it without
    contacting anyone again."""
    flow.arm_provider_failure(ProviderFailureMode.ACCEPT_THEN_TIMEOUT)
    outcome = flow.ingest(opportunity(amount=400_000))
    assert outcome.outcome == "GRANTED"
    assert outcome.delivery is not None
    assert outcome.delivery.attempts > 1
    assert outcome.delivery.deduplicated is True


# --- degradation is recorded, never claimed away ----------------------------


def test_model_unavailability_is_recorded_at_session_start(flow, raw_conn):
    """The uplift model is unavailable on this repository's data (the
    committed Phase 6/7 finding). The session records that rather than
    starting silently degraded, and nothing anywhere claims otherwise."""
    codes = reason_codes(raw_conn)
    assert codes.get("model.degraded") == "model.artifact_unavailable"
    assert flow.degraded is True
    assert flow.scorer.inner_name == "HeuristicScorer"


# --- guards -----------------------------------------------------------------


def test_ingest_before_prepare_is_refused(raw_conn, demo_schema):
    run = RazorpayProductRun(conn=raw_conn, schema=demo_schema)
    with pytest.raises(ProductFlowError):
        run.ingest(opportunity())


def test_the_agent_scope_is_the_unchanged_arm_b_one(flow, raw_conn):
    """Nothing was widened to admit a Razorpay payment."""
    from sim.arm_b import _AGENT_SCOPES

    with raw_conn.cursor() as cur:
        cur.execute(
            "SELECT allowed_channels, allowed_intents, allowed_risk_sources, max_incentive_bps "
            "FROM capability_scopes WHERE agent_id = %s",
            (AGENT_ID,),
        )
        row = cur.fetchone()
    expected = _AGENT_SCOPES[AGENT_ID]
    assert list(row[0]) == expected.allowed_channels
    assert list(row[1]) == expected.allowed_intents
    assert list(row[2]) == expected.allowed_risk_sources
    assert row[3] == expected.max_incentive_bps == 0
