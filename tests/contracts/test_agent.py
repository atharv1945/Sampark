"""Agent / CapabilityScope — CONTRACTS.md Part 1."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sampark.contracts import Agent, AgentState, CapabilityScope


def test_agent_valid_construction():
    agent = Agent(
        agent_id="agent-1",
        public_key="pk-bytes-b64",
        publisher="Acme Recovery Co",
        state=AgentState.ACTIVE,
    )
    assert agent.agent_id == "agent-1"
    assert agent.state is AgentState.ACTIVE


def test_agent_strike_count_defaults_to_zero():
    agent = Agent(
        agent_id="agent-1", public_key="pk", publisher="Acme",
        state=AgentState.REVOKED,
    )
    assert agent.strike_count == 0


def test_agent_rejects_negative_strike_count():
    with pytest.raises(ValidationError):
        Agent(
            agent_id="agent-1", public_key="pk", publisher="Acme",
            state=AgentState.ACTIVE, strike_count=-1,
        )


def test_agent_accepts_zero_strike_count_boundary():
    agent = Agent(
        agent_id="agent-1", public_key="pk", publisher="Acme",
        state=AgentState.ACTIVE, strike_count=0,
    )
    assert agent.strike_count == 0


def test_agent_rejects_unapproved_state_value():
    with pytest.raises(ValidationError):
        Agent(
            agent_id="agent-1", public_key="pk", publisher="Acme",
            state="SUSPENDED",
        )


def test_agent_rejects_unapproved_extra_field():
    with pytest.raises(ValidationError):
        Agent(
            agent_id="agent-1", public_key="pk", publisher="Acme",
            state=AgentState.ACTIVE, capability_scope={"anything": 1},
        )


def test_capability_scope_valid_construction():
    scope = CapabilityScope(
        allowed_channels=["whatsapp", "sms"],
        allowed_intents=["recover_cart"],
        allowed_risk_sources=["cart_abandonment"],
        max_incentive_bps=500,
        max_requests_per_hour=10,
    )
    assert scope.max_incentive_bps == 500


def test_capability_scope_rejects_negative_max_incentive_bps():
    with pytest.raises(ValidationError):
        CapabilityScope(
            allowed_channels=[], allowed_intents=[], allowed_risk_sources=[],
            max_incentive_bps=-1, max_requests_per_hour=10,
        )


def test_capability_scope_rejects_negative_max_requests_per_hour():
    with pytest.raises(ValidationError):
        CapabilityScope(
            allowed_channels=[], allowed_intents=[], allowed_risk_sources=[],
            max_incentive_bps=0, max_requests_per_hour=-1,
        )


def test_capability_scope_accepts_zero_boundary_values():
    scope = CapabilityScope(
        allowed_channels=[], allowed_intents=[], allowed_risk_sources=[],
        max_incentive_bps=0, max_requests_per_hour=0,
    )
    assert scope.max_incentive_bps == 0
    assert scope.max_requests_per_hour == 0


def test_capability_scope_has_no_agent_id_field():
    """CONTRACTS.md's canonical CapabilityScope has no agent_id — that FK
    is a persistence-layer addition in sampark/schema.sql, not part of
    the domain contract."""
    with pytest.raises(ValidationError):
        CapabilityScope(
            agent_id="agent-1",
            allowed_channels=[], allowed_intents=[], allowed_risk_sources=[],
            max_incentive_bps=0, max_requests_per_hour=0,
        )
