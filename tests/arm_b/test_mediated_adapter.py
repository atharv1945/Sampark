"""agents/mediated.py — ContactAction -> signed GrantRequest, Design Lock §13.2."""

from __future__ import annotations

import datetime as dt

from agents.mediated import request_id_for, to_grant_request
from agents.types import ContactAction
from sampark.registry.keys import generate_keypair
from sampark.registry.signing import verify_signature

SCHEDULED_AT = dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc)


def _action() -> ContactAction:
    return ContactAction(
        agent_id="cart_recovery_agent", risk_id="risk-1", customer_id="cust-1",
        channel="whatsapp", intent="cart_recovery", incentive_bps=500, scheduled_at=SCHEDULED_AT,
    )


def test_request_id_is_deterministic_given_seed_agent_risk():
    a = request_id_for(42, "cart_recovery_agent", "risk-1")
    b = request_id_for(42, "cart_recovery_agent", "risk-1")
    assert a == b


def test_request_id_differs_across_seeds():
    a = request_id_for(42, "cart_recovery_agent", "risk-1")
    b = request_id_for(7, "cart_recovery_agent", "risk-1")
    assert a != b


def test_request_id_differs_across_risk_ids():
    a = request_id_for(42, "cart_recovery_agent", "risk-1")
    b = request_id_for(42, "cart_recovery_agent", "risk-2")
    assert a != b


def test_to_grant_request_produces_a_verifiably_signed_request():
    keypair = generate_keypair()
    request = to_grant_request(_action(), seed=42, keypair=keypair)
    assert request.request_id == request_id_for(42, "cart_recovery_agent", "risk-1")
    assert request.agent_id == "cart_recovery_agent"
    assert request.customer_id == "cust-1"
    assert request.risk_id == "risk-1"
    assert request.intent == "cart_recovery"
    assert request.requested_channel == "whatsapp"
    assert request.requested_max_incentive_bps == 500
    assert verify_signature(keypair.public_key_b64, request.canonical_bytes(), request.signature)


def test_to_grant_request_signature_is_invalid_under_a_different_key():
    keypair = generate_keypair()
    other_keypair = generate_keypair()
    request = to_grant_request(_action(), seed=42, keypair=keypair)
    assert not verify_signature(other_keypair.public_key_b64, request.canonical_bytes(), request.signature)
