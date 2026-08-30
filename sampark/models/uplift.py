"""Uplift model (T-learner) — Phase 6, spec §18.1.

A T-learner fits two separate response models — one on TREATED
observations, one on CONTROL observations — and reports their
difference as the estimated causal uplift of treatment. That requires
the training data to actually contain both arms, with enough
observations in each to fit a model.

Arm A's log does not. `agents/base.py::eligible_risk_items` returns
EVERY risk item of an agent's source, and every one of the four Phase 2
agents contacts every eligible item exactly once (`sim/arm_a.py`) — so
there is no untreated (never-contacted) control population for any
source. Separately, `INCENTIVE_BPS` is a fixed per-agent constant
(`agents/cart_recovery.py`, `agents/payment_retry.py`, etc.), so within
a single source every observation shares the identical incentive —
there is no incentive-level treatment/control split either. Design
Lock (Phase 4) §14.1 names this exact collinearity as the reason the
Phase 4 heuristic "cannot separate base rate from incentive response
using Arm A alone."

`detect_treatment_control_split` runs both checks and reports the
result as a `TreatmentControlReport`; `train_uplift_model` refuses to
fit anything the moment either check fails -- it never falls back to
manufacturing a split (e.g. an arbitrary contact-index or amount-based
proxy grouping) that would misrepresent a non-causal split as the
causal one this function's caller claims to compute. If a real holdout
arm exists in a future phase (spec section 8.9's Phase 7 attribution
holdout), this check passes and `fit_uplift_model` below fits for real.

`fit_uplift_model` is a genuine T-learner implementation, exercised by
`tests/models/test_uplift.py` against synthetic treatment/control data
constructed in the test itself — it is real, tested infrastructure,
not a stub, even though `train_uplift_model(seed=42)` will not reach it
on this dataset.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass
from typing import Sequence

from sampark.models.training_data import (
    TrainingRow,
    TreatmentArm,
    load_training_rows,
    load_training_rows_with_holdout,
)

_MIN_OBS_PER_ARM = 30  # a floor low enough to be honest about being a floor, not a target


@dataclass(frozen=True)
class TreatmentControlReport:
    """Per-source diagnostics from `detect_treatment_control_split`, kept
    even when the split fails, so the committed model artifact can state
    EXACTLY what was checked and what it found — not just "unavailable"."""

    distinct_incentive_bps_by_source: dict[str, tuple[int, ...]]
    uncontacted_fraction_by_source: dict[str, float]
    has_incentive_variation: bool
    has_uncontacted_control: bool


def _distinct_incentive_bps_by_source(rows: Sequence[TrainingRow]) -> dict[str, tuple[int, ...]]:
    by_source: dict[str, set[int]] = collections.defaultdict(set)
    for row in rows:
        by_source[row.source].add(row.incentive_bps)
    return {source: tuple(sorted(values)) for source, values in by_source.items()}


def _uncontacted_fraction_by_source(seed: int, rows: Sequence[TrainingRow]) -> dict[str, float]:
    """Fraction of each source's total RiskItems that never appear as a
    `TrainingRow` at all — i.e. were never contacted by any agent. Reads
    `sim.cli.build_dataset(seed)`'s ledger for the denominator (every
    RiskItem that source generated) and Arm A's own outcome log for the
    numerator (RiskItems actually contacted) — both already public,
    ground-truth-free sources this package is allowed to read."""
    from sim.cli import build_dataset

    _population, _signals, ledger = build_dataset(seed)
    total_by_source: dict[str, int] = collections.Counter(item.source for item in ledger.risk_items)
    contacted_risk_ids_by_source: dict[str, set[str]] = collections.defaultdict(set)
    for row in rows:
        contacted_risk_ids_by_source[row.source].add(row.risk_id)

    fractions: dict[str, float] = {}
    for source, total in total_by_source.items():
        contacted = len(contacted_risk_ids_by_source.get(source, ()))
        fractions[source] = 0.0 if total == 0 else 1.0 - (contacted / total)
    return fractions


def detect_treatment_control_split(seed: int) -> TreatmentControlReport:
    """Runs both adequacy checks against Arm A's seed-`seed` log. Never
    raises by itself — `train_uplift_model` decides what to do with the
    report; this function only measures."""
    rows = load_training_rows(seed)
    distinct = _distinct_incentive_bps_by_source(rows)
    uncontacted = _uncontacted_fraction_by_source(seed, rows)

    has_incentive_variation = any(len(values) >= 2 for values in distinct.values())
    has_uncontacted_control = any(frac > 0.0 for frac in uncontacted.values())

    return TreatmentControlReport(
        distinct_incentive_bps_by_source=distinct,
        uncontacted_fraction_by_source=uncontacted,
        has_incentive_variation=has_incentive_variation,
        has_uncontacted_control=has_uncontacted_control,
    )


@dataclass(frozen=True)
class UpliftModel:
    """A fitted T-learner: two independent response-probability models,
    one per arm, keyed by the SAME feature tuple `predict_uplift` uses.
    Deliberately a plain dict-backed lookup rather than a black-box
    estimator object — this is tabular data at demo scale, and a lookup
    table is exactly as auditable as `sampark/allocator/calibrated.py`'s
    P_BASE table, which this mirrors."""

    treated_response_by_bucket: dict[tuple[str, str], float]
    control_response_by_bucket: dict[tuple[str, str], float]

    def predict_uplift(self, source: str, root_cause: str) -> float:
        key = (source, root_cause)
        treated = self.treated_response_by_bucket.get(key)
        control = self.control_response_by_bucket.get(key)
        if treated is None or control is None:
            raise KeyError(f"no fitted response for bucket {key!r}")
        return treated - control


def fit_uplift_model(
    rows: Sequence[TrainingRow],
    is_treated: "collections.abc.Callable[[TrainingRow], bool]",
) -> UpliftModel:
    """Generic T-learner fit: partitions `rows` by `is_treated`, fits an
    independent empirical response rate per (source, root_cause) bucket
    within each partition. `is_treated` is supplied by the caller
    (never guessed here) because what constitutes "treatment" is a
    modeling decision this module must not make unilaterally — see
    `train_uplift_model`'s docstring for why no caller in this
    repository can supply one honestly today."""

    def _bucket_rates(subset: Sequence[TrainingRow]) -> dict[tuple[str, str], float]:
        totals: dict[tuple[str, str], list[int]] = collections.defaultdict(lambda: [0, 0])
        for row in subset:
            key = (row.source, row.root_cause)
            totals[key][0] += 1
            totals[key][1] += 1 if row.recovered else 0
        return {key: recovered / obs for key, (obs, recovered) in totals.items() if obs > 0}

    treated = [r for r in rows if is_treated(r)]
    control = [r for r in rows if not is_treated(r)]
    return UpliftModel(
        treated_response_by_bucket=_bucket_rates(treated),
        control_response_by_bucket=_bucket_rates(control),
    )


@dataclass(frozen=True)
class UpliftModelResult:
    available: bool
    reason: str | None
    report: TreatmentControlReport
    model: UpliftModel | None = None


def train_uplift_model(seed: int) -> UpliftModelResult:
    """The one entry point `sampark.models.artifact.build_model_artifact`
    calls. Runs the adequacy check FIRST; only reaches `fit_uplift_model`
    if it passes. On this dataset it will not — see module docstring —
    and this function returns `available=False` with the exact reason,
    never a model fit on a fabricated split."""
    report = detect_treatment_control_split(seed)

    if not report.has_uncontacted_control:
        max_uncontacted = max(report.uncontacted_fraction_by_source.values(), default=0.0)
        reason = (
            "no untreated (never-contacted) control population exists for any risk "
            "source: every eligible RiskItem is contacted by its matching Phase 2 "
            f"agent exactly once (max uncontacted fraction observed: {max_uncontacted:.4f}). "
            "A T-learner requires real treated/control variation; this dataset has none "
            "until a holdout arm exists (spec section 8.9, Phase 7)."
        )
        return UpliftModelResult(available=False, reason=reason, report=report, model=None)

    if not report.has_incentive_variation:
        reason = (
            "incentive_bps is a fixed per-agent constant within every risk source "
            f"(observed distinct values per source: {report.distinct_incentive_bps_by_source!r}), "
            "so incentive is perfectly collinear with source and cannot supply the "
            "treatment/control split either (Design Lock section 14.1)."
        )
        return UpliftModelResult(available=False, reason=reason, report=report, model=None)

    # Unreachable on the current dataset (both checks above fail first),
    # kept as real, tested infrastructure for the day a holdout arm makes
    # this reachable. See tests/models/test_uplift.py for a direct
    # exercise of fit_uplift_model against synthetic treated/control data.
    rows = load_training_rows(seed)
    model = fit_uplift_model(rows, is_treated=lambda r: r.incentive_bps > 0)
    return UpliftModelResult(available=True, reason=None, report=report, model=model)


# =============================================================================
# Phase 7 — holdout-aware path (spec §8.9). A SEPARATE set of functions,
# never modifying detect_treatment_control_split / train_uplift_model above
# (Phase 6's exact behavior, and hence sampark/models/artifact_data.py's
# committed content, is unaffected by anything below this line).
#
# _MIN_OBS_PER_ARM_HOLDOUT = 200 (raised from the pre-Phase-7 30 above,
# which was declared but NEVER ACTUALLY ENFORCED anywhere in this module —
# Phase 7 design lock, Decision 12) mirrors sim/calibration.py's own
# committed precedent (_DECAY_MIN_BUCKET_OBS = 200) rather than inventing a
# new number.
# =============================================================================

_MIN_OBS_PER_ARM_HOLDOUT = 200


def _n_by_bucket(rows: Sequence[TrainingRow]) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = collections.defaultdict(int)
    for row in rows:
        counts[(row.source, row.root_cause)] += 1
    return dict(counts)


def _bucket_rate(rows: Sequence[TrainingRow], bucket: tuple[str, str]) -> float | None:
    subset = [r for r in rows if (r.source, r.root_cause) == bucket]
    if not subset:
        return None
    return sum(1 for r in subset if r.recovered) / len(subset)


@dataclass(frozen=True)
class HoldoutTreatmentControlReport:
    """Phase 7's holdout-aware adequacy report — a SEPARATE type from
    `TreatmentControlReport` above, because the checks it runs are
    genuinely different: real randomized `TreatmentArm` labels (Phase 7
    design lock, Decision 15's hard restriction: control rows come ONLY
    from `sim.holdout`-derived customers, never from an
    allocator-declined item) instead of the incentive-collinearity /
    uncontacted-fraction proxies Phase 6 had to rely on before a holdout
    existed."""

    fraction: float
    n_treated_by_bucket: dict[tuple[str, str], int]
    n_control_by_bucket: dict[tuple[str, str], int]
    degenerate_buckets: tuple[tuple[str, str], ...]  # control rate exactly 0.0 or 1.0
    has_uncontacted_control: bool
    meets_min_obs_floor: bool
    under_floor_buckets: dict[tuple[str, str], tuple[int, int]]  # bucket -> (n_treated, n_control)


def detect_treatment_control_split_holdout(seed: int, fraction: float) -> HoldoutTreatmentControlReport:
    """Runs the Phase 7 adequacy checks against `load_training_rows_with_holdout`'s
    real TREATED/HOLDOUT split. Never raises — `train_uplift_model_holdout`
    decides what to do with the report; this function only measures."""
    rows = load_training_rows_with_holdout(seed, fraction)
    treated = [r for r in rows if r.treatment_arm is TreatmentArm.TREATED]
    control = [r for r in rows if r.treatment_arm is TreatmentArm.HOLDOUT]

    n_treated = _n_by_bucket(treated)
    n_control = _n_by_bucket(control)
    buckets = sorted(set(n_treated) | set(n_control))

    degenerate = [b for b in buckets if _bucket_rate(control, b) in (0.0, 1.0)]
    under_floor = {
        b: (n_treated.get(b, 0), n_control.get(b, 0))
        for b in buckets
        if n_treated.get(b, 0) < _MIN_OBS_PER_ARM_HOLDOUT or n_control.get(b, 0) < _MIN_OBS_PER_ARM_HOLDOUT
    }

    return HoldoutTreatmentControlReport(
        fraction=fraction,
        n_treated_by_bucket=n_treated,
        n_control_by_bucket=n_control,
        degenerate_buckets=tuple(degenerate),
        has_uncontacted_control=len(control) > 0,
        meets_min_obs_floor=bool(buckets) and not under_floor,
        under_floor_buckets=under_floor,
    )


@dataclass(frozen=True)
class UpliftModelResultHoldout:
    available: bool
    reason: str | None
    report: HoldoutTreatmentControlReport
    model: UpliftModel | None = None


def train_uplift_model_holdout(seed: int, fraction: float) -> UpliftModelResultHoldout:
    """The Phase 7 entry point `sampark.models.artifact.build_model_artifact`
    calls when `fraction > 0`. Mirrors `train_uplift_model`'s
    never-fabricate-a-split discipline exactly, against the real holdout
    instead of a proxy. `is_treated=lambda r: r.treatment_arm is
    TreatmentArm.TREATED` — NEVER the collinear `incentive_bps > 0` proxy
    `train_uplift_model` above was forced to use as a placeholder for the
    unreachable branch; that proxy is retired here in favor of the real
    label the holdout finally provides."""
    report = detect_treatment_control_split_holdout(seed, fraction)

    if not report.has_uncontacted_control:
        return UpliftModelResultHoldout(
            available=False,
            reason=f"fraction={fraction!r} produced zero HOLDOUT rows — no control population exists",
            report=report,
            model=None,
        )

    if not report.meets_min_obs_floor:
        return UpliftModelResultHoldout(
            available=False,
            reason=(
                f"one or more (source, root_cause) buckets fall below the "
                f"{_MIN_OBS_PER_ARM_HOLDOUT}-observation floor in at least one arm "
                f"(bucket -> (n_treated, n_control)): {report.under_floor_buckets!r}"
            ),
            report=report,
            model=None,
        )

    if report.degenerate_buckets:
        return UpliftModelResultHoldout(
            available=False,
            reason=f"degenerate (0%% or 100%%) control-arm recovery rate in buckets: {report.degenerate_buckets!r}",
            report=report,
            model=None,
        )

    rows = load_training_rows_with_holdout(seed, fraction)
    model = fit_uplift_model(rows, is_treated=lambda r: r.treatment_arm is TreatmentArm.TREATED)
    return UpliftModelResultHoldout(available=True, reason=None, report=report, model=model)


def evaluate_uplift_model_holdout(
    model: UpliftModel, seed: int, fraction: float
) -> dict[tuple[str, str], dict[str, float]]:
    """Out-of-sample evaluation (Phase 7 design lock, §D.3: train on seed
    42 only, evaluate on 7/101/2024/31337). For each `(source, root_cause)`
    bucket present in BOTH `model` and the evaluation seed's own real
    holdout split, compares `model.predict_uplift(...)` (fit on the
    TRAINING seed) against the REALIZED uplift on THIS seed's own data
    (`treated_rate - control_rate`, both freshly measured here, never
    read from `model`). Returns `{bucket: {"predicted": ..., "realized":
    ..., "abs_error": ...}}` — the caller (sim/train_phase7_models.py)
    aggregates this into a single reported MAE, never a single bucket
    cherry-picked to look good."""
    rows = load_training_rows_with_holdout(seed, fraction)
    treated = [r for r in rows if r.treatment_arm is TreatmentArm.TREATED]
    control = [r for r in rows if r.treatment_arm is TreatmentArm.HOLDOUT]

    buckets = sorted(set(model.treated_response_by_bucket) & set(model.control_response_by_bucket))
    result: dict[tuple[str, str], dict[str, float]] = {}
    for bucket in buckets:
        treated_rate = _bucket_rate(treated, bucket)
        control_rate = _bucket_rate(control, bucket)
        if treated_rate is None or control_rate is None:
            continue
        predicted = model.predict_uplift(*bucket)
        realized = treated_rate - control_rate
        result[bucket] = {
            "predicted": predicted,
            "realized": realized,
            "abs_error": abs(predicted - realized),
        }
    return result
