from __future__ import annotations

from sampark.allocator.reason_codes import OPT_OUT_ACTIVE
from sampark.policy.hard import opt_out
from sampark.policy.types import Verdict


def test_no_optout_is_admissible(make_candidate, policy_context):
    candidate = make_candidate(customer_id="cust-1")
    verdict = opt_out.evaluate(candidate, policy_context())
    assert verdict.verdict is Verdict.ADMISSIBLE


def test_opted_out_channel_is_denied(make_candidate, policy_context, fake_ledger):
    fake_ledger.optouts["cust-1"] = {"whatsapp": "2025-08-01T00:00:00Z"}
    candidate = make_candidate(customer_id="cust-1", requested_channel="whatsapp")
    verdict = opt_out.evaluate(candidate, policy_context())
    assert verdict.verdict is Verdict.INADMISSIBLE
    assert verdict.reason_code == OPT_OUT_ACTIVE
    assert verdict.is_deny
    assert not verdict.is_defer


def test_optout_on_a_different_channel_does_not_block(make_candidate, policy_context, fake_ledger):
    fake_ledger.optouts["cust-1"] = {"sms": "2025-08-01T00:00:00Z"}
    candidate = make_candidate(customer_id="cust-1", requested_channel="whatsapp")
    verdict = opt_out.evaluate(candidate, policy_context())
    assert verdict.verdict is Verdict.ADMISSIBLE


def test_opt_out_is_never_fact_unavailable(make_candidate, policy_context):
    candidate = make_candidate(customer_id="cust-1")
    verdict = opt_out.evaluate(candidate, policy_context())
    assert verdict.verdict is not Verdict.FACT_UNAVAILABLE
