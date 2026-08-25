from __future__ import annotations

import datetime as dt

from sampark.allocator.reason_codes import (
    FACT_UNAVAILABLE_FRAUD_REVIEW,
    FACT_UNAVAILABLE_MANDATE_CANCELLATION,
    FACT_UNAVAILABLE_REFUND_IN_FLIGHT,
    FACT_UNAVAILABLE_RTO_FLAG,
    INTERLOCK_ACTIVE_GRANT_IN_WINDOW,
    INTERLOCK_DISPUTE_OPEN,
)
from sampark.policy.hard import interlocks
from sampark.policy.types import Verdict

DECISION_AT = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)


# --- dispute_open (available, proxy) ------------------------------------


def test_dispute_open_denies_incentive_bearing_candidate(make_candidate, make_risk_item, policy_context, fake_ledger):
    disputed_item = make_risk_item(risk_id="risk-other", root_cause="disputed")
    fake_ledger.risk_items_by_customer_map["cust-1"] = (disputed_item,)
    candidate = make_candidate(customer_id="cust-1", requested_max_incentive_bps=500)
    verdict = interlocks.evaluate_dispute_open(candidate, policy_context(DECISION_AT))
    assert verdict.verdict is Verdict.INADMISSIBLE
    assert verdict.reason_code == INTERLOCK_DISPUTE_OPEN
    assert verdict.is_deny


def test_dispute_open_does_not_apply_to_zero_incentive_candidate(make_candidate, make_risk_item, policy_context, fake_ledger):
    disputed_item = make_risk_item(risk_id="risk-other", root_cause="disputed")
    fake_ledger.risk_items_by_customer_map["cust-1"] = (disputed_item,)
    candidate = make_candidate(customer_id="cust-1", requested_max_incentive_bps=0)
    verdict = interlocks.evaluate_dispute_open(candidate, policy_context(DECISION_AT))
    assert verdict.verdict is Verdict.ADMISSIBLE


def test_dispute_open_admissible_with_no_disputed_items(make_candidate, make_risk_item, policy_context, fake_ledger):
    fake_ledger.risk_items_by_customer_map["cust-1"] = (make_risk_item(root_cause="price_hesitation"),)
    candidate = make_candidate(customer_id="cust-1", requested_max_incentive_bps=500)
    verdict = interlocks.evaluate_dispute_open(candidate, policy_context(DECISION_AT))
    assert verdict.verdict is Verdict.ADMISSIBLE


def test_dispute_open_never_fact_unavailable(make_candidate, policy_context, fake_ledger):
    candidate = make_candidate(customer_id="cust-1", requested_max_incentive_bps=500)
    verdict = interlocks.evaluate_dispute_open(candidate, policy_context(DECISION_AT))
    assert verdict.verdict is not Verdict.FACT_UNAVAILABLE


# --- unavailable interlocks: applies_to gates relevance -------------------


def test_rto_flag_fact_unavailable_for_cart_recovery(make_candidate, policy_context):
    candidate = make_candidate(intent="cart_recovery")
    verdict = interlocks.evaluate_rto_flag(candidate, policy_context(DECISION_AT))
    assert verdict.verdict is Verdict.FACT_UNAVAILABLE
    assert verdict.reason_code == FACT_UNAVAILABLE_RTO_FLAG


def test_rto_flag_admissible_for_non_cart_recovery_intent(make_candidate, policy_context):
    candidate = make_candidate(intent="payment_retry", requested_max_incentive_bps=0)
    verdict = interlocks.evaluate_rto_flag(candidate, policy_context(DECISION_AT))
    assert verdict.verdict is Verdict.ADMISSIBLE


def test_refund_in_flight_fact_unavailable_for_retry_intents(make_candidate, policy_context):
    for intent in ("payment_retry", "mandate_retry"):
        candidate = make_candidate(intent=intent, requested_max_incentive_bps=0)
        verdict = interlocks.evaluate_refund_in_flight(candidate, policy_context(DECISION_AT))
        assert verdict.verdict is Verdict.FACT_UNAVAILABLE
        assert verdict.reason_code == FACT_UNAVAILABLE_REFUND_IN_FLIGHT


def test_refund_in_flight_admissible_for_non_retry_intent(make_candidate, policy_context):
    candidate = make_candidate(intent="receivables_followup", requested_max_incentive_bps=0)
    verdict = interlocks.evaluate_refund_in_flight(candidate, policy_context(DECISION_AT))
    assert verdict.verdict is Verdict.ADMISSIBLE


def test_fraud_review_fact_unavailable_for_incentive_bearing(make_candidate, policy_context):
    candidate = make_candidate(requested_max_incentive_bps=500)
    verdict = interlocks.evaluate_fraud_review(candidate, policy_context(DECISION_AT))
    assert verdict.verdict is Verdict.FACT_UNAVAILABLE
    assert verdict.reason_code == FACT_UNAVAILABLE_FRAUD_REVIEW


def test_fraud_review_admissible_for_zero_incentive(make_candidate, policy_context):
    candidate = make_candidate(requested_max_incentive_bps=0)
    verdict = interlocks.evaluate_fraud_review(candidate, policy_context(DECISION_AT))
    assert verdict.verdict is Verdict.ADMISSIBLE


def test_mandate_cancellation_fact_unavailable_for_mandate_retry(make_candidate, policy_context):
    candidate = make_candidate(intent="mandate_retry", requested_max_incentive_bps=200)
    verdict = interlocks.evaluate_mandate_cancellation(candidate, policy_context(DECISION_AT))
    assert verdict.verdict is Verdict.FACT_UNAVAILABLE
    assert verdict.reason_code == FACT_UNAVAILABLE_MANDATE_CANCELLATION


def test_mandate_cancellation_admissible_for_other_intent(make_candidate, policy_context):
    candidate = make_candidate(intent="cart_recovery")
    verdict = interlocks.evaluate_mandate_cancellation(candidate, policy_context(DECISION_AT))
    assert verdict.verdict is Verdict.ADMISSIBLE


# --- active_grant_in_window (available) -----------------------------------


def test_active_grant_in_window_defers(make_candidate, policy_context, fake_ledger):
    candidate = make_candidate(customer_id="cust-1", proposed_send_after=dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc))
    fake_ledger.active_claims.add(("cust-1", candidate.window_id))
    verdict = interlocks.evaluate_active_grant_in_window(candidate, policy_context(DECISION_AT))
    assert verdict.verdict is Verdict.INADMISSIBLE
    assert verdict.reason_code == INTERLOCK_ACTIVE_GRANT_IN_WINDOW
    assert verdict.is_defer


def test_no_active_grant_is_admissible(make_candidate, policy_context, fake_ledger):
    candidate = make_candidate(customer_id="cust-1")
    verdict = interlocks.evaluate_active_grant_in_window(candidate, policy_context(DECISION_AT))
    assert verdict.verdict is Verdict.ADMISSIBLE


def test_active_grant_in_window_applies_to_every_candidate(make_candidate, policy_context, fake_ledger):
    candidate = make_candidate(customer_id="cust-1", requested_max_incentive_bps=0, intent="payment_retry")
    fake_ledger.active_claims.add(("cust-1", candidate.window_id))
    verdict = interlocks.evaluate_active_grant_in_window(candidate, policy_context(DECISION_AT))
    assert verdict.is_defer
