from __future__ import annotations

from sampark.allocator.reason_codes import DLT_TEMPLATE_UNAVAILABLE
from sampark.policy.hard import dlt_template
from sampark.policy.types import Verdict


def test_registered_pair_is_admissible(make_candidate, policy_context):
    candidate = make_candidate(intent="cart_recovery", requested_channel="whatsapp")
    verdict = dlt_template.evaluate(candidate, policy_context())
    assert verdict.verdict is Verdict.ADMISSIBLE


def test_all_four_baseline_agent_pairs_are_registered(make_candidate, policy_context):
    """Phase 4 must never deny the batch's own well-behaved agents on a
    template gap it never populated."""
    baseline_pairs = [
        ("payment_retry", "sms"),
        ("cart_recovery", "whatsapp"),
        ("mandate_retry", "whatsapp"),
        ("receivables_followup", "voice"),
    ]
    for intent, channel in baseline_pairs:
        candidate = make_candidate(intent=intent, requested_channel=channel, requested_max_incentive_bps=0)
        verdict = dlt_template.evaluate(candidate, policy_context())
        assert verdict.verdict is Verdict.ADMISSIBLE, f"({intent}, {channel}) must be registered"


def test_unregistered_pair_is_denied(make_candidate, policy_context):
    candidate = make_candidate(intent="cart_recovery", requested_channel="voice", requested_max_incentive_bps=0)
    verdict = dlt_template.evaluate(candidate, policy_context())
    assert verdict.verdict is Verdict.INADMISSIBLE
    assert verdict.reason_code == DLT_TEMPLATE_UNAVAILABLE
    assert verdict.is_deny


def test_dlt_template_never_fact_unavailable(make_candidate, policy_context):
    candidate = make_candidate(intent="unknown_intent", requested_channel="email", requested_max_incentive_bps=0)
    verdict = dlt_template.evaluate(candidate, policy_context())
    assert verdict.verdict is not Verdict.FACT_UNAVAILABLE
