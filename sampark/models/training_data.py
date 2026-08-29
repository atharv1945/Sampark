"""Arm A training data — Phase 6.

Every Phase 6 training entry point reads Arm A's PUBLIC outcome log via
this module and nothing else: never `sim.environment` internals, never
`Population.hidden_response`, never any seed other than the one asked
for. This mirrors the boundary `sim/calibration.py` already observes
for Phase 4's calibrated constants (`sim/calibration.py`'s own
docstring: "This module does not read Environment internals or
HiddenResponseProfile; it only re-derives the index from the public
outcome sequence").

`tests/models/test_no_leakage.py` enforces this at the AST level, the
same technique `tests/allocator/test_structural_boundaries.py` already
uses for the allocator/hard-policy boundary.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass

from agents.types import ContactOutcome
from sampark.contracts import RiskItem
from sim.arm_a import run_arm_a
from sim.cli import build_dataset


@dataclass(frozen=True)
class TrainingRow:
    """One Arm A contact outcome, joined with its RiskItem and its
    cross-agent contact index `n` (SAMPARK's own count of contacts to
    this customer strictly before this one, in `run_arm_a`'s fixed
    chronological replay order — never a value Environment computed for
    its own purposes)."""

    agent_id: str
    customer_id: str
    risk_id: str
    source: str
    root_cause: str
    channel: str
    incentive_bps: int
    amount_paise: int
    contact_index: int
    recovered: bool
    amount_recovered_paise: int
    incentive_paise: int


def _contact_indexed_outcomes(
    outcomes: tuple[ContactOutcome, ...],
) -> list[tuple[int, ContactOutcome]]:
    """(contact_index, outcome) pairs — index = the count of outcomes
    already seen for this customer_id, in `outcomes`' own order (Arm A's
    fixed (scheduled_at, agent_id, risk_id) replay order). Independent
    re-implementation of the same technique `sim/calibration.py` uses
    (that module's `_contact_indexed_outcomes` is private to it) so this
    package has no import dependency on `sim/calibration.py`."""
    counters: dict[str, int] = collections.Counter()
    indexed: list[tuple[int, ContactOutcome]] = []
    for outcome in outcomes:
        n = counters[outcome.customer_id]
        indexed.append((n, outcome))
        counters[outcome.customer_id] = n + 1
    return indexed


def load_training_rows(seed: int) -> tuple[TrainingRow, ...]:
    """Arm A's complete outcome log for `seed`, joined to RiskItem and
    indexed by cross-agent contact order. Deterministic: calling this
    twice for the same seed returns byte-identical rows (`run_arm_a` and
    `build_dataset` are both pure functions of `seed`)."""
    _population, _signals, ledger = build_dataset(seed)
    risk_items_by_id: dict[str, RiskItem] = {r.risk_id: r for r in ledger.risk_items}

    outcomes = run_arm_a(seed)
    indexed = _contact_indexed_outcomes(outcomes)

    rows: list[TrainingRow] = []
    for n, outcome in indexed:
        risk_item = risk_items_by_id[outcome.risk_id]
        rows.append(
            TrainingRow(
                agent_id=outcome.agent_id,
                customer_id=outcome.customer_id,
                risk_id=outcome.risk_id,
                source=risk_item.source,
                root_cause=risk_item.root_cause,
                channel=outcome.channel,
                incentive_bps=outcome.incentive_bps,
                amount_paise=risk_item.amount_paise,
                contact_index=n,
                recovered=outcome.recovered,
                amount_recovered_paise=outcome.amount_recovered_paise,
                incentive_paise=outcome.incentive_paise,
            )
        )
    return tuple(rows)
