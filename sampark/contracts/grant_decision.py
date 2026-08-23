"""GrantDecision — CONTRACTS.md Part 2.

Not persisted as its own table. A GRANTED decision references a Grant
(CONTRACTS.md Part 1); a DENIED or DEFERRED decision does not.

`reason_code` is typed as a plain string, not a closed enum: CONTRACTS.md
names a `ReasonCode` type but does not enumerate its members, and neither
does sampark/schema.sql (audit_events.reason_code is unconstrained TEXT).
Enumerating values here would mean inventing a vocabulary that was never
approved — flagged in the implementation report rather than resolved
silently.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from sampark.contracts.enums import DecisionOutcome
from sampark.contracts.grant import Grant


class GrantDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: UUID
    request_id: UUID
    outcome: DecisionOutcome
    reason_code: str | None = None
    human_readable: str | None = None
    next_eligible_at: datetime | None = None
    grant: Grant | None = None

    @model_validator(mode="after")
    def _check_outcome_invariants(self) -> "GrantDecision":
        if self.outcome is DecisionOutcome.GRANTED:
            if self.grant is None:
                raise ValueError("GRANTED decision requires grant")
            if self.reason_code is not None:
                raise ValueError("GRANTED decision must not set reason_code")
            if self.next_eligible_at is not None:
                raise ValueError("GRANTED decision must not set next_eligible_at")
        elif self.outcome is DecisionOutcome.DENIED:
            if self.grant is not None:
                raise ValueError("DENIED decision must not reference a grant")
            if self.reason_code is None:
                raise ValueError("DENIED decision requires reason_code")
        elif self.outcome is DecisionOutcome.DEFERRED:
            if self.grant is not None:
                raise ValueError("DEFERRED decision must not reference a grant")
            if self.reason_code is None:
                raise ValueError("DEFERRED decision requires reason_code")
            if self.next_eligible_at is None:
                raise ValueError("DEFERRED decision requires next_eligible_at")
        return self
