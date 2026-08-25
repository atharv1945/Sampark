"""Phase 4 scoring calibration — Design Lock §14.1.

Calibrates DECAY, P_BASE_MEAN, and P_BASE[source, root_cause] from Arm A's
outcome log at seed 42 ONLY. Per the Design Lock: "Calibrate on seed 42
only, and use that one table for all five gate seeds. Re-calibrating per
seed would let each Arm B run see its own arm's structure and is a leak."

Methodology (Design Lock §14.1, verbatim):

    DECAY: exp(slope) of an OLS of log(empirical recovery rate at
    cross-agent contact index n) on n, over buckets with >= 200
    observations.

    P_BASE_MEAN: global first-contact (n = 0) recovery rate.

    P_BASE[source, root_cause]: first-contact recovery rate for that
    bucket; falls back to the source-level rate below 100 observations,
    then to P_BASE_MEAN below 30.

The contact index `n` reproduced here is sim.arm_a.run_arm_a's own
replay order (actions sorted by (scheduled_at, agent_id, risk_id)) —
the same cross-agent, chronological count sim/environment.py's
Environment uses internally as `prior_contacts`. This module does not
read Environment internals or HiddenResponseProfile; it only re-derives
the index from the public outcome sequence, which is already in that
fixed replay order.

Output is deterministic given the calibration seed: run this twice and
get byte-identical results. `main()` writes the generated
sampark/allocator/calibrated.py.
"""

from __future__ import annotations

import collections
import math
from dataclasses import dataclass
from pathlib import Path

from agents.types import ContactOutcome
from sampark.contracts import RiskItem
from sim.arm_a import run_arm_a
from sim.cli import build_dataset

CALIBRATION_SEED = 42

_DECAY_MIN_BUCKET_OBS = 200
_P_BASE_SOURCE_FALLBACK_MIN_OBS = 100
_P_BASE_MEAN_FALLBACK_MIN_OBS = 30

_CALIBRATED_MODULE_PATH = (
    Path(__file__).resolve().parent.parent / "sampark" / "allocator" / "calibrated.py"
)


@dataclass(frozen=True)
class CalibrationResult:
    decay: float
    p_base_mean: float
    p_base_by_bucket: dict[tuple[str, str], float]
    p_base_source_fallback: dict[str, float]
    seed: int


def _contact_indexed_outcomes(
    outcomes: tuple[ContactOutcome, ...],
) -> list[tuple[int, ContactOutcome]]:
    """(contact_index, outcome) pairs, index = SAMPARK's own count of
    contacts to that customer strictly before this one, in run_arm_a's
    fixed chronological replay order (see module docstring)."""
    counters: dict[str, int] = collections.Counter()
    indexed: list[tuple[int, ContactOutcome]] = []
    for outcome in outcomes:
        n = counters[outcome.customer_id]
        indexed.append((n, outcome))
        counters[outcome.customer_id] = n + 1
    return indexed


def _calibrate_decay(indexed: list[tuple[int, ContactOutcome]]) -> float:
    by_n: dict[int, list[int]] = collections.defaultdict(list)  # n -> [recovered(0/1), ...]
    for n, outcome in indexed:
        by_n[n].append(1 if outcome.recovered else 0)

    ns: list[float] = []
    log_rates: list[float] = []
    for n in sorted(by_n):
        obs = by_n[n]
        if len(obs) < _DECAY_MIN_BUCKET_OBS:
            continue
        rate = sum(obs) / len(obs)
        if rate <= 0.0:
            continue
        ns.append(float(n))
        log_rates.append(math.log(rate))

    if len(ns) < 2:
        raise RuntimeError(
            "Not enough contact-index buckets with >= "
            f"{_DECAY_MIN_BUCKET_OBS} observations to calibrate DECAY"
        )

    slope, _intercept = _ols_fit(ns, log_rates)
    decay = math.exp(slope)
    if not (0.0 < decay < 1.0):
        raise RuntimeError(f"Calibrated DECAY out of (0, 1) range: {decay!r}")
    return decay


