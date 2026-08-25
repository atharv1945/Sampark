"""The composite hard-filter chain — Design Lock §5.1.

First real non-ADMISSIBLE verdict wins; FACT_UNAVAILABLE does not
short-circuit.
"""

from __future__ import annotations

import datetime as dt

from sampark.allocator.reason_codes import (
    CONTACT_CAP_24H,
    DLT_TEMPLATE_UNAVAILABLE,
    FACT_UNAVAILABLE_CONSENT_SCOPE,
    FACT_UNAVAILABLE_FRAUD_REVIEW,
    FACT_UNAVAILABLE_RTO_FLAG,
    OPT_OUT_ACTIVE,
    QUIET_HOURS,
)
from sampark.policy.hard import evaluate_all
from sampark.policy.types import Verdict

DECISION_AT = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)
BUSINESS_HOURS_SEND_AFTER = dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc)


def test_fully_admissible_candidate_collects_fact_unavailable_but_admits(
    make_candidate, policy_context, fake_ledger
):
    candidate = make_candidate(
        customer_id="cust-1",
        intent="cart_recovery",
        requested_channel="whatsapp",
        requested_max_incentive_bps=500,
        proposed_send_after=BUSINESS_HOURS_SEND_AFTER,
    )
    result = evaluate_all(candidate, policy_context(DECISION_AT))
    assert result.verdict.verdict is Verdict.ADMISSIBLE
    # consent_scope (always) + rto_flag (cart_recovery) + fraud_review
    # (incentive > 0) all apply and are unavailable, but none blocked it.
    assert FACT_UNAVAILABLE_CONSENT_SCOPE in result.fact_unavailable_reason_codes
    assert FACT_UNAVAILABLE_RTO_FLAG in result.fact_unavailable_reason_codes
    assert FACT_UNAVAILABLE_FRAUD_REVIEW in result.fact_unavailable_reason_codes


def test_opt_out_wins_over_everything_else(make_candidate, policy_context, fake_ledger):
    """opt_out is rule #1 — a permanently barred candidate should report
    that, not a downstream transient concern."""
    fake_ledger.optouts["cust-1"] = {"whatsapp": "2025-08-01T00:00:00Z"}
    candidate = make_candidate(
        customer_id="cust-1",
        requested_channel="whatsapp",
        proposed_send_after=dt.datetime(2025, 9, 10, 22, 0, tzinfo=dt.timezone.utc),  # also quiet hours
    )
    result = evaluate_all(candidate, policy_context(DECISION_AT))
    assert result.verdict.reason_code == OPT_OUT_ACTIVE
    assert result.verdict.is_deny


def test_quiet_hours_short_circuits_before_contact_cap(make_candidate, policy_context, fake_ledger):
    fake_ledger.rolling_counts_map["cust-1"] = (1, 1)  # would ALSO breach the cap
    candidate = make_candidate(
        customer_id="cust-1",
        proposed_send_after=dt.datetime(2025, 9, 10, 22, 0, tzinfo=dt.timezone.utc),
    )
    result = evaluate_all(candidate, policy_context(DECISION_AT))
    assert result.verdict.reason_code == QUIET_HOURS


def test_dlt_template_wins_over_quiet_hours(make_candidate, policy_context, fake_ledger):
    candidate = make_candidate(
        intent="cart_recovery",
        requested_channel="voice",  # not a registered pair
        requested_max_incentive_bps=0,
        proposed_send_after=dt.datetime(2025, 9, 10, 22, 0, tzinfo=dt.timezone.utc),  # also quiet hours
    )
    result = evaluate_all(candidate, policy_context(DECISION_AT))
    assert result.verdict.reason_code == DLT_TEMPLATE_UNAVAILABLE
    assert result.verdict.is_deny


def test_admissible_verdict_has_no_reason_code(make_candidate, policy_context):
    candidate = make_candidate(proposed_send_after=BUSINESS_HOURS_SEND_AFTER)
    result = evaluate_all(candidate, policy_context(DECISION_AT))
    assert result.verdict.verdict is Verdict.ADMISSIBLE
    assert result.verdict.reason_code is None
    assert result.verdict.next_eligible_at is None
