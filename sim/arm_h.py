"""Arm H — full-population, zero-contact counterfactual, Phase 7 (spec §11:
"H — Holdout. No contact. Natural recovery baseline.").

Phase 7 design lock, Decision 16: H is a separate ARM, not the held-out
fraction inside Arm A-H/B-H. Every one of the 20,000 risk items receives a
natural-recovery draw and NONE receives a contact — no agents run, no
allocator runs, no grant is ever issued.

Arm H exists to VALIDATE the randomized-holdout estimate used by the
attribution ledger (Phase 7 design lock, Decision 15), never to produce a
credit itself: a real merchant cannot run this counterfactual, so using it
for `attribution_credits` would make the ledger depend on information no
production system could obtain. `sampark/attribution/baseline.py` must
never read this module's output — enforced by an AST test
(tests/sampark_attribution/test_no_arm_h_leakage.py).

Because it issues no grants and makes no decisions, Arm H writes no audit
events (Phase 7 design lock §2.18: the audit log is SAMPARK's decision
record, and nothing here is a decision).
"""

from __future__ import annotations

from dataclasses import dataclass

from sim.cli import build_dataset
from sim.natural import NaturalOutcome, observation_window_end


@dataclass(frozen=True)
class ArmHResult:
    natural_outcomes: tuple[NaturalOutcome, ...]
    seed: int


def run_arm_h(seed: int) -> ArmHResult:
    from sim.environment import Environment

    population, signals, ledger = build_dataset(seed)
    environment = Environment.build(population, signals, ledger, seed, world="v2")

    horizon = observation_window_end()
    risk_items_by_id = {item.risk_id: item for item in ledger.risk_items}

    natural_outcomes = []
    for risk_id in sorted(risk_items_by_id):
        customer_id = ledger.risk_customer_map[risk_id]
        natural_outcomes.append(
            environment.observe_natural(risk_items_by_id[risk_id], customer_id, observed_at=horizon)
        )

    return ArmHResult(natural_outcomes=tuple(natural_outcomes), seed=seed)
