"""Phase-3-local scope-denial reason codes.

Plain namespaced strings, not a closed enum — CONTRACTS.md's
GrantDecision.reason_code is typed `str | None` precisely because no
ReasonCode vocabulary has been approved yet ("do not invent or
enumerate reason codes now" applies to a *global* vocabulary). These
nine are a Phase-3-local proposal, scoped to sampark/registry/scope.py's
own denials only, approved for this phase's implementation.
"""

from __future__ import annotations

UNKNOWN_AGENT = "scope.unknown_agent"
INVALID_SIGNATURE = "scope.invalid_signature"
AGENT_REVOKED = "scope.agent_revoked"
UNKNOWN_RISK_ITEM = "scope.unknown_risk_item"
CUSTOMER_RISK_ITEM_MISMATCH = "scope.customer_risk_item_mismatch"
CHANNEL_NOT_ALLOWED = "scope.channel_not_allowed"
INTENT_NOT_ALLOWED = "scope.intent_not_allowed"
RISK_SOURCE_NOT_ALLOWED = "scope.risk_source_not_allowed"
INCENTIVE_CEILING_EXCEEDED = "scope.incentive_ceiling_exceeded"
