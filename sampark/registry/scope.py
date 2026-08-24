"""Scope evaluation — spec §6.2, §8.1.

Pure: no database calls, no network, no Redis, no allocator import.
Reads only through the injected AgentRepository / RiskItemRepository
protocols (sampark/registry/store.py) — this module contains no I/O of
its own, and never writes grant_requests (that is Phase 4's job, at
issuance time).

Returns None when the request is within the agent's declared capability
— there is no allocator yet to forward a verified request to in Phase 3.
Returns a DENIED GrantDecision otherwise. Never constructs GRANTED or
DEFERRED: both require a Grant, and only Phase 4's allocator may create
one.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sampark.contracts import AgentState, DecisionOutcome, GrantDecision, GrantRequest
from sampark.registry.reason_codes import (
    AGENT_REVOKED,
    CHANNEL_NOT_ALLOWED,
    CUSTOMER_RISK_ITEM_MISMATCH,
    INCENTIVE_CEILING_EXCEEDED,
    INTENT_NOT_ALLOWED,
    INVALID_SIGNATURE,
    RISK_SOURCE_NOT_ALLOWED,
    UNKNOWN_AGENT,
    UNKNOWN_RISK_ITEM,
)
from sampark.registry.signing import verify_signature
from sampark.registry.store import AgentRepository, RiskItemRepository


def _deny(request_id: UUID, reason_code: str) -> GrantDecision:
    return GrantDecision(
        decision_id=uuid4(),
        request_id=request_id,
        outcome=DecisionOutcome.DENIED,
        reason_code=reason_code,
        human_readable=None,
        next_eligible_at=None,
        grant=None,
    )


def evaluate_scope(
    request: GrantRequest,
    agent_repo: AgentRepository,
    risk_item_repo: RiskItemRepository,
) -> GrantDecision | None:
    """LOCKED DECISIONS evaluation order, steps 1-10.

    1. agent exists            6. customer_id matches risk_item's owner
    2. verify signature        7. requested_channel allowed
    3. agent is ACTIVE         8. intent allowed
    4. capability scope exists 9. risk source (from risk_item) allowed
    5. risk item exists        10. requested incentive <= scope ceiling
    """
    agent = agent_repo.get_agent(request.agent_id)
    if agent is None:
        return _deny(request.request_id, UNKNOWN_AGENT)

    if not verify_signature(agent.public_key, request.canonical_bytes(), request.signature):
        return _deny(request.request_id, INVALID_SIGNATURE)

    if agent.state is not AgentState.ACTIVE:
        return _deny(request.request_id, AGENT_REVOKED)

    scope = agent_repo.get_capability_scope(agent.agent_id)
    if scope is None:
        # AGENT ||--|| CAPABILITY_SCOPE is 1:1 by schema, and
        # AgentRepository.register() always writes both together — an
        # ACTIVE agent with no scope row is registry data corruption,
        # not a request-level denial, so this is not one of the nine
        # approved reason codes.
        raise RuntimeError(f"ACTIVE agent {agent.agent_id!r} has no capability scope on record")

    record = risk_item_repo.get_risk_item(request.risk_id)
    if record is None:
        return _deny(request.request_id, UNKNOWN_RISK_ITEM)

    if record.customer_id != request.customer_id:
        return _deny(request.request_id, CUSTOMER_RISK_ITEM_MISMATCH)

    if request.requested_channel not in scope.allowed_channels:
        return _deny(request.request_id, CHANNEL_NOT_ALLOWED)

    if request.intent not in scope.allowed_intents:
        return _deny(request.request_id, INTENT_NOT_ALLOWED)

    if record.risk_item.source not in scope.allowed_risk_sources:
        return _deny(request.request_id, RISK_SOURCE_NOT_ALLOWED)

    if request.requested_max_incentive_bps > scope.max_incentive_bps:
        return _deny(request.request_id, INCENTIVE_CEILING_EXCEEDED)

    return None
