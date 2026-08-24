"""Phase 2 simulation-harness types — Arm A.

Deliberately NOT sampark.contracts: those are human-owned Pydantic
domain/API contracts (CONTRACTS.md, CLAUDE.md §3) with their own approval
history. These are plain, frozen dataclasses scoped to the Arm A baseline
simulation only.

This module has no dependency on sim/ — LedgerView is built by the
caller (sim/arm_a.py) from a sim.ledger.Ledger; agents/ itself never
imports anything from sim/.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from sampark.contracts import Customer, RiskItem


@dataclass(frozen=True)
class ContactAction:
    agent_id: str
    risk_id: str
    customer_id: str
    channel: str
    intent: str
    incentive_bps: int
    scheduled_at: datetime


@dataclass(frozen=True)
class ContactOutcome:
    """Arm A's outcome shape. Deliberately not an AuditEvent and not
    persisted to audit_events — that is Phase 5's responsibility.

    Recovery-unit semantics (Phase 2 accounting clarification):
    `amount_recovered_paise` is `RiskItem.amount_paise` for the one
    RiskItem this outcome is about — a synthetic, risk-item-level
    recovered value. It is NOT deduplicated per customer (one customer
    can own several risk items, each counted independently) and it is
    NOT credited/attributed recovery — there is no holdout-baseline
    subtraction here. sim/metrics.py's `recovery_unit: "risk_item"`
    output field states this explicitly. That correction belongs to
    Phase 7 attribution (spec §8.9) and is deliberately not implemented
    yet.
    """

    outcome_id: str
    agent_id: str
    customer_id: str
    risk_id: str
    channel: str
    incentive_bps: int
    contacted_at: datetime
    recovered: bool
    amount_recovered_paise: int
    incentive_paise: int


@dataclass(frozen=True)
class LedgerView:
    """Read-only, source-partitioned view of Phase 1 ledger data — the
    only thing any RecoveryAgent receives.

    Deliberately excludes contact_states (no shared cross-agent contact
    history) and carries nothing from Population.hidden_response — see
    sim/environment.py's module docstring for what agents must never see.
    """

    customers_by_id: Mapping[str, Customer]
    risk_items_by_source: Mapping[str, tuple[RiskItem, ...]]
    customer_id_by_risk_id: Mapping[str, str]
