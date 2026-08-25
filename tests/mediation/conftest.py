"""Shared fixtures for sampark/mediation/ tests."""

from __future__ import annotations

import datetime as dt
from uuid import uuid4

import pytest

from sampark.allocator.candidate import build_candidate
from sampark.contracts import Agent, AgentState, CapabilityScope, GrantRequest, RiskItem
from sampark.registry.keys import generate_keypair
from sampark.registry.store import InMemoryAgentRepository, InMemoryRiskItemRepository

DETECTED_AT = dt.datetime(2025, 9, 10, 6, 0, tzinfo=dt.timezone.utc)


@pytest.fixture()
def registered_agent():
    """A real Ed25519 keypair + narrow capability scope, registered in
    an InMemoryAgentRepository — mirrors tests/registry/conftest.py's
    pattern so mediation tests exercise the REAL Phase 3 scope check,
    never a stub."""
    keypair = generate_keypair()
    agent = Agent(
        agent_id="cart_recovery_agent", public_key=keypair.public_key_b64,
        publisher="Test", state=AgentState.ACTIVE, strike_count=0,
    )
    scope = CapabilityScope(
        allowed_channels=["whatsapp"], allowed_intents=["cart_recovery"],
        allowed_risk_sources=["abandoned_checkout"], max_incentive_bps=500, max_requests_per_hour=1000,
    )
    agent_repo = InMemoryAgentRepository()
    agent_repo.register(agent, scope)
    return agent_repo, keypair


@pytest.fixture()
def risk_item_repo():
    repo = InMemoryRiskItemRepository()
    item = RiskItem(
        risk_id="risk-1", source="abandoned_checkout", amount_paise=1_000_000,
        root_cause="price_hesitation", detected_at=DETECTED_AT,
    )
    repo.add(item, customer_id="cust-1")
    return repo


@pytest.fixture()
def make_signed_request(registered_agent):
    agent_repo, keypair = registered_agent

    def _make(
        risk_id: str = "risk-1",
        customer_id: str = "cust-1",
        intent: str = "cart_recovery",
        requested_channel: str = "whatsapp",
        requested_max_incentive_bps: int = 500,
    ) -> GrantRequest:
        from uuid import uuid4

        unsigned = GrantRequest(
            request_id=uuid4(), agent_id="cart_recovery_agent", customer_id=customer_id, risk_id=risk_id,
            intent=intent, requested_channel=requested_channel,
            requested_max_incentive_bps=requested_max_incentive_bps, issued_at=DETECTED_AT,
            signature="placeholder",
        )
        signature = keypair.sign(unsigned.canonical_bytes())
        return unsigned.model_copy(update={"signature": signature})

    return _make


@pytest.fixture()
def make_candidate():
    def _make(
        customer_id: str = "cust-1",
        risk_id: str = "risk-1",
        amount_paise: int = 500_000,
        bps: int = 500,
        agent_id: str = "cart_recovery_agent",
        intent: str = "cart_recovery",
        channel: str = "whatsapp",
        proposed_send_after: dt.datetime = dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc),
        source: str = "abandoned_checkout",
        root_cause: str = "price_hesitation",
    ):
        item = RiskItem(
            risk_id=risk_id, source=source, amount_paise=amount_paise,
            root_cause=root_cause, detected_at=DETECTED_AT,
        )
        request = GrantRequest(
            request_id=uuid4(), agent_id=agent_id, customer_id=customer_id, risk_id=risk_id,
            intent=intent, requested_channel=channel, requested_max_incentive_bps=bps,
            issued_at=DETECTED_AT, signature="sig",
        )
        return build_candidate(request, item, customer_id, proposed_send_after)

    return _make
