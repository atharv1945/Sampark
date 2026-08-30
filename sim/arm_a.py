"""Arm A — unmediated baseline runner (Phase 2, spec §18.1 exit criterion:
"Arm A runs end to end and emits a metrics file").

Every agent generates its COMPLETE action list from a read-only
LedgerView before any action is executed (locked decision 6) — no agent
reacts to an earlier execution's outcome. Actions are then merged across
all four agents, sorted deterministically, and replayed through
Environment.observe once each, in that fixed order.
"""

from __future__ import annotations

from agents import (
    CartRecoveryAgent,
    ContactOutcome,
    LedgerView,
    MandateRecoveryAgent,
    PaymentRetryAgent,
    ReceivablesAgent,
    RecoveryAgent,
)
from agents.channel import MockChannelAdapter
from agents.types import ContactAction
from sampark.contracts import RiskItem
from sim.cli import build_dataset
from sim.environment import BETA_FATIGUE, BETA_INCENTIVE, Environment
from sim.ledger import Ledger

_AGENTS: tuple[RecoveryAgent, ...] = (
    PaymentRetryAgent(),
    CartRecoveryAgent(),
    MandateRecoveryAgent(),
    ReceivablesAgent(),
)

_ADAPTERS: dict[str, MockChannelAdapter] = {
    "sms": MockChannelAdapter("sms"),
    "whatsapp": MockChannelAdapter("whatsapp"),
    "voice": MockChannelAdapter("voice"),
}


def _build_ledger_view(ledger: Ledger) -> LedgerView:
    risk_items_by_source: dict[str, list[RiskItem]] = {}
    for item in ledger.risk_items:
        risk_items_by_source.setdefault(item.source, []).append(item)
    return LedgerView(
        customers_by_id={c.customer_id: c for c in ledger.customers},
        risk_items_by_source={
            source: tuple(items) for source, items in risk_items_by_source.items()
        },
        customer_id_by_risk_id=dict(ledger.risk_customer_map),
    )


def _sort_key(action: ContactAction) -> tuple:
    return (action.scheduled_at, action.agent_id, action.risk_id)


def run_arm_a(
    seed: int,
    *,
    beta_fatigue: float = BETA_FATIGUE,
    beta_incentive: float = BETA_INCENTIVE,
) -> tuple[ContactOutcome, ...]:
    """`beta_fatigue` / `beta_incentive` (Phase 9, spec §11) are keyword-only
    and default to the frozen `sim.environment` constants, so `run_arm_a(seed)`
    — every pre-Phase-9 call site — is byte-identical. They reach nothing but
    `Environment`'s ground-truth response model: agent action selection below
    happens before any observation and cannot see them."""
    population, signals, ledger = build_dataset(seed)
    view = _build_ledger_view(ledger)
    environment = Environment.build(
        population, signals, ledger, seed,
        beta_fatigue=beta_fatigue, beta_incentive=beta_incentive,
    )

    actions: list[ContactAction] = []
    for agent in _AGENTS:
        actions.extend(agent.select_actions(view))
    actions.sort(key=_sort_key)

    risk_items_by_id = {item.risk_id: item for item in ledger.risk_items}

    outcomes: list[ContactOutcome] = []
    for action in actions:
        _ADAPTERS[action.channel].send(action)
        outcomes.append(environment.observe(action, risk_items_by_id[action.risk_id]))

    return tuple(outcomes)
