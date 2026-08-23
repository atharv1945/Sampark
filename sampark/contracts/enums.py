"""Closed enums for vocabularies with an explicit closed list in one of the
two Phase 0 authoritative artifacts (CONTRACTS.md, sampark/schema.sql).

Do not add members without approval.
"""

from __future__ import annotations

from enum import Enum


class AgentState(str, Enum):
    """sampark/schema.sql: agents_state_valid CHECK (state IN (...))."""

    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class GrantState(str, Enum):
    """sampark/schema.sql: grants_state_valid CHECK (state IN (...))."""

    RESERVED = "RESERVED"
    EXECUTING = "EXECUTING"
    CONFIRMED = "CONFIRMED"
    ROLLED_BACK = "ROLLED_BACK"
    EXPIRED = "EXPIRED"


class DecisionOutcome(str, Enum):
    """CONTRACTS.md: GrantDecision.outcome / DecisionOutcome."""

    GRANTED = "GRANTED"
    DENIED = "DENIED"
    DEFERRED = "DEFERRED"
