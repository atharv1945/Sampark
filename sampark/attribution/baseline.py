"""Natural-recovery baseline estimation — Phase 7, spec §8.9.

THE hard restriction (Phase 7 design lock, Decision 15): the natural rate
used to compute credited recovery is estimated ONLY from randomized-holdout
observations — never from Arm H (a real merchant cannot run that
counterfactual; using it would make the ledger depend on information no
production system could obtain), and never from an allocator-declined
item's natural outcome (that population is selected ON LOW EXPECTED VALUE
by the allocator itself, so estimating a rate from it would be biased low
by exactly the allocator's own selection skill — inflating every credit by
that same amount).

`build_baseline_estimator` enforces this structurally: it takes the
ACTUAL held-out customer_id set (as produced by `sim.holdout.assign`, via
`ArmAHoldoutResult.holdout_customer_ids` / `ArmBHoldoutResult.holdout_customer_ids`
— never hand-constructed) and filters every `NaturalOutcome` to that set
BEFORE computing anything. An allocator-declined item's `NaturalOutcome`
(present in `ArmBHoldoutResult.natural_outcomes` per Decision 1's Option 2
— every uncontacted item gets a draw) is silently excluded here, by
construction, not by a runtime provenance check this module cannot
actually perform (it has no way to know WHERE a caller's frozenset came
from — the guarantee is that every production caller passes
`ArmB(A)HoldoutResult.holdout_customer_ids` verbatim, never a
reconstructed set — enforced by `tests/sampark_attribution/test_baseline.py`'s
integration test against the real pipeline, not by this function alone).

Stratum hierarchy mirrors `sim/calibration.py`'s own committed thresholds
(`_P_BASE_SOURCE_FALLBACK_MIN_OBS = 100`, `_P_BASE_MEAN_FALLBACK_MIN_OBS = 30`)
rather than inventing new ones: `(source, root_cause)` if n>=100,
`(source)` if n>=30, else `global`.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass
from typing import Mapping, Sequence

from sim.natural import NaturalOutcome

LEVEL_SOURCE_ROOT_CAUSE = "source_root_cause"
LEVEL_SOURCE = "source"
LEVEL_GLOBAL = "global"

_SOURCE_ROOT_CAUSE_MIN_OBS = 100
_SOURCE_MIN_OBS = 30


@dataclass(frozen=True)
class BaselineRate:
    stratum: str  # e.g. "mandate_failure.insufficient_funds", "mandate_failure", "global"
    level: str  # LEVEL_SOURCE_ROOT_CAUSE | LEVEL_SOURCE | LEVEL_GLOBAL
    rate: float
    n: int


@dataclass(frozen=True)
class NaturalBaselineEstimator:
    """Fitted from ONLY randomized-holdout `NaturalOutcome`s. `rate_for`
    resolves the fallback chain and always returns a `BaselineRate` — the
    global level is the guaranteed terminal fallback (n > 0 required at
    construction, see `build_baseline_estimator`)."""

    rate_by_source_root_cause: Mapping[tuple[str, str], BaselineRate]
    rate_by_source: Mapping[str, BaselineRate]
    global_rate: BaselineRate

    def rate_for(self, source: str, root_cause: str) -> BaselineRate:
        if (source, root_cause) in self.rate_by_source_root_cause:
            return self.rate_by_source_root_cause[(source, root_cause)]
        if source in self.rate_by_source:
            return self.rate_by_source[source]
        return self.global_rate


class InsufficientHoldoutDataError(RuntimeError):
    """The holdout is empty, or produced zero natural outcomes — no
    baseline can be estimated at all. Raised rather than returning a
    degenerate estimator with a fabricated global rate."""


def build_baseline_estimator(
    held_out_customer_ids: frozenset[str], natural_outcomes: Sequence[NaturalOutcome]
) -> NaturalBaselineEstimator:
    control_outcomes = [o for o in natural_outcomes if o.customer_id in held_out_customer_ids]
    if not control_outcomes:
        raise InsufficientHoldoutDataError(
            f"zero randomized-holdout natural outcomes available "
            f"(held_out_customer_ids has {len(held_out_customer_ids)} members) — "
            "cannot estimate a baseline rate"
        )

    def _rate(subset: Sequence[NaturalOutcome]) -> tuple[float, int]:
        n = len(subset)
        recovered = sum(1 for o in subset if o.recovered)
        return (recovered / n if n else 0.0), n

    by_source_root_cause: dict[tuple[str, str], list[NaturalOutcome]] = collections.defaultdict(list)
    by_source: dict[str, list[NaturalOutcome]] = collections.defaultdict(list)
    for o in control_outcomes:
        by_source_root_cause[(o.source, o.root_cause)].append(o)
        by_source[o.source].append(o)

    global_rate_value, global_n = _rate(control_outcomes)
    global_rate = BaselineRate(stratum="global", level=LEVEL_GLOBAL, rate=global_rate_value, n=global_n)

    rate_by_source: dict[str, BaselineRate] = {}
    for source, subset in by_source.items():
        if len(subset) >= _SOURCE_MIN_OBS:
            rate, n = _rate(subset)
            rate_by_source[source] = BaselineRate(stratum=source, level=LEVEL_SOURCE, rate=rate, n=n)

    rate_by_source_root_cause: dict[tuple[str, str], BaselineRate] = {}
    for (source, root_cause), subset in by_source_root_cause.items():
        if len(subset) >= _SOURCE_ROOT_CAUSE_MIN_OBS:
            rate, n = _rate(subset)
            rate_by_source_root_cause[(source, root_cause)] = BaselineRate(
                stratum=f"{source}.{root_cause}", level=LEVEL_SOURCE_ROOT_CAUSE, rate=rate, n=n
            )

    return NaturalBaselineEstimator(
        rate_by_source_root_cause=rate_by_source_root_cause,
        rate_by_source=rate_by_source,
        global_rate=global_rate,
    )
