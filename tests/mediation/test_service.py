"""Mediation service — Design Lock §1, §16.

Scope denials never reach the allocator; a GRANTED decision carries a
real Grant; a DENIED/DEFERRED decision never does.
"""

from __future__ import annotations

import datetime as dt

from sampark.budget.store import InMemoryGrantIssuer, InMemoryMediationLedger
from sampark.contracts import DecisionOutcome, RiskItem
from sampark.mediation.service import mediate_window

DECISION_AT = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)
SEND_AFTER = dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc)


def _ledger():
    risk_items_by_customer = {
        "cust-1": (
            RiskItem(
                risk_id="risk-1", source="abandoned_checkout", amount_paise=1_000_000,
                root_cause="price_hesitation", detected_at=dt.datetime(2025, 9, 10, 6, 0, tzinfo=dt.timezone.utc),
            ),
        )
    }
    return InMemoryMediationLedger(risk_items_by_customer, merchant_budget_paise_per_window=1_000_000_000)


def test_out_of_scope_request_is_denied_without_allocator_involvement(
    make_signed_request, registered_agent, risk_item_repo
):
    agent_repo, _ = registered_agent
    request = make_signed_request(requested_channel="voice")  # scope only allows whatsapp
    ledger = _ledger()
    issuer = InMemoryGrantIssuer()

    result = mediate_window(
        ((request, SEND_AFTER),), (), agent_repo, risk_item_repo, ledger, issuer, DECISION_AT
    )

    assert len(result.decisions) == 1
    decision = result.decisions[0]
    assert decision.outcome is DecisionOutcome.DENIED
    assert decision.reason_code.startswith("scope.")
    assert decision.grant is None
    # No candidate reached allocation: the ledger has no claim recorded.
    assert not ledger.has_active_claim("cust-1", SEND_AFTER.date())


def test_in_scope_request_is_mediated_and_can_be_granted(make_signed_request, registered_agent, risk_item_repo):
    agent_repo, _ = registered_agent
    request = make_signed_request()
    ledger = _ledger()
    issuer = InMemoryGrantIssuer()

    result = mediate_window(
        ((request, SEND_AFTER),), (), agent_repo, risk_item_repo, ledger, issuer, DECISION_AT
    )

    assert len(result.decisions) == 1
    decision = result.decisions[0]
    assert decision.outcome is DecisionOutcome.GRANTED
    assert decision.grant is not None
    assert decision.reason_code is None
    assert decision.next_eligible_at is None
    assert result.effective_incentive_bps_by_request_id[request.request_id] is not None


def test_quiet_hour_request_is_deferred_with_no_grant(make_signed_request, registered_agent, risk_item_repo):
    agent_repo, _ = registered_agent
    request = make_signed_request()
    ledger = _ledger()
    issuer = InMemoryGrantIssuer()
    quiet_send_after = dt.datetime(2025, 9, 10, 22, 0, tzinfo=dt.timezone.utc)

    result = mediate_window(
        ((request, quiet_send_after),), (), agent_repo, risk_item_repo, ledger, issuer, DECISION_AT
    )

    decision = result.decisions[0]
    assert decision.outcome is DecisionOutcome.DEFERRED
    assert decision.grant is None
    assert decision.next_eligible_at is not None
    assert len(result.rescheduled_candidates) == 1


def test_decision_ids_are_deterministic_across_repeated_calls(make_signed_request, registered_agent, risk_item_repo):
    agent_repo, _ = registered_agent
    request = make_signed_request()

    results = []
    for _ in range(2):
        ledger = _ledger()
        issuer = InMemoryGrantIssuer()
        result = mediate_window(
            ((request, SEND_AFTER),), (), agent_repo, risk_item_repo, ledger, issuer, DECISION_AT
        )
        results.append(result.decisions[0].decision_id)
    assert results[0] == results[1]


def test_carried_forward_candidate_is_not_rechecked_for_scope(make_signed_request, registered_agent, risk_item_repo):
    """A carried-forward Candidate is passed directly, bypassing the
    scope re-check — scope was already verified when it first arrived."""
    from sampark.allocator.candidate import build_candidate

    agent_repo, _ = registered_agent
    request = make_signed_request()
    record = risk_item_repo.get_risk_item(request.risk_id)
    candidate = build_candidate(request, record.risk_item, record.customer_id, SEND_AFTER).aged()

    ledger = _ledger()
    issuer = InMemoryGrantIssuer()
    result = mediate_window((), (candidate,), agent_repo, risk_item_repo, ledger, issuer, DECISION_AT)

    assert len(result.decisions) == 1
    assert result.decisions[0].outcome is DecisionOutcome.GRANTED
