"""sampark/registry/strikes.py — strike accumulation, threshold, and
revocation. In-memory repository only: this module is pure Agent-state
transitions plus one save_agent() write, no Postgres dependency needed
to exercise the policy.
"""

from __future__ import annotations

from sampark.contracts import AgentState
from sampark.registry import reason_codes
from sampark.registry.scope import evaluate_scope
from sampark.registry.strikes import STRIKE_THRESHOLD, record_scope_denial, revoke


def test_invalid_signature_does_not_strike(agent_repo, registered_agent):
    updated = record_scope_denial(agent_repo, registered_agent, reason_codes.INVALID_SIGNATURE)
    assert updated.strike_count == 0
    assert updated.state is AgentState.ACTIVE


def test_unknown_agent_reason_does_not_strike(agent_repo, registered_agent):
    updated = record_scope_denial(agent_repo, registered_agent, reason_codes.UNKNOWN_AGENT)
    assert updated.strike_count == 0


def test_agent_revoked_reason_does_not_strike(agent_repo, registered_agent):
    updated = record_scope_denial(agent_repo, registered_agent, reason_codes.AGENT_REVOKED)
    assert updated.strike_count == 0


def test_declared_scope_violation_increments_strike(agent_repo, registered_agent):
    updated = record_scope_denial(agent_repo, registered_agent, reason_codes.CHANNEL_NOT_ALLOWED)
    assert updated.strike_count == 1
    assert updated.state is AgentState.ACTIVE
    assert agent_repo.get_agent(registered_agent.agent_id).strike_count == 1


def test_third_capability_violation_revokes(agent_repo, registered_agent):
    agent = registered_agent
    for _ in range(STRIKE_THRESHOLD - 1):
        agent = record_scope_denial(agent_repo, agent, reason_codes.CHANNEL_NOT_ALLOWED)
        assert agent.state is AgentState.ACTIVE

    agent = record_scope_denial(agent_repo, agent, reason_codes.INTENT_NOT_ALLOWED)

    assert agent.strike_count == STRIKE_THRESHOLD
    assert agent.state is AgentState.REVOKED
    assert agent_repo.get_agent(registered_agent.agent_id).state is AgentState.REVOKED


def test_revoked_agent_remains_denied_by_scope_evaluation(
    agent_repo, risk_item_repo, registered_agent, risk_item, make_request
):
    agent = registered_agent
    for _ in range(STRIKE_THRESHOLD):
        agent = record_scope_denial(agent_repo, agent, reason_codes.CHANNEL_NOT_ALLOWED)
    assert agent.state is AgentState.REVOKED

    request = make_request()  # otherwise perfectly in-scope
    decision = evaluate_scope(request, agent_repo, risk_item_repo)
    assert decision.reason_code == reason_codes.AGENT_REVOKED


def test_manual_revoke(agent_repo, registered_agent):
    revoked = revoke(registered_agent)
    agent_repo.save_agent(revoked)

    stored = agent_repo.get_agent(registered_agent.agent_id)
    assert stored.state is AgentState.REVOKED
    assert stored.strike_count == registered_agent.strike_count  # manual revoke doesn't touch strikes
