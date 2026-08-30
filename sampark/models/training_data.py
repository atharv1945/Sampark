"""Arm A training data — Phase 6, extended for Phase 7 (spec §8.9).

Every Phase 6/7 training entry point reads Arm A's / Arm A-H's PUBLIC
outcome log via this module and nothing else: never `sim.environment`
internals, never `Population.hidden_response`, never any seed other than
the one asked for. This mirrors the boundary `sim/calibration.py` already
observes for Phase 4's calibrated constants (`sim/calibration.py`'s own
docstring: "This module does not read Environment internals or
HiddenResponseProfile; it only re-derives the index from the public
outcome sequence").

`tests/models/test_no_leakage.py` enforces this at the AST level, the
same technique `tests/allocator/test_structural_boundaries.py` already
uses for the allocator/hard-policy boundary.

Phase 7 adds `TreatmentArm.HOLDOUT` rows, built from
`sim.arm_a_holdout.run_arm_a_holdout`'s `natural_outcomes` — real,
randomized-control observations, never a proxy for "was not contacted."
`load_training_rows(seed)` (no `fraction` argument, Phase 6's original
entry point) is UNCHANGED: it still calls `sim.arm_a.run_arm_a` directly
and returns only `TreatmentArm.TREATED` rows, preserving the committed
`sampark/models/artifact_data.py` byte-for-byte. `load_training_rows_with_holdout`
is the new, additive Phase 7 entry point.

THE anti-inflation rule (Phase 7 design lock, Decision 15's hard
restriction): a `HOLDOUT` row may ONLY come from a risk item belonging to
a customer `sim.holdout.assign()` actually selected. An
allocator-declined-but-not-held-out item is `TreatmentArm.TREATED`-eligible
data that never got a contact — it is NEVER a control, because it was
excluded on the STRENGTH of the allocator's own judgement (endogenous
selection), which would make any uplift/rate estimated from it exactly as
optimistic as the allocator's own skill. This module has no code path that
can construct a `HOLDOUT` row from anything other than
`ArmAHoldoutResult.natural_outcomes` — enforced additionally by
`tests/models/test_no_endogenous_controls.py` (AST + a direct injection
test).
"""

from __future__ import annotations

import collections
from dataclasses import dataclass
from enum import Enum

from agents.types import ContactOutcome
from sampark.contracts import RiskItem
from sim.arm_a import run_arm_a
from sim.arm_a_holdout import run_arm_a_holdout
from sim.cli import build_dataset


class TreatmentArm(Enum):
    """Closed by design — no third member. A row is either a real
    contact (TREATED) or a real randomized-holdout observation (HOLDOUT).
    There is deliberately no "UNCONTACTED" member: an endogenously
    uncontacted item (allocator-declined, deferred to terminal deny) is
    neither — it must never enter training data as a control (Phase 7
    design lock, Decision 15)."""

    TREATED = "TREATED"
    HOLDOUT = "HOLDOUT"


@dataclass(frozen=True)
class TrainingRow:
    """One Arm A / Arm A-H outcome, joined with its RiskItem and its
    cross-agent contact index `n` (SAMPARK's own count of contacts to
    this customer strictly before this one, in `run_arm_a`'s fixed
    chronological replay order — never a value Environment computed for
    its own purposes).

    `treatment_arm` defaults to `TREATED` so every pre-Phase-7 caller
    (including `tests/models/test_uplift.py` / `test_fatigue_hazard.py`'s
    own synthetic row constructions) is unaffected. A `HOLDOUT` row must
    have `agent_id=None`, `channel=None`, `incentive_bps=0`,
    `contact_index=0`, `incentive_paise=0` — enforced in `__post_init__`
    rather than merely documented, since nothing about a never-contacted
    item can honestly carry a channel or an incentive."""

    agent_id: str | None
    customer_id: str
    risk_id: str
    source: str
    root_cause: str
    channel: str | None
    incentive_bps: int
    amount_paise: int
    contact_index: int
    recovered: bool
    amount_recovered_paise: int
    incentive_paise: int
    treatment_arm: TreatmentArm = TreatmentArm.TREATED
    opt_out: bool = False
    """Phase 7 (world v2 only; default False preserves every pre-Phase-7
    caller's behavior). Sourced from `ContactOutcome.opt_out` for TREATED
    rows; always False for HOLDOUT rows (a never-contacted item cannot
    opt out — `sim/environment.py::p_optout`'s own locked structural
    property)."""

    def __post_init__(self) -> None:
        if self.treatment_arm is TreatmentArm.HOLDOUT:
            if (
                self.agent_id is not None
                or self.channel is not None
                or self.incentive_bps != 0
                or self.contact_index != 0
                or self.incentive_paise != 0
                or self.opt_out is not False
            ):
                raise ValueError(
                    "a HOLDOUT TrainingRow must have agent_id=None, channel=None, "
                    "incentive_bps=0, contact_index=0, incentive_paise=0, opt_out=False — got "
                    f"agent_id={self.agent_id!r}, channel={self.channel!r}, "
                    f"incentive_bps={self.incentive_bps!r}, contact_index={self.contact_index!r}, "
                    f"incentive_paise={self.incentive_paise!r}, opt_out={self.opt_out!r}"
                )


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