def _ols_fit(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Plain least-squares slope/intercept — no numpy.polyfit dependency
    beyond what's already pinned; this is a two-parameter fit over at
    most a handful of points."""
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    if var_x == 0.0:
        raise RuntimeError("Degenerate calibration input: all contact indices identical")
    slope = cov / var_x
    intercept = mean_y - slope * mean_x
    return slope, intercept


def _calibrate_p_base(
    indexed: list[tuple[int, ContactOutcome]],
    risk_items_by_id: dict[str, RiskItem],
) -> tuple[float, dict[tuple[str, str], float], dict[str, float]]:
    global_first = [0, 0]  # [obs, recovered]
    by_bucket: dict[tuple[str, str], list[int]] = collections.defaultdict(lambda: [0, 0])
    by_source: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])

    for n, outcome in indexed:
        if n != 0:
            continue
        risk_item = risk_items_by_id[outcome.risk_id]
        key = (risk_item.source, risk_item.root_cause)
        by_bucket[key][0] += 1
        by_bucket[key][0 + 1] += 1 if outcome.recovered else 0
        by_source[risk_item.source][0] += 1
        by_source[risk_item.source][1] += 1 if outcome.recovered else 0
        global_first[0] += 1
        global_first[1] += 1 if outcome.recovered else 0

    if global_first[0] == 0:
        raise RuntimeError("No first-contact (n=0) observations to calibrate P_BASE_MEAN")
    p_base_mean = global_first[1] / global_first[0]

    p_base_source_fallback: dict[str, float] = {}
    for source, (obs, recovered) in by_source.items():
        p_base_source_fallback[source] = (
            recovered / obs if obs >= _P_BASE_MEAN_FALLBACK_MIN_OBS else p_base_mean
        )

    p_base_by_bucket: dict[tuple[str, str], float] = {}
    for key, (obs, recovered) in by_bucket.items():
        source, _root_cause = key
        if obs >= _P_BASE_SOURCE_FALLBACK_MIN_OBS:
            p_base_by_bucket[key] = recovered / obs
        else:
            p_base_by_bucket[key] = p_base_source_fallback.get(source, p_base_mean)

    return p_base_mean, p_base_by_bucket, p_base_source_fallback


def calibrate(seed: int = CALIBRATION_SEED) -> CalibrationResult:
    _population, _signals, ledger = build_dataset(seed)
    risk_items_by_id = {r.risk_id: r for r in ledger.risk_items}

    outcomes = run_arm_a(seed)
    indexed = _contact_indexed_outcomes(outcomes)

    decay = _calibrate_decay(indexed)
    p_base_mean, p_base_by_bucket, p_base_source_fallback = _calibrate_p_base(
        indexed, risk_items_by_id
    )

    return CalibrationResult(
        decay=decay,
        p_base_mean=p_base_mean,
        p_base_by_bucket=p_base_by_bucket,
        p_base_source_fallback=p_base_source_fallback,
        seed=seed,
    )


def render_calibrated_module(result: CalibrationResult) -> str:
    """Deterministic source text for sampark/allocator/calibrated.py —
    sorted dict keys, fixed float formatting, so re-running calibrate()
    on the same seed produces byte-identical output."""
    bucket_lines = ",\n".join(
        f'    ("{source}", "{root_cause}"): {rate!r}'
        for (source, root_cause), rate in sorted(result.p_base_by_bucket.items())
    )
    fallback_lines = ",\n".join(
        f'    "{source}": {rate!r}'
        for source, rate in sorted(result.p_base_source_fallback.items())
    )
    return f'''"""GENERATED FILE — DO NOT EDIT BY HAND.

Produced by sim/calibration.py::main() from Arm A's outcome log at
seed {result.seed} only (Design Lock §14.1). Re-run
`python -m sim.calibration` to regenerate; do not hand-edit these
values.

Calibrated from data — never chosen, never tuned against Arm B results
(Design Lock, "Do not tune parameters after seeing results").
"""

from __future__ import annotations

CALIBRATION_SEED: int = {result.seed}

# exp(slope) of OLS of log(empirical recovery rate at contact index n)
# on n, over buckets with >= {_DECAY_MIN_BUCKET_OBS} observations.
DECAY: float = {result.decay!r}

# Global first-contact (n=0) recovery rate.
P_BASE_MEAN: float = {result.p_base_mean!r}

# First-contact recovery rate per (source, root_cause), falling back to
# the source-level rate below {_P_BASE_SOURCE_FALLBACK_MIN_OBS} observations.
P_BASE_BY_BUCKET: dict[tuple[str, str], float] = {{
{bucket_lines}
}}

# Source-level first-contact recovery rate, used as P_BASE_BY_BUCKET's
# fallback and as its own fallback to P_BASE_MEAN below
# {_P_BASE_MEAN_FALLBACK_MIN_OBS} observations.
P_BASE_SOURCE_FALLBACK: dict[str, float] = {{
{fallback_lines}
}}


def p_base(source: str, root_cause: str) -> float:
    """P_BASE[source, root_cause] with the documented fallback chain."""
    key = (source, root_cause)
    if key in P_BASE_BY_BUCKET:
        return P_BASE_BY_BUCKET[key]
    if source in P_BASE_SOURCE_FALLBACK:
        return P_BASE_SOURCE_FALLBACK[source]
    return P_BASE_MEAN
'''


def main() -> None:
    result = calibrate(CALIBRATION_SEED)
    module_source = render_calibrated_module(result)
    _CALIBRATED_MODULE_PATH.write_text(module_source, encoding="utf-8")
    print(f"seed: {result.seed}")
    print(f"DECAY: {result.decay:.6f}")
    print(f"P_BASE_MEAN: {result.p_base_mean:.6f}")
    print(f"P_BASE buckets: {len(result.p_base_by_bucket)}")
    print(f"wrote: {_CALIBRATED_MODULE_PATH}")


if __name__ == "__main__":
    main()
