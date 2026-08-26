"""T-23..T-25 — explainability projection (Phase 5A §9).

No database, no ledger, no policy evaluator — sampark.audit.explain
functions accept only event lists. This is the enforcement mechanism
for "reconstructable from the log alone," so these tests build event
lists directly via sampark.audit.emit and never touch a
MediationLedgerView / Environment / repository of any kind.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from sampark.allocator.candidate import build_candidate
from sampark.allocator.outcomes import AllocationOutcome, OutcomeKind
from sampark.audit import emit
from sampark.audit.explain import (
    IncompleteLogError,
    explain_contested_window,
    explain_request,
    format_explanation,
)
from sampark.contracts import DecisionOutcome, Grant, GrantDecision, GrantRequest, GrantState, RiskItem

ISSUED_AT = dt.datetime(2025, 9, 10, 6, 0, tzinfo=dt.timezone.utc)


def _request(**overrides) -> GrantRequest:
    fields = dict(
        request_id=uuid.uuid4(), agent_id="cart_recovery_agent", customer_id="cust-1", risk_id="risk-1",
        intent="cart_recovery", requested_channel="whatsapp", requested_max_incentive_bps=500,
        issued_at=ISSUED_AT, signature="sig",
    )
    fields.update(overrides)
    return GrantRequest(**fields)


def _candidate(request, amount_paise=500_000, send_after=dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc)):
    item = RiskItem(risk_id=request.risk_id, source="abandoned_checkout", amount_paise=amount_paise,
                     root_cause="price_hesitation", detected_at=ISSUED_AT)
    return build_candidate(request, item, request.customer_id, send_after)


def test_explain_request_reconstructs_a_granted_decision():
    # T-23/T-25 (grant path)
    request = _request()
    candidate = _candidate(request)
    grant = Grant(
        grant_id=uuid.uuid4(), channel="whatsapp", incentive_ceiling_paise=25_000,
        send_after=candidate.proposed_send_after,
        expires_at=candidate.proposed_send_after + dt.timedelta(hours=2), state=GrantState.RESERVED,
    )
    outcome = AllocationOutcome(
        candidate=candidate, outcome_kind=OutcomeKind.GRANTED, reason_code=None, next_eligible_at=None,
        grant=grant, fact_unavailable_reason_codes=(), score=None, rescheduled_candidate=None,
        effective_incentive_bps=500,
    )
    events = [
        emit.event_for_request_received(request),
        emit.event_for_grant_reserved(outcome, uuid.uuid4(), uuid.uuid4()),
    ]
    explanation = explain_request(events)

    assert explanation.outcome == "GRANTED"
    assert explanation.authenticated is True
    assert explanation.scope_result.passed is True
    assert explanation.grant is not None
    assert explanation.grant.grant_id == str(grant.grant_id)
    assert explanation.agent_id == "cart_recovery_agent"
    assert explanation.risk.risk_id == "risk-1"


def test_explain_request_reconstructs_a_scope_denial_without_allocator_fields():
    request = _request(requested_channel="voice")
    decision = GrantDecision(
        decision_id=uuid.uuid4(), request_id=request.request_id, outcome=DecisionOutcome.DENIED,
        reason_code="scope.channel_not_allowed", human_readable=None, next_eligible_at=None, grant=None,
    )
    events = [emit.event_for_request_received(request), emit.event_for_denied_on_scope(decision, request, ISSUED_AT)]
    explanation = explain_request(events)

    assert explanation.outcome == "DENIED"
    assert explanation.scope_result.passed is False
    assert explanation.scope_result.reason_code == "scope.channel_not_allowed"
    assert explanation.policy_result is None  # allocator never ran
    assert explanation.grant is None


def test_explain_request_reconstructs_a_hard_policy_denial():
    request = _request()
    candidate = _candidate(request)
    outcome = AllocationOutcome(
        candidate=candidate, outcome_kind=OutcomeKind.DENIED, reason_code="policy.opt_out_active",
        next_eligible_at=None, grant=None, fact_unavailable_reason_codes=("fact_unavailable.rto_flag",),
        score=None, rescheduled_candidate=None,
    )
    events = [emit.event_for_request_received(request), emit.event_for_decision(outcome, ISSUED_AT)]
    explanation = explain_request(events)

    assert explanation.outcome == "DENIED"
    assert explanation.scope_result.passed is True
    assert explanation.policy_result is not None
    assert explanation.policy_result.reason_code == "policy.opt_out_active"
    assert explanation.policy_result.fact_unavailable_reason_codes == ("fact_unavailable.rto_flag",)


def test_explain_request_reconstructs_a_deferral_and_the_final_grant():
    # A request deferred once, then granted in a later window — both
    # events must appear in the reconstructed lifecycle/timeline.
    request = _request()
    candidate = _candidate(request)
    deferred_outcome = AllocationOutcome(
        candidate=candidate, outcome_kind=OutcomeKind.DEFERRED, reason_code="budget.contact_cap_24h",
        next_eligible_at=dt.datetime(2025, 9, 11, 9, 0, tzinfo=dt.timezone.utc), grant=None,
        fact_unavailable_reason_codes=(), score=None, rescheduled_candidate=None,
    )
    rescheduled = candidate.rescheduled(dt.date(2025, 9, 11), dt.datetime(2025, 9, 11, 9, 0, tzinfo=dt.timezone.utc)).aged()
    grant = Grant(
        grant_id=uuid.uuid4(), channel="whatsapp", incentive_ceiling_paise=25_000,
        send_after=rescheduled.proposed_send_after, expires_at=rescheduled.proposed_send_after + dt.timedelta(hours=2),
        state=GrantState.RESERVED,
    )
    granted_outcome = AllocationOutcome(
        candidate=rescheduled, outcome_kind=OutcomeKind.GRANTED, reason_code=None, next_eligible_at=None,
        grant=grant, fact_unavailable_reason_codes=(), score=None, rescheduled_candidate=None,
        effective_incentive_bps=500,
    )
    events = [
        emit.event_for_request_received(request),
        emit.event_for_decision(deferred_outcome, dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)),
        emit.event_for_grant_reserved(granted_outcome, uuid.uuid4(), uuid.uuid4()),
    ]
    explanation = explain_request(events)
    assert explanation.outcome == "GRANTED"
    assert len(explanation.timeline) == 3


def test_explain_request_lifecycle_steps_are_in_order():
    request = _request()
    candidate = _candidate(request)
    grant = Grant(
        grant_id=uuid.uuid4(), channel="whatsapp", incentive_ceiling_paise=25_000,
        send_after=candidate.proposed_send_after, expires_at=candidate.proposed_send_after + dt.timedelta(hours=2),
        state=GrantState.RESERVED,
    )
    outcome = AllocationOutcome(
        candidate=candidate, outcome_kind=OutcomeKind.GRANTED, reason_code=None, next_eligible_at=None,
        grant=grant, fact_unavailable_reason_codes=(), score=None, rescheduled_candidate=None,
        effective_incentive_bps=500,
    )
    events = [
        emit.event_for_request_received(request),
        emit.event_for_grant_reserved(outcome, uuid.uuid4(), uuid.uuid4()),
        emit.event_for_grant_executing(grant, request, grant.send_after),
        emit.event_for_grant_confirmed(grant, request, grant.send_after, actual_spend_paise=20_000),
    ]
    explanation = explain_request(events)
    assert [s.event_type for s in explanation.lifecycle] == ["grant.executing", "grant.confirmed"]


def test_explanation_is_deterministic():
    # T-23
    request = _request()
    candidate = _candidate(request)
    outcome = AllocationOutcome(
        candidate=candidate, outcome_kind=OutcomeKind.DENIED, reason_code="allocation.negative_expected_net",
        next_eligible_at=None, grant=None, fact_unavailable_reason_codes=(), score=None, rescheduled_candidate=None,
    )
    events = [emit.event_for_request_received(request), emit.event_for_decision(outcome, ISSUED_AT)]

    e1 = explain_request(events)
    e2 = explain_request(list(reversed(events)))  # order-independence: sorted internally
    assert e1 == e2
    assert format_explanation(e1) == format_explanation(e2)


def test_explanation_uses_only_the_log_not_a_poisoned_object():
    # T-24: explain_request's signature accepts ONLY events — there is
    # no ledger/database parameter it could read from even if it wanted
    # to. This test documents that structurally rather than asserting a
    # negative against a mutable global.
    import inspect

    sig = inspect.signature(explain_request)
    assert list(sig.parameters) == ["events"]


def test_incomplete_log_raises_on_missing_request_received():
    request = _request()
    decision = GrantDecision(
        decision_id=uuid.uuid4(), request_id=request.request_id, outcome=DecisionOutcome.DENIED,
        reason_code="scope.channel_not_allowed", human_readable=None, next_eligible_at=None, grant=None,
    )
    scope_event = emit.event_for_denied_on_scope(decision, request, ISSUED_AT)
    with pytest.raises(IncompleteLogError):
        explain_request([scope_event])


def test_incomplete_log_raises_on_empty_event_list():
    with pytest.raises(IncompleteLogError):
        explain_request([])


def test_incomplete_log_raises_on_grant_lifecycle_without_reservation():
    request = _request()
    candidate = _candidate(request)
    grant = Grant(
        grant_id=uuid.uuid4(), channel="whatsapp", incentive_ceiling_paise=25_000,
        send_after=candidate.proposed_send_after, expires_at=candidate.proposed_send_after + dt.timedelta(hours=2),
        state=GrantState.RESERVED,
    )
    events = [
        emit.event_for_request_received(request),
        emit.event_for_grant_executing(grant, request, grant.send_after),  # no grant.reserved before it
    ]
    with pytest.raises(IncompleteLogError):
        explain_request(events)


def test_incomplete_log_raises_on_mixed_request_ids():
    request_a = _request()
    request_b = _request(risk_id="risk-2")
    events = [emit.event_for_request_received(request_a), emit.event_for_request_received(request_b)]
    with pytest.raises(IncompleteLogError):
        explain_request(events)


def test_competitor_reconstruction_names_winner_and_losers():
    # T-25: one (customer, window) contested round — winner + loser with
    # its reason_code, reconstructed purely from the decision/grant
    # events (no allocator, no ledger).
    winner_request = _request(agent_id="mandate_recovery_agent", intent="mandate_retry", requested_channel="whatsapp")
    loser_request = _request(agent_id="cart_recovery_agent", risk_id="risk-2")

    winner_candidate = _candidate(winner_request, amount_paise=410_000)
    loser_candidate = _candidate(loser_request, amount_paise=68_000)

    grant = Grant(
        grant_id=uuid.uuid4(), channel="whatsapp", incentive_ceiling_paise=8_200,
        send_after=winner_candidate.proposed_send_after,
        expires_at=winner_candidate.proposed_send_after + dt.timedelta(hours=2), state=GrantState.RESERVED,
    )
    winner_outcome = AllocationOutcome(
        candidate=winner_candidate, outcome_kind=OutcomeKind.GRANTED, reason_code=None, next_eligible_at=None,
        grant=grant, fact_unavailable_reason_codes=(), score=None, rescheduled_candidate=None,
        effective_incentive_bps=200,
    )
    loser_outcome = AllocationOutcome(
        candidate=loser_candidate, outcome_kind=OutcomeKind.DEFERRED, reason_code="allocation.lost_to_higher_expected_net",
        next_eligible_at=dt.datetime(2025, 9, 11, 9, 0, tzinfo=dt.timezone.utc), grant=None,
        fact_unavailable_reason_codes=(), score=None, rescheduled_candidate=None,
    )
    events = [
        emit.event_for_grant_reserved(winner_outcome, uuid.uuid4(), uuid.uuid4()),
        emit.event_for_decision(loser_outcome, ISSUED_AT),
    ]

    summary = explain_contested_window(events)
    assert summary.winner is not None
    assert summary.winner.agent_id == "mandate_recovery_agent"
    assert len(summary.losers) == 1
    assert summary.losers[0].agent_id == "cart_recovery_agent"
    assert summary.losers[0].reason_code == "allocation.lost_to_higher_expected_net"


def test_competitor_reconstruction_rejects_mixed_windows():
    request_a = _request()
    request_b = _request(risk_id="risk-2", customer_id="cust-2")
    outcome_a = AllocationOutcome(
        candidate=_candidate(request_a), outcome_kind=OutcomeKind.DENIED, reason_code="policy.opt_out_active",
        next_eligible_at=None, grant=None, fact_unavailable_reason_codes=(), score=None, rescheduled_candidate=None,
    )
    outcome_b = AllocationOutcome(
        candidate=_candidate(request_b), outcome_kind=OutcomeKind.DENIED, reason_code="policy.opt_out_active",
        next_eligible_at=None, grant=None, fact_unavailable_reason_codes=(), score=None, rescheduled_candidate=None,
    )
    events = [emit.event_for_decision(outcome_a, ISSUED_AT), emit.event_for_decision(outcome_b, ISSUED_AT)]
    with pytest.raises(IncompleteLogError):
        explain_contested_window(events)


def test_format_explanation_names_the_denial_reason():
    request = _request()
    outcome = AllocationOutcome(
        candidate=_candidate(request), outcome_kind=OutcomeKind.DENIED, reason_code="policy.quiet_hours",
        next_eligible_at=None, grant=None, fact_unavailable_reason_codes=(), score=None, rescheduled_candidate=None,
    )
    events = [emit.event_for_request_received(request), emit.event_for_decision(outcome, ISSUED_AT)]
    text = format_explanation(explain_request(events))
    assert "policy.quiet_hours" in text
    assert "DENIED" in text
