"""Strike accumulation and revocation — spec §8.1, §12.3.

Pure Agent-state transitions (apply_strike, revoke) plus one
orchestration function (record_scope_denial) that ties a
sampark/registry/scope.py denial's reason_code to the strike policy and
persists the result through the injected AgentRepository. scope.py
itself never calls into this module — striking is a separate, explicit
step, not a side effect hidden inside evaluation.

Strike policy (LOCKED DECISIONS): only a declared-scope violation by a
signature-verified, ACTIVE agent counts — channel, intent, risk source,
or incentive ceiling. unknown_agent, invalid_signature, and
agent_revoked never strike, so a forged or spoofed agent_id can never be
used to quarantine the real agent it impersonates.

Reaching STRIKE_THRESHOLD strikes transitions ACTIVE -> REVOKED.
Revocation is permanent in Phase 3: no QUARANTINED state, no
reactivation flow.
"""

from __future__ import annotations

from sampark.contracts import Agent, AgentState
from sampark.registry.reason_codes import (
    CHANNEL_NOT_ALLOWED,
    INCENTIVE_CEILING_EXCEEDED,
    INTENT_NOT_ALLOWED,
    RISK_SOURCE_NOT_ALLOWED,
)
from sampark.registry.store import AgentRepository

STRIKE_THRESHOLD = 3

STRIKE_WORTHY_REASON_CODES = frozenset(
    {
        CHANNEL_NOT_ALLOWED,
        INTENT_NOT_ALLOWED,
        RISK_SOURCE_NOT_ALLOWED,
        INCENTIVE_CEILING_EXCEEDED,
    }
)


def apply_strike(agent: Agent) -> Agent:
    """Increment strike_count; auto-revoke at STRIKE_THRESHOLD.

    Callers must only invoke this for an agent scope evaluation actually
    resolved as ACTIVE — see record_scope_denial.
    """
    if agent.state is not AgentState.ACTIVE:
        raise ValueError(f"apply_strike called on a non-ACTIVE agent: {agent.agent_id!r}")

    new_strike_count = agent.strike_count + 1
    new_state = AgentState.REVOKED if new_strike_count >= STRIKE_THRESHOLD else AgentState.ACTIVE
    return agent.model_copy(update={"strike_count": new_strike_count, "state": new_state})


def revoke(agent: Agent) -> Agent:
    """Manual, immediate revocation — independent of strike_count."""
    return agent.model_copy(update={"state": AgentState.REVOKED})


def record_scope_denial(agent_repo: AgentRepository, agent: Agent, reason_code: str | None) -> Agent:
    """Apply and persist a strike iff `reason_code` is strike-worthy.

    Returns `agent` unchanged when the reason code is not strike-worthy
    (unknown_agent, invalid_signature, agent_revoked), so callers can
    always use the return value as the agent's current state after a
    denial.
    """
    if reason_code not in STRIKE_WORTHY_REASON_CODES:
        return agent
    updated = apply_strike(agent)
    agent_repo.save_agent(updated)
    return updated
