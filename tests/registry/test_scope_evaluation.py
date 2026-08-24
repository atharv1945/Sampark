"""sampark/registry/scope.py — evaluate_scope, in-memory repositories.

Covers LOCKED DECISIONS steps 1-10: agent existence, agent state,
customer/risk-item ownership, and each declared-capability dimension
(channel, intent, risk source, incentive ceiling), including the exact
incentive boundary.
"""

from __future__ import annotations

from sampark.contracts import AgentState, DecisionOutcome
from sampark.registry import reason_codes
from sampark.registry.scope import evaluate_scope


def test_in_scope_request_returns_none(agent_repo, risk_item_repo, registered_agent, risk_item, make_request):
    request = make_request()
    assert evaluate_scope(request, agent_repo, risk_item_repo) is None


def test_unknown_agent_is_denied(agent_repo, risk_item_repo, risk_item, make_request):
    request = make_request(agent_id="ghost-agent")
    decision = evaluate_scope(request, agent_repo, risk_item_repo)
    assert decision.outcome is DecisionOutcome.DENIED
    assert decision.reason_code == reason_codes.UNKNOWN_AGENT
    assert decision.grant is None
    assert decision.request_id == request.request_id


def test_revoked_agent_is_denied(agent_repo, risk_item_repo, registered_agent, risk_item, make_request):
    revoked = registered_agent.model_copy(update={"state": AgentState.REVOKED})
    agent_repo.save_agent(revoked)

    request = make_request()
    decision = evaluate_scope(request, agent_repo, risk_item_repo)
    assert decision.reason_code == reason_codes.AGENT_REVOKED


def test_allowed_channel_passes(agent_repo, risk_item_repo, registered_agent, risk_item, make_request):
    request = make_request(requested_channel="sms")
    assert evaluate_scope(request, agent_repo, risk_item_repo) is None


def test_disallowed_channel_is_denied(agent_repo, risk_item_repo, registered_agent, risk_item, make_request):
    request = make_request(requested_channel="voice")
    decision = evaluate_scope(request, agent_repo, risk_item_repo)
    assert decision.reason_code == reason_codes.CHANNEL_NOT_ALLOWED


def test_allowed_intent_passes(agent_repo, risk_item_repo, registered_agent, risk_item, make_request):
    request = make_request(intent="recover_cart")
    assert evaluate_scope(request, agent_repo, risk_item_repo) is None


def test_disallowed_intent_is_denied(agent_repo, risk_item_repo, registered_agent, risk_item, make_request):
    request = make_request(intent="recover_mandate")
    decision = evaluate_scope(request, agent_repo, risk_item_repo)
    assert decision.reason_code == reason_codes.INTENT_NOT_ALLOWED


def test_allowed_risk_source_passes(agent_repo, risk_item_repo, registered_agent, risk_item, make_request):
    request = make_request(risk_id=risk_item.risk_id)  # source="abandoned_checkout", in scope
    assert evaluate_scope(request, agent_repo, risk_item_repo) is None


def test_disallowed_risk_source_is_denied(agent_repo, risk_item_repo, registered_agent, make_risk_item, make_request):
    make_risk_item(risk_id="risk-2", customer_id="cust-1", source="failed_payment")
    request = make_request(risk_id="risk-2")
    decision = evaluate_scope(request, agent_repo, risk_item_repo)
    assert decision.reason_code == reason_codes.RISK_SOURCE_NOT_ALLOWED


def test_unknown_risk_item_is_denied(agent_repo, risk_item_repo, registered_agent, make_request):
    request = make_request(risk_id="does-not-exist")
    decision = evaluate_scope(request, agent_repo, risk_item_repo)
    assert decision.reason_code == reason_codes.UNKNOWN_RISK_ITEM


def test_customer_risk_item_mismatch_is_denied(agent_repo, risk_item_repo, registered_agent, risk_item, make_request):
    request = make_request(customer_id="someone-else")
    decision = evaluate_scope(request, agent_repo, risk_item_repo)
    assert decision.reason_code == reason_codes.CUSTOMER_RISK_ITEM_MISMATCH


def test_incentive_below_ceiling_passes(agent_repo, risk_item_repo, registered_agent, risk_item, make_request):
    request = make_request(requested_max_incentive_bps=100)  # scope ceiling is 200
    assert evaluate_scope(request, agent_repo, risk_item_repo) is None


def test_incentive_exactly_at_ceiling_passes(agent_repo, risk_item_repo, registered_agent, risk_item, make_request):
    request = make_request(requested_max_incentive_bps=200)  # == ceiling, must pass
    assert evaluate_scope(request, agent_repo, risk_item_repo) is None


def test_incentive_above_ceiling_is_denied(agent_repo, risk_item_repo, registered_agent, risk_item, make_request):
    request = make_request(requested_max_incentive_bps=201)
    decision = evaluate_scope(request, agent_repo, risk_item_repo)
    assert decision.reason_code == reason_codes.INCENTIVE_CEILING_EXCEEDED
