"""Customer and ContactState — CONTRACTS.md Part 1.

ContactState deliberately excludes customer_id, the relational foreign key
sampark/schema.sql adds only to support the CUSTOMER ||--|| CONTACT_STATE
relationship. See agent.py's module docstring for the same pattern.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Customer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    phone_hash: str | None = None
    email_hash: str | None = None


class ContactState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contacts_24h: int = Field(default=0, ge=0)
    contacts_7d: int = Field(default=0, ge=0)
    last_contact_at: datetime | None = None
    optouts_by_channel: dict[str, Any]
    consent_scopes: dict[str, Any]
    fatigue_score: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _check_contacts_7d_at_least_24h(self) -> "ContactState":
        if self.contacts_7d < self.contacts_24h:
            raise ValueError("contacts_7d must be >= contacts_24h")
        return self
