"""sampark.models.calibration — isotonic calibrator correctness and
determinism, on synthetic data (nothing in this repository trains a
model to feed it yet — see sampark/models/uplift.py and
fatigue_hazard.py)."""

from __future__ import annotations

from sampark.models.calibration import fit_isotonic_calibrator


def test_calibrator_output_is_monotone_nondecreasing():
    raw = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    labels = [0, 0, 1, 0, 1, 1, 0, 1, 1]
    calibrator = fit_isotonic_calibrator(raw, labels)

    calibrated = [calibrator.calibrate(x) for x in raw]
    assert all(a <= b + 1e-12 for a, b in zip(calibrated, calibrated[1:]))


def test_calibrator_output_is_bounded_in_unit_interval():
    raw = [0.0, 0.5, 1.0, 2.0, -1.0]
    labels = [0, 1, 1, 1, 0]
    calibrator = fit_isotonic_calibrator(raw, labels)
    for x in raw:
        y = calibrator.calibrate(x)
        assert 0.0 <= y <= 1.0


def test_calibrator_is_deterministic_across_refits():
    raw = [0.1, 0.3, 0.2, 0.9, 0.6, 0.4]
    labels = [0, 1, 0, 1, 1, 0]
    a = fit_isotonic_calibrator(raw, labels)
    b = fit_isotonic_calibrator(raw, labels)
    for x in [0.05, 0.15, 0.35, 0.55, 0.75, 0.95]:
        assert a.calibrate(x) == b.calibrate(x)


def test_calibrator_rejects_mismatched_lengths():
    import pytest

    with pytest.raises(ValueError):
        fit_isotonic_calibrator([0.1, 0.2], [1])


def test_calibrator_rejects_too_few_observations():
    import pytest

    with pytest.raises(ValueError):
        fit_isotonic_calibrator([0.5], [1])
