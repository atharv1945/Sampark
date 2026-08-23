"""Agent and CapabilityScope — CONTRACTS.md Part 1.

CapabilityScope deliberately excludes agent_id, the relational foreign key
sampark/schema.sql adds only to support the AGENT ||--|| CAPABILITY_SCOPE
relationship at the database level. CONTRACTS.md documents that FK as a
persistence-layer addition, not a canonical field — see its "two artifacts
are not the same thing" section.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from sampark.contracts.enums import AgentState


class Agent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    public_key: str
    publisher: str
    state: AgentState
    strike_count: int = Field(default=0, ge=0)


class CapabilityScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_channels: list[str]
    allowed_intents: list[str]
    allowed_risk_sources: list[str]
    max_incentive_bps: int = Field(ge=0)
    max_requests_per_hour: int = Field(ge=0)
