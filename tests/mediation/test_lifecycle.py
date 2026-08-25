"""Grant lifecycle transitions — Design Lock §9."""

from __future__ import annotations

import datetime as dt

import pytest

from sampark.budget.store import GrantIssued, InMemoryGrantIssuer, InMemoryMediationLedger
from sampark.contracts import GrantState, RiskItem
from sampark.mediation import lifecycle

DECISION_AT = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)


@pytest.fixture()
def granted(make_candidate):
    well_funded = {
        "cust-1": (
            RiskItem(
                risk_id="placeholder-cust-1", source="abandoned_checkout",
                amount_paise=100_000_000, root_cause="price_hesitation", detected_at=DECISION_AT,
            ),
        )
    }
    ledger = InMemoryMediationLedger(well_funded, merchant_budget_paise_per_window=1_000_000)
    issuer = InMemoryGrantIssuer()
    candidate = make_candidate()
    result = issuer.issue_grant(ledger, candidate, 500, DECISION_AT)
    assert isinstance(result, GrantIssued)
    return ledger, result.grant


def test_reserved_to_executing(granted):
    ledger, grant = granted
    updated = lifecycle.execute(ledger, grant.grant_id, at=DECISION_AT)
    assert updated.state is GrantState.EXECUTING


def test_execute_is_idempotent(granted):
    ledger, grant = granted
    lifecycle.execute(ledger, grant.grant_id, at=DECISION_AT)
    updated_again = lifecycle.execute(ledger, grant.grant_id, at=DECISION_AT)
    assert updated_again.state is GrantState.EXECUTING


def test_executing_to_confirmed_settles_margin(granted):
    ledger, grant = granted
    lifecycle.execute(ledger, grant.grant_id, at=DECISION_AT)
    lifecycle.confirm(ledger, grant.grant_id, at=DECISION_AT, actual_spend_paise=1_000)
    record = ledger.get_grant_by_grant_id(grant.grant_id)
    assert record.grant.state is GrantState.CONFIRMED
    merchant_remaining, _ = ledger.remaining_margin_paise("cust-1", grant.send_after.date())


def test_reserved_to_rolled_back_releases_margin(granted):
    ledger, grant = granted
    merchant_before, _ = ledger.remaining_margin_paise("cust-1", grant.send_after.date())
    lifecycle.rollback(ledger, grant.grant_id, at=DECISION_AT)
    merchant_after, _ = ledger.remaining_margin_paise("cust-1", grant.send_after.date())
    assert merchant_after == merchant_before + grant.incentive_ceiling_paise
    record = ledger.get_grant_by_grant_id(grant.grant_id)
    assert record.grant.state is GrantState.ROLLED_BACK


def test_executing_to_rolled_back_is_legal(granted):
    ledger, grant = granted
    lifecycle.execute(ledger, grant.grant_id, at=DECISION_AT)
    updated = lifecycle.rollback(ledger, grant.grant_id, at=DECISION_AT)
    assert updated.state is GrantState.ROLLED_BACK


def test_reserved_to_expired_releases_margin(granted):
    ledger, grant = granted
    merchant_before, _ = ledger.remaining_margin_paise("cust-1", grant.send_after.date())
    lifecycle.expire(ledger, grant.grant_id, at=grant.expires_at + dt.timedelta(minutes=1))
    merchant_after, _ = ledger.remaining_margin_paise("cust-1", grant.send_after.date())
    assert merchant_after == merchant_before + grant.incentive_ceiling_paise


def test_confirmed_to_anything_is_illegal(granted):
    ledger, grant = granted
    lifecycle.execute(ledger, grant.grant_id, at=DECISION_AT)
    lifecycle.confirm(ledger, grant.grant_id, at=DECISION_AT, actual_spend_paise=0)
    with pytest.raises(lifecycle.IllegalTransitionError):
        lifecycle.rollback(ledger, grant.grant_id, at=DECISION_AT)


def test_reserved_to_confirmed_directly_is_illegal(granted):
    ledger, grant = granted
    with pytest.raises(lifecycle.IllegalTransitionError):
        lifecycle._transition(ledger, grant.grant_id, GrantState.CONFIRMED, DECISION_AT)


def test_rolled_back_grant_is_terminal(granted):
    ledger, grant = granted
    lifecycle.rollback(ledger, grant.grant_id, at=DECISION_AT)
    with pytest.raises(lifecycle.IllegalTransitionError):
        lifecycle.execute(ledger, grant.grant_id, at=DECISION_AT)


def test_rollback_is_idempotent(granted):
    ledger, grant = granted
    lifecycle.rollback(ledger, grant.grant_id, at=DECISION_AT)
    merchant_after_first, _ = ledger.remaining_margin_paise("cust-1", grant.send_after.date())
    lifecycle.rollback(ledger, grant.grant_id, at=DECISION_AT)  # must not double-release
    merchant_after_second, _ = ledger.remaining_margin_paise("cust-1", grant.send_after.date())
    assert merchant_after_first == merchant_after_second


def test_unknown_grant_id_raises(granted):
    ledger, _ = granted
    import uuid

    with pytest.raises(lifecycle.UnknownGrantError):
        lifecycle.execute(ledger, uuid.uuid4(), at=DECISION_AT)


def test_sweep_expired_moves_past_ttl_reserved_grants(granted):
    ledger, grant = granted
    result = lifecycle.sweep_expired(ledger, now=grant.expires_at + dt.timedelta(minutes=1))
    assert grant.grant_id in result.expired_grant_ids
    record = ledger.get_grant_by_grant_id(grant.grant_id)
    assert record.grant.state is GrantState.EXPIRED


def test_sweep_expired_leaves_unexpired_grants_alone(granted):
    ledger, grant = granted
    result = lifecycle.sweep_expired(ledger, now=DECISION_AT)  # well before expires_at
    assert grant.grant_id not in result.expired_grant_ids
    record = ledger.get_grant_by_grant_id(grant.grant_id)
    assert record.grant.state is GrantState.RESERVED


def test_expiry_releases_the_claim_and_a_retry_can_win_the_window(granted):
    ledger, grant = granted
    lifecycle.sweep_expired(ledger, now=grant.expires_at + dt.timedelta(minutes=1))
    from sampark.budget.store import InMemoryGrantIssuer

    issuer = InMemoryGrantIssuer()
    assert not ledger.has_active_claim("cust-1", grant.send_after.date())
