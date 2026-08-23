"""GrantDecision — CONTRACTS.md Part 2, outcome-specific invariants."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sampark.contracts import DecisionOutcome, Grant, GrantDecision, GrantState


def _grant() -> Grant:
    send_after = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)
    return Grant(
        grant_id=uuid4(), channel="whatsapp", incentive_ceiling_paise=0,
        send_after=send_after, expires_at=send_after + timedelta(minutes=15),
        state=GrantState.RESERVED,
    )


def test_granted_decision_valid_with_grant_and_no_reason():
    decision = GrantDecision(
        decision_id=uuid4(), request_id=uuid4(),
        outcome=DecisionOutcome.GRANTED, grant=_grant(),
    )
    assert decision.outcome is DecisionOutcome.GRANTED


def test_granted_decision_rejects_missing_grant():
    with pytest.raises(ValidationError):
        GrantDecision(
            decision_id=uuid4(), request_id=uuid4(),
            outcome=DecisionOutcome.GRANTED, grant=None,
        )


def test_granted_decision_rejects_reason_code_present():
    with pytest.raises(ValidationError):
        GrantDecision(
            decision_id=uuid4(), request_id=uuid4(),
            outcome=DecisionOutcome.GRANTED, grant=_grant(),
            reason_code="BUDGET_EXHAUSTED",
        )


def test_granted_decision_rejects_next_eligible_at_present():
    with pytest.raises(ValidationError):
        GrantDecision(
            decision_id=uuid4(), request_id=uuid4(),
            outcome=DecisionOutcome.GRANTED, grant=_grant(),
            next_eligible_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )


def test_denied_decision_valid_with_reason_and_no_grant():
    decision = GrantDecision(
        decision_id=uuid4(), request_id=uuid4(),
        outcome=DecisionOutcome.DENIED, reason_code="SCOPE_VIOLATION",
    )
    assert decision.grant is None


def test_denied_decision_valid_with_next_eligible_at_present():
    decision = GrantDecision(
        decision_id=uuid4(), request_id=uuid4(),
        outcome=DecisionOutcome.DENIED, reason_code="QUIET_HOURS",
        next_eligible_at=datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc),
    )
    assert decision.next_eligible_at is not None


def test_denied_decision_valid_with_next_eligible_at_null():
    decision = GrantDecision(
        decision_id=uuid4(), request_id=uuid4(),
        outcome=DecisionOutcome.DENIED, reason_code="OPTED_OUT",
        next_eligible_at=None,
    )
    assert decision.next_eligible_at is None


def test_denied_decision_rejects_missing_reason_code():
    with pytest.raises(ValidationError):
        GrantDecision(
            decision_id=uuid4(), request_id=uuid4(), outcome=DecisionOutcome.DENIED,
        )


def test_denied_decision_rejects_grant_present():
    with pytest.raises(ValidationError):
        GrantDecision(
            decision_id=uuid4(), request_id=uuid4(),
            outcome=DecisionOutcome.DENIED, reason_code="SCOPE_VIOLATION",
            grant=_grant(),
        )


def test_deferred_decision_valid_with_reason_and_next_eligible_at():
    decision = GrantDecision(
        decision_id=uuid4(), request_id=uuid4(),
        outcome=DecisionOutcome.DEFERRED, reason_code="BUDGET_EXHAUSTED",
        next_eligible_at=datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc),
    )
    assert decision.outcome is DecisionOutcome.DEFERRED


def test_deferred_decision_rejects_missing_reason_code():
    with pytest.raises(ValidationError):
        GrantDecision(
            decision_id=uuid4(), request_id=uuid4(),
            outcome=DecisionOutcome.DEFERRED,
            next_eligible_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
        )


def test_deferred_decision_rejects_missing_next_eligible_at():
    with pytest.raises(ValidationError):
        GrantDecision(
            decision_id=uuid4(), request_id=uuid4(),
            outcome=DecisionOutcome.DEFERRED, reason_code="BUDGET_EXHAUSTED",
        )


def test_deferred_decision_rejects_grant_present():
    with pytest.raises(ValidationError):
        GrantDecision(
            decision_id=uuid4(), request_id=uuid4(),
            outcome=DecisionOutcome.DEFERRED, reason_code="BUDGET_EXHAUSTED",
            next_eligible_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
            grant=_grant(),
        )


def test_decision_rejects_unapproved_outcome_value():
    with pytest.raises(ValidationError):
        GrantDecision(decision_id=uuid4(), request_id=uuid4(), outcome="MAYBE")
