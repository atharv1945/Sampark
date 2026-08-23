"""Customer / ContactState — CONTRACTS.md Part 1."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from sampark.contracts import ContactState, Customer


def test_customer_valid_construction_with_both_hashes():
    customer = Customer(customer_id="cust-1", phone_hash="ph", email_hash="eh")
    assert customer.customer_id == "cust-1"


def test_customer_hashes_are_optional():
    customer = Customer(customer_id="cust-1")
    assert customer.phone_hash is None
    assert customer.email_hash is None


def test_customer_rejects_unapproved_extra_field():
    with pytest.raises(ValidationError):
        Customer(customer_id="cust-1", loyalty_tier="gold")


def test_contact_state_valid_construction():
    state = ContactState(
        contacts_24h=1, contacts_7d=3,
        optouts_by_channel={"sms": False}, consent_scopes={"cart_recovery": True},
        fatigue_score=0.2,
    )
    assert state.contacts_7d == 3


def test_contact_state_defaults_contacts_to_zero():
    state = ContactState(optouts_by_channel={}, consent_scopes={}, fatigue_score=0.0)
    assert state.contacts_24h == 0
    assert state.contacts_7d == 0


def test_contact_state_rejects_negative_contacts_24h():
    with pytest.raises(ValidationError):
        ContactState(
            contacts_24h=-1, contacts_7d=0,
            optouts_by_channel={}, consent_scopes={}, fatigue_score=0.0,
        )


def test_contact_state_rejects_negative_contacts_7d():
    with pytest.raises(ValidationError):
        ContactState(
            contacts_24h=0, contacts_7d=-1,
            optouts_by_channel={}, consent_scopes={}, fatigue_score=0.0,
        )


def test_contact_state_rejects_contacts_7d_below_contacts_24h():
    with pytest.raises(ValidationError):
        ContactState(
            contacts_24h=5, contacts_7d=4,
            optouts_by_channel={}, consent_scopes={}, fatigue_score=0.0,
        )


def test_contact_state_accepts_contacts_7d_equal_to_contacts_24h_boundary():
    state = ContactState(
        contacts_24h=3, contacts_7d=3,
        optouts_by_channel={}, consent_scopes={}, fatigue_score=0.0,
    )
    assert state.contacts_7d == state.contacts_24h


def test_contact_state_fatigue_score_rejects_below_zero():
    with pytest.raises(ValidationError):
        ContactState(optouts_by_channel={}, consent_scopes={}, fatigue_score=-0.01)


def test_contact_state_fatigue_score_rejects_above_one():
    with pytest.raises(ValidationError):
        ContactState(optouts_by_channel={}, consent_scopes={}, fatigue_score=1.01)


def test_contact_state_fatigue_score_accepts_zero_and_one_boundaries():
    low = ContactState(optouts_by_channel={}, consent_scopes={}, fatigue_score=0.0)
    high = ContactState(optouts_by_channel={}, consent_scopes={}, fatigue_score=1.0)
    assert low.fatigue_score == 0.0
    assert high.fatigue_score == 1.0


def test_contact_state_last_contact_at_is_optional():
    state = ContactState(optouts_by_channel={}, consent_scopes={}, fatigue_score=0.5)
    assert state.last_contact_at is None


def test_contact_state_accepts_explicit_last_contact_at():
    when = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)
    state = ContactState(
        optouts_by_channel={}, consent_scopes={}, fatigue_score=0.5,
        last_contact_at=when,
    )
    assert state.last_contact_at == when


def test_contact_state_has_no_customer_id_field():
    with pytest.raises(ValidationError):
        ContactState(
            customer_id="cust-1",
            optouts_by_channel={}, consent_scopes={}, fatigue_score=0.5,
        )
