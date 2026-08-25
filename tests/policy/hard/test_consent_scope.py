from __future__ import annotations

from sampark.allocator.reason_codes import FACT_UNAVAILABLE_CONSENT_SCOPE
from sampark.policy.hard import consent_scope
from sampark.policy.types import Verdict


def test_consent_scope_is_always_fact_unavailable(make_candidate, policy_context):
    """Design Lock §4.3: consent_scopes = {} is a PLACEHOLDER, not a
    true statement — this rule never interprets it either way."""
    candidate = make_candidate(customer_id="cust-1")
    verdict = consent_scope.evaluate(candidate, policy_context())
    assert verdict.verdict is Verdict.FACT_UNAVAILABLE
    assert verdict.reason_code == FACT_UNAVAILABLE_CONSENT_SCOPE


def test_consent_scope_is_fact_unavailable_even_with_populated_data(make_candidate, policy_context, fake_ledger):
    """Even if some future dataset populates consent_scopes, this rule
    (as locked) does not interpret it — it is a permanent property of
    the current design, not a per-request check."""
    fake_ledger.consents["cust-1"] = {"cart_recovery": {"granted_at": "2025-09-01T00:00:00Z"}}
    candidate = make_candidate(customer_id="cust-1")
    verdict = consent_scope.evaluate(candidate, policy_context())
    assert verdict.verdict is Verdict.FACT_UNAVAILABLE


def test_consent_scope_never_admits(make_candidate, policy_context):
    candidate = make_candidate(customer_id="cust-1")
    verdict = consent_scope.evaluate(candidate, policy_context())
    assert verdict.verdict is not Verdict.ADMISSIBLE
