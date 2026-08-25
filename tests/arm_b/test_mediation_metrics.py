"""sim/mediation_metrics.py — Design Lock §13.6, §14.

One predicate, two modes — Arm A observed, Arm B enforced.
"""

from __future__ import annotations

import datetime as dt

from agents.types import ContactOutcome
from sampark.contracts import RiskItem
from sim.mediation_metrics import build_contact_records, compute_compliance_metrics, scope_violation_count

DETECTED_AT = dt.datetime(2025, 9, 10, 6, 0, tzinfo=dt.timezone.utc)


def _outcome(agent_id, customer_id, risk_id, channel, incentive_bps, contacted_at):
    return ContactOutcome(
        outcome_id=f"{agent_id}:{risk_id}", agent_id=agent_id, customer_id=customer_id, risk_id=risk_id,
        channel=channel, incentive_bps=incentive_bps, contacted_at=contacted_at,
        recovered=False, amount_recovered_paise=0, incentive_paise=0,
    )


def _risk_item(risk_id, source="abandoned_checkout", root_cause="price_hesitation"):
    return RiskItem(risk_id=risk_id, source=source, amount_paise=10_000, root_cause=root_cause, detected_at=DETECTED_AT)


def test_quiet_hour_violation_counted_when_unmediated():
    outcome = _outcome(
        "cart_recovery_agent", "cust-1", "risk-1", "whatsapp", 500,
        dt.datetime(2025, 9, 10, 22, 0, tzinfo=dt.timezone.utc),
    )
    risk_items_by_id = {"risk-1": _risk_item("risk-1")}
    records = build_contact_records((outcome,), risk_items_by_id)
    metrics = compute_compliance_metrics(records, {})
    assert metrics["quiet_hour_violations"] == 1


def test_no_quiet_hour_violation_inside_business_hours():
    outcome = _outcome(
        "cart_recovery_agent", "cust-1", "risk-1", "whatsapp", 500,
        dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc),
    )
    risk_items_by_id = {"risk-1": _risk_item("risk-1")}
    records = build_contact_records((outcome,), risk_items_by_id)
    metrics = compute_compliance_metrics(records, {})
    assert metrics["quiet_hour_violations"] == 0


def test_second_contact_within_24h_breaches_cap():
    base = dt.datetime(2025, 9, 10, 10, 0, tzinfo=dt.timezone.utc)
    o1 = _outcome("payment_retry_agent", "cust-1", "risk-1", "sms", 0, base)
    o2 = _outcome("mandate_recovery_agent", "cust-1", "risk-2", "whatsapp", 200, base + dt.timedelta(hours=2))
    risk_items_by_id = {"risk-1": _risk_item("risk-1"), "risk-2": _risk_item("risk-2", source="mandate_failure")}
    records = build_contact_records((o1, o2), risk_items_by_id)
    metrics = compute_compliance_metrics(records, {})
    assert metrics["contact_cap_24h_breaches"] == 1
    assert metrics["conflicting_action_incidents"] == 1  # same IST window


def test_dispute_open_violation_counted_for_incentive_bearing_contact():
    outcome = _outcome(
        "cart_recovery_agent", "cust-1", "risk-1", "whatsapp", 500,
        dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc),
    )
    risk_items_by_id = {"risk-1": _risk_item("risk-1")}
    disputed_item = _risk_item("risk-other", root_cause="disputed")
    records = build_contact_records((outcome,), risk_items_by_id)
    metrics = compute_compliance_metrics(records, {"cust-1": (disputed_item,)})
    assert metrics["interlock_dispute_open_violations"] == 1


def test_fact_unavailable_counts_match_applicability():
    outcome = _outcome(
        "cart_recovery_agent", "cust-1", "risk-1", "whatsapp", 500,
        dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc),
    )
    risk_items_by_id = {"risk-1": _risk_item("risk-1")}
    records = build_contact_records((outcome,), risk_items_by_id)
    metrics = compute_compliance_metrics(records, {})
    assert metrics["fact_unavailable_counts"]["fact_unavailable.rto_flag"] == 1  # cart_recovery intent
    assert metrics["fact_unavailable_counts"]["fact_unavailable.refund_in_flight"] == 0  # not a retry intent
    assert metrics["fact_unavailable_counts"]["fact_unavailable.consent_scope"] == 1  # unconditional


def test_scope_violation_count_is_zero_with_no_scope_reason_codes():
    from sampark.contracts import DecisionOutcome, GrantDecision
    from uuid import uuid4

    decisions = [
        GrantDecision(
            decision_id=uuid4(), request_id=uuid4(), outcome=DecisionOutcome.DENIED,
            reason_code="policy.quiet_hours", human_readable=None, next_eligible_at=None, grant=None,
        )
    ]
    assert scope_violation_count(decisions) == 0
