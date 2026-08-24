"""Agent Registry — spec §8.1.

Ed25519 identity, declared capability scopes, signature verification,
strikes and revocation. This is the authorization floor: it answers "is
this request genuinely from this registered agent, and is it within
that agent's declared capability" — never "which agent should win",
which is the Phase 4 allocator's question, not this package's.

evaluate_scope() never constructs a GRANTED or DEFERRED GrantDecision:
both require a Grant, and only Phase 4's allocator may create one.
"""

from __future__ import annotations

from sampark.registry.keys import AgentKeypair, generate_keypair
from sampark.registry.scope import evaluate_scope
from sampark.registry.signing import verify_signature
from sampark.registry.store import (
    AgentRegistrationConflictError,
    AgentRepository,
    InMemoryAgentRepository,
    InMemoryRiskItemRepository,
    PostgresAgentRepository,
    PostgresRiskItemRepository,
    RiskItemRecord,
    RiskItemRepository,
)
from sampark.registry.strikes import (
    STRIKE_THRESHOLD,
    apply_strike,
    record_scope_denial,
    revoke,
)

__all__ = [
    "AgentKeypair",
    "AgentRegistrationConflictError",
    "AgentRepository",
    "InMemoryAgentRepository",
    "InMemoryRiskItemRepository",
    "PostgresAgentRepository",
    "PostgresRiskItemRepository",
    "RiskItemRecord",
    "RiskItemRepository",
    "STRIKE_THRESHOLD",
    "apply_strike",
    "evaluate_scope",
    "generate_keypair",
    "record_scope_denial",
    "revoke",
    "verify_signature",
]
