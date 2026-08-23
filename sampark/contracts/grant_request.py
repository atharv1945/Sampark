"""GrantRequest — CONTRACTS.md Part 2.

Implements the approved signed-payload boundary: canonical_payload() and
canonical_bytes() expose a deterministic representation of exactly the
fields covered by the agent's signature — request_id, agent_id,
customer_id, risk_id, intent, requested_channel,
requested_max_incentive_bps, issued_at. `signature` itself is excluded
from that representation, since the signed bytes cannot include the
signature over themselves.

No cryptography library is introduced here. A later security module signs
or verifies canonical_bytes() directly, without duplicating this
serialization.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

_SIGNED_FIELDS = (
    "request_id",
    "agent_id",
    "customer_id",
    "risk_id",
    "intent",
    "requested_channel",
    "requested_max_incentive_bps",
    "issued_at",
)


class GrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    agent_id: str
    customer_id: str
    risk_id: str
    intent: str
    requested_channel: str
    requested_max_incentive_bps: int = Field(ge=0)
    issued_at: datetime
    signature: str

    def canonical_payload(self) -> dict[str, Any]:
        """The fields covered by `signature`, in a stable field order."""
        payload: dict[str, Any] = {}
        for field_name in _SIGNED_FIELDS:
            value = getattr(self, field_name)
            if isinstance(value, UUID):
                value = str(value)
            elif isinstance(value, datetime):
                value = value.isoformat()
            payload[field_name] = value
        return payload

    def canonical_bytes(self) -> bytes:
        """Deterministic UTF-8 JSON encoding of canonical_payload().

        Sorted keys and compact separators make this byte-identical for
        byte-identical field values regardless of construction order — the
        property a later Ed25519 sign/verify step depends on.
        """
        return json.dumps(
            self.canonical_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
