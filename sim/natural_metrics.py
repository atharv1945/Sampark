"""Natural-recovery metrics — Phase 7 (spec §8.9), a NEW file, never
modifying the frozen `sim/metrics.py` (Design Lock — Phase 4 protection).

`compute_metrics` (sim/metrics.py) is computed only over CONTACTED
outcomes and stays exactly as it is — its numerator is contact-attributable
recovery, which is what the Phase 4 gate (`sim.gate`) reads. This module
adds a SEPARATE set of keys for the natural-recovery component so the two
are never confused: a caller combining both must explicitly add
`recovered_amount_paise + natural_recovered_amount_paise` to get
`total_recovered_amount_paise` — never done implicitly by this function.
"""

from __future__ import annotations

from typing import Sequence

from sim.natural import NaturalOutcome


def compute_natural_metrics(outcomes: Sequence[NaturalOutcome]) -> dict:
    total_items = len(outcomes)
    total_recoveries = sum(1 for o in outcomes if o.recovered)
    recovered_amount_paise = sum(o.amount_recovered_paise for o in outcomes)

    recovered_amount_per_item_paise = (
        recovered_amount_paise / total_items if total_items else 0.0
    )

    return {
        "natural_recovery_unit": "risk_item",
        "natural_total_items": total_items,
        "natural_total_recoveries": total_recoveries,
        "natural_recovered_amount_paise": recovered_amount_paise,
        "natural_recovered_amount_per_item_paise": recovered_amount_per_item_paise,
    }
