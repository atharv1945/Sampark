"""ContactAction -> signed GrantRequest adapter — Design Lock §13.2.

The smallest possible bridge between Arm A's existing agent output
(agents.types.ContactAction, produced by the four UNCHANGED Phase 2
agents' select_actions()) and Phase 4's signed GrantRequest. Does not
touch agents/base.py, agents/types.py, or any of the four agent
classes — Arm B reuses their select_actions() output verbatim.

`request_id` is derived deterministically
(uuid5(NS_REQUEST, f"{seed}:{agent_id}:{risk_id}")) so Arm B produces
identical request IDs across repeated runs at the same seed (Design
Lock §16 determinism). Agent keypairs are generated fresh at runner
start (sim/arm_b.py) — signature BYTES therefore differ run to run, but
no decision reads the signature bytes themselves (only
verify_signature()'s True/False), so decisions and metrics stay
byte-identical.

`issued_at` is set to `action.scheduled_at` — Phase 2's ContactAction
conflates "when the agent decided to contact" and "the proposed contact
time" into one field, and Arm B does not introduce a second timestamp
concept on top of it.
"""

from __future__ import annotations

import uuid

from agents.types import ContactAction
from sampark.contracts import GrantRequest
from sampark.registry.keys import AgentKeypair

NS_REQUEST = uuid.UUID("8c3e7a1e-9b2a-4f2a-8e2a-1a7b9c2d4e6f")


def request_id_for(seed: int, agent_id: str, risk_id: str) -> uuid.UUID:
    return uuid.uuid5(NS_REQUEST, f"{seed}:{agent_id}:{risk_id}")


def to_grant_request(action: ContactAction, seed: int, keypair: AgentKeypair) -> GrantRequest:
    unsigned = GrantRequest(
        request_id=request_id_for(seed, action.agent_id, action.risk_id),
        agent_id=action.agent_id,
        customer_id=action.customer_id,
        risk_id=action.risk_id,
        intent=action.intent,
        requested_channel=action.channel,
        requested_max_incentive_bps=action.incentive_bps,
        issued_at=action.scheduled_at,
        signature="placeholder",
    )
    signature = keypair.sign(unsigned.canonical_bytes())
    return unsigned.model_copy(update={"signature": signature})
