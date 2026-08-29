"""Isotonic calibration — Phase 6, spec §7 ("calibration (isotonic)").

A thin, deterministic wrapper around
`sklearn.isotonic.IsotonicRegression`, bounded to [0, 1] since every
Phase 6 model produces a probability. `IsotonicRegression.fit` is a
deterministic pool-adjacent-violators fit — no RNG, no random_state
parameter exists on it — so re-fitting on the same inputs is always
byte-identical, which is what `tests/models/test_calibration.py`'s
determinism test checks directly rather than assuming.

Nothing in `sampark.models.uplift` or `sampark.models.fatigue_hazard`
currently reaches this module end to end, because both report
`available=False` on this dataset (see their own docstrings) — there is
nothing real to calibrate yet. This module is still built and tested on
its own synthetic data, per the Phase 6 contract's requirement that
calibration infrastructure exist and be correct, independent of whether
an upstream model exists to feed it today.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from sklearn.isotonic import IsotonicRegression


@dataclass(frozen=True)
class IsotonicCalibrator:
    """Wraps a fitted `IsotonicRegression`. `x_thresholds`/`y_thresholds`
    are the fitted step function's knot points, kept alongside the
    sklearn object so a committed artifact can serialize the calibrator
    as plain numbers (matching `sampark/allocator/calibrated.py`'s
    plain-dict style) without needing to pickle an estimator."""

    _model: IsotonicRegression
    x_thresholds: tuple[float, ...]
    y_thresholds: tuple[float, ...]

    def calibrate(self, raw_score: float) -> float:
        (value,) = self._model.predict([raw_score])
        return float(min(max(value, 0.0), 1.0))


def fit_isotonic_calibrator(raw_scores: Sequence[float], labels: Sequence[int]) -> IsotonicCalibrator:
    """`labels` must be 0/1 (a recovered/opted-out/etc. indicator).
    Deterministic given deterministic inputs — no randomness anywhere in
    an isotonic regression fit."""
    if len(raw_scores) != len(labels):
        raise ValueError(f"raw_scores and labels must be the same length, got {len(raw_scores)} and {len(labels)}")
    if len(raw_scores) < 2:
        raise ValueError("isotonic calibration needs at least 2 observations")

    model = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    model.fit(raw_scores, labels)
    return IsotonicCalibrator(
        _model=model,
        x_thresholds=tuple(float(x) for x in model.X_thresholds_),
        y_thresholds=tuple(float(y) for y in model.y_thresholds_),
    )
