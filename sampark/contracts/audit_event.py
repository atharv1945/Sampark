"""AuditEvent — CONTRACTS.md Part 1, extended.

CONTRACTS.md's Part 1 table for AuditEvent lists only the five fields
carried over from the spec's bare ER diagram (event_id, prev_hash,
agent_signature, reason_code, payload). event_type and occurred_at are NOT
in that table, but they ARE required (NOT NULL) columns in
sampark/schema.sql, and they were explicitly dictated as "the approved
application-level additions" during this repository's schema-authoring
session. This is treated as a documentation gap in CONTRACTS.md's Part 1
table, not a deliberate exclusion, and both fields are included here —
flagged in the implementation report rather than resolved silently.

The hash chain (prev_hash) is application logic, not a database
constraint — it is not, and must not be, verified here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    event_type: str
    occurred_at: datetime
    prev_hash: str
    agent_signature: str | None = None
    reason_code: str | None = None
    payload: dict[str, Any]
