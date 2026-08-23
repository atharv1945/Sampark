"""Grant — CONTRACTS.md Part 1.

Deliberately excludes request_id, the relational foreign key
sampark/schema.sql adds to support GRANT_REQUEST ||--o| GRANT. See
agent.py's module docstring for the same pattern.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sampark.contracts.enums import GrantState


class Grant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grant_id: UUID
    channel: str
    incentive_ceiling_paise: int = Field(ge=0)
    send_after: datetime
    expires_at: datetime
    state: GrantState

    @model_validator(mode="after")
    def _check_send_after_before_expires(self) -> "Grant":
        if not self.send_after < self.expires_at:
            raise ValueError("send_after must be strictly before expires_at")
        return self
