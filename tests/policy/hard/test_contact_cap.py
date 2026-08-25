from __future__ import annotations

import datetime as dt

from sampark.allocator.reason_codes import CONTACT_CAP_24H, CONTACT_CAP_7D
from sampark.policy.hard import contact_cap
from sampark.policy.types import Verdict

DECISION_AT = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)


def test_under_both_caps_is_admissible(make_candidate, policy_context, fake_ledger):
    fake_ledger.rolling_counts_map["cust-1"] = (0, 0)
    candidate = make_candidate(customer_id="cust-1")
    verdict = contact_cap.evaluate(candidate, policy_context(DECISION_AT))
    assert verdict.verdict is Verdict.ADMISSIBLE


def test_at_24h_cap_defers(make_candidate, policy_context, fake_ledger):
    fake_ledger.rolling_counts_map["cust-1"] = (1, 1)  # CONTACT_CAP_24H == 1
    candidate = make_candidate(customer_id="cust-1")
    verdict = contact_cap.evaluate(candidate, policy_context(DECISION_AT))
    assert verdict.verdict is Verdict.INADMISSIBLE
    assert verdict.reason_code == CONTACT_CAP_24H
    assert verdict.is_defer


def test_under_24h_but_at_7d_cap_defers(make_candidate, policy_context, fake_ledger):
    fake_ledger.rolling_counts_map["cust-1"] = (0, 2)  # CONTACT_CAP_7D == 2
    candidate = make_candidate(customer_id="cust-1")
    verdict = contact_cap.evaluate(candidate, policy_context(DECISION_AT))
    assert verdict.verdict is Verdict.INADMISSIBLE
    assert verdict.reason_code == CONTACT_CAP_7D
    assert verdict.is_defer


def test_one_under_7d_cap_is_admissible(make_candidate, policy_context, fake_ledger):
    fake_ledger.rolling_counts_map["cust-1"] = (0, 1)
    candidate = make_candidate(customer_id="cust-1")
    verdict = contact_cap.evaluate(candidate, policy_context(DECISION_AT))
    assert verdict.verdict is Verdict.ADMISSIBLE


def test_contact_cap_never_fact_unavailable(make_candidate, policy_context, fake_ledger):
    candidate = make_candidate(customer_id="cust-1")
    verdict = contact_cap.evaluate(candidate, policy_context(DECISION_AT))
    assert verdict.verdict is not Verdict.FACT_UNAVAILABLE
