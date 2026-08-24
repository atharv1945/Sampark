"""Shared fixtures for sampark/registry/ tests — in-memory repositories,
one keypair, one narrow capability scope, and factory fixtures for
building signed requests / risk items with per-test overrides.

Deliberately in-memory only (no Postgres) so scope/strike tests run with
no external dependency; tests/registry/test_registration.py covers the
same repository contract against real Postgres separately.
"""

from __future__ import annotations

import datetime as dt
from uuid import uuid4

import pytest

from sampark.contracts import Agent, AgentState, CapabilityScope, GrantRequest, RiskItem
from sampark.registry import AgentKeypair, InMemoryAgentRepository, InMemoryRiskItemRepository, generate_keypair

ISSUED_AT = dt.datetime(2026, 8, 25, 9, 0, tzinfo=dt.timezone.utc)
DETECTED_AT = dt.datetime(2026, 8, 24, 9, 0, tzinfo=dt.timezone.utc)


@pytest.fixture()
def keypair() -> AgentKeypair:
    return generate_keypair()


@pytest.fixture()
def agent_repo() -> InMemoryAgentRepository:
    return InMemoryAgentRepository()


@pytest.fixture()
def risk_item_repo() -> InMemoryRiskItemRepository:
    return InMemoryRiskItemRepository()


@pytest.fixture()
def scope() -> CapabilityScope:
    return CapabilityScope(
        allowed_channels=["sms"],
        allowed_intents=["recover_cart"],
        allowed_risk_sources=["abandoned_checkout"],
        max_incentive_bps=200,
        max_requests_per_hour=10,
    )


@pytest.fixture()
def registered_agent(agent_repo, keypair, scope) -> Agent:
    agent = Agent(
        agent_id="agent-1",
        public_key=keypair.public_key_b64,
        publisher="Acme Recovery Co",
        state=AgentState.ACTIVE,
        strike_count=0,
    )
    agent_repo.register(agent, scope)
    return agent


@pytest.fixture()
def make_risk_item(risk_item_repo):
    """Factory: build a RiskItem, register it in risk_item_repo, return it."""

    def _make(
        risk_id: str = "risk-1",
        customer_id: str = "cust-1",
        source: str = "abandoned_checkout",
        amount_paise: int = 15_000,
        root_cause: str = "price_hesitation",
        detected_at: dt.datetime = DETECTED_AT,
    ) -> RiskItem:
        item = RiskItem(
            risk_id=risk_id, source=source, amount_paise=amount_paise,
            root_cause=root_cause, detected_at=detected_at,
        )
        risk_item_repo.add(item, customer_id=customer_id)
        return item

    return _make


@pytest.fixture()
def risk_item(make_risk_item) -> RiskItem:
    """The default risk item: matches `scope`'s allowed_risk_sources and
    `registered_agent`'s customer, so a default `make_request()` is
    in-scope end to end."""
    return make_risk_item()


@pytest.fixture()
def make_request(keypair):
    """Factory: build and sign a GrantRequest with per-test overrides."""

    def _make(
        agent_id: str = "agent-1",
        customer_id: str = "cust-1",
        risk_id: str = "risk-1",
        intent: str = "recover_cart",
        requested_channel: str = "sms",
        requested_max_incentive_bps: int = 100,
        issued_at: dt.datetime = ISSUED_AT,
        signer: AgentKeypair | None = None,
    ) -> GrantRequest:
        kp = signer if signer is not None else keypair
        unsigned = GrantRequest(
            request_id=uuid4(), agent_id=agent_id, customer_id=customer_id, risk_id=risk_id,
            intent=intent, requested_channel=requested_channel,
            requested_max_incentive_bps=requested_max_incentive_bps, issued_at=issued_at,
            signature="placeholder",
        )
        signature = kp.sign(unsigned.canonical_bytes())
        return unsigned.model_copy(update={"signature": signature})

    return _make
