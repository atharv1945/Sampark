"""Arm A-H — unmediated baseline runner minus a randomized customer-level
holdout, Phase 7 (spec §8.9, §11).

A NEW file, deliberately not a modification of the FROZEN `sim/arm_a.py`
(Phase 7 design lock, Part 10.1 — Phase 4 protection). Reuses the same four
UNCHANGED Phase 2 agent classes, `MockChannelAdapter`, and the same sort
key `sim/arm_a.py` already uses; the only differences are (1) actions
targeting a held-out customer are filtered out before replay, and (2) the
Environment is built for world="v2" so the risk items belonging to those
held-out customers — and ONLY those, since every eligible risk item
otherwise receives exactly one action from its matching agent (Phase 6's
own finding: max uncontacted fraction observed 0.0000) — receive a natural-
recovery draw instead of a contact.

Filtering happens HERE, never inside `sampark/` or inside the frozen agent
classes (Phase 7 design lock, Part 3.4, layer 2): the holdout is an
evaluation-harness concept, not a hard-policy rule, and injecting it into
`sampark/policy/hard/` would let a reader mistake an experiment for a
regulation.

`_AGENTS` / `_build_ledger_view` / `_sort_key` are duplicated below rather
than imported from `sim/arm_a.py`: that file is frozen (Phase 7 design
lock, Part 10.1) and, per `sim/arm_b.py`'s own established precedent,
"does not export this as a public name" — an underscore-prefixed name in a
frozen module is not a contract this file may depend on.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents import (
    CartRecoveryAgent,
    LedgerView,
    MandateRecoveryAgent,
    PaymentRetryAgent,
    ReceivablesAgent,
    RecoveryAgent,
)
from agents.channel import MockChannelAdapter
from agents.types import ContactAction, ContactOutcome
from sampark.contracts import RiskItem
from sim.cli import build_dataset
from sim.environment import Environment
from sim.holdout import assign, customer_amounts_from_risk_items, membership_digest
from sim.ledger import Ledger
from sim.natural import NaturalOutcome, observation_window_end

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
    """Mirrors sim/arm_a.py::_build_ledger_view exactly (and sim/arm_b.py's
    own duplicate of the same function) — see this module's docstring for
    why it is duplicated rather than imported."""
    risk_items_by_source: dict[str, list[RiskItem]] = {}
    for item in ledger.risk_items:
        risk_items_by_source.setdefault(item.source, []).append(item)
    return LedgerView(
        customers_by_id={c.customer_id: c for c in ledger.customers},
        risk_items_by_source={source: tuple(items) for source, items in risk_items_by_source.items()},
        customer_id_by_risk_id=dict(ledger.risk_customer_map),
    )


def _sort_key(action: ContactAction) -> tuple:
    return (action.scheduled_at, action.agent_id, action.risk_id)


@dataclass(frozen=True)
class ArmAHoldoutResult:
    contact_outcomes: tuple[ContactOutcome, ...]
    natural_outcomes: tuple[NaturalOutcome, ...]
    holdout_customer_ids: frozenset[str]
    holdout_customer_set_sha256: str
    seed: int
    fraction: float


def run_arm_a_holdout(seed: int, fraction: float) -> ArmAHoldoutResult:
    """`fraction=0.0` degenerates to an empty holdout set — every action is
    replayed exactly as `sim.arm_a.run_arm_a` would, and every risk item
    goes through `observe()` (world v1 semantics for recovery, since world
    is still "v2" here for the opt-out label, but the holdout carve-out is
    empty so nothing is ever routed to `observe_natural`). This is the
    world-v2-with-empty-holdout diagnostic path (Phase 7 design lock,
    Holdout Design table) — NOT the world="v1" placebo, which is a
    separate, stronger guarantee tested directly against `sim.arm_a`."""
    population, signals, ledger = build_dataset(seed)
    view = _build_ledger_view(ledger)
    environment = Environment.build(population, signals, ledger, seed, world="v2")

    customer_amounts = customer_amounts_from_risk_items(ledger.risk_items, ledger.risk_customer_map)
    held_out = assign(seed, fraction, customer_amounts)

    actions: list[ContactAction] = []
    for agent in _AGENTS:
        actions.extend(agent.select_actions(view))
    actions = [a for a in actions if a.customer_id not in held_out]
    actions.sort(key=_sort_key)

    risk_items_by_id = {item.risk_id: item for item in ledger.risk_items}

    contact_outcomes: list[ContactOutcome] = []
    for action in actions:
        _ADAPTERS[action.channel].send(action)
        contact_outcomes.append(environment.observe(action, risk_items_by_id[action.risk_id]))

    horizon = observation_window_end()
    natural_outcomes: list[NaturalOutcome] = []
    for risk_id in sorted(risk_items_by_id):
        customer_id = ledger.risk_customer_map[risk_id]
        if customer_id in held_out:
            natural_outcomes.append(
                environment.observe_natural(risk_items_by_id[risk_id], customer_id, observed_at=horizon)
            )

    return ArmAHoldoutResult(
        contact_outcomes=tuple(contact_outcomes),
        natural_outcomes=tuple(natural_outcomes),
        holdout_customer_ids=held_out,
        holdout_customer_set_sha256=membership_digest(held_out),
        seed=seed,
        fraction=fraction,
    )
