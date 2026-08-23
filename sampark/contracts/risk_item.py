"""RiskItem — CONTRACTS.md Part 1.

Deliberately excludes customer_id, the relational foreign key
sampark/schema.sql adds to support CUSTOMER ||--o{ RISK_ITEM. See
agent.py's module docstring for the same pattern.

amount_paise is authoritative ledger data (CONTRACTS.md invariant): no
other contract in this package may redefine it.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RiskItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_id: str
    source: str
    amount_paise: int = Field(gt=0)
    root_cause: str
    detected_at: datetime