def _treated_row(outcome: ContactOutcome, n: int, risk_item: RiskItem) -> TrainingRow:
    return TrainingRow(
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
        treatment_arm=TreatmentArm.TREATED,
        opt_out=outcome.opt_out,
    )


def load_training_rows(seed: int) -> tuple[TrainingRow, ...]:
    """Phase 6's original entry point — UNCHANGED behavior (still calls
    `sim.arm_a.run_arm_a` directly, no holdout, every row `TREATED`),
    preserving `sampark/models/artifact_data.py` (the committed Phase 6
    artifact) byte-for-byte. Deterministic: calling this twice for the
    same seed returns byte-identical rows."""
    _population, _signals, ledger = build_dataset(seed)
    risk_items_by_id: dict[str, RiskItem] = {r.risk_id: r for r in ledger.risk_items}

    outcomes = run_arm_a(seed)
    indexed = _contact_indexed_outcomes(outcomes)

    return tuple(_treated_row(outcome, n, risk_items_by_id[outcome.risk_id]) for n, outcome in indexed)


def load_training_rows_with_holdout(seed: int, fraction: float) -> tuple[TrainingRow, ...]:
    """Phase 7's entry point. `TreatmentArm.TREATED` rows come from
    `ArmAHoldoutResult.contact_outcomes` (real contacts, minus the
    held-out customers); `TreatmentArm.HOLDOUT` rows come EXCLUSIVELY
    from `ArmAHoldoutResult.natural_outcomes` — which, for Arm A-H, are
    exactly and only the held-out customers' risk items (every other
    item receives a contact from its matching agent — Phase 6's own
    finding: max uncontacted fraction 0.0000 absent a holdout). `fraction=0.0`
    degenerates to zero HOLDOUT rows and a TREATED set identical in
    content to `load_training_rows(seed)` (proven at real scale by
    `tests/sim_arm_a_holdout/test_arm_a_holdout.py`)."""
    _population, _signals, ledger = build_dataset(seed)
    risk_items_by_id: dict[str, RiskItem] = {r.risk_id: r for r in ledger.risk_items}

    result = run_arm_a_holdout(seed, fraction)
    indexed = _contact_indexed_outcomes(result.contact_outcomes)

    rows: list[TrainingRow] = [_treated_row(outcome, n, risk_items_by_id[outcome.risk_id]) for n, outcome in indexed]

    for natural_outcome in result.natural_outcomes:
        risk_item = risk_items_by_id[natural_outcome.risk_id]
        rows.append(
            TrainingRow(
                agent_id=None,
                customer_id=natural_outcome.customer_id,
                risk_id=natural_outcome.risk_id,
                source=risk_item.source,
                root_cause=risk_item.root_cause,
                channel=None,
                incentive_bps=0,
                amount_paise=risk_item.amount_paise,
                contact_index=0,
                recovered=natural_outcome.recovered,
                amount_recovered_paise=natural_outcome.amount_recovered_paise,
                incentive_paise=0,
                treatment_arm=TreatmentArm.HOLDOUT,
            )
        )

    return tuple(rows)
