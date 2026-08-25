"""sim/calibration.py — Design Lock §14.1.

Calibration must be deterministic given the calibration seed, and must
never be re-run per gate seed (Design Lock: "Calibrate on seed 42 only
... Re-calibrating per seed ... is a leak").
"""

from __future__ import annotations

from sim.calibration import CALIBRATION_SEED, calibrate, render_calibrated_module


def test_calibration_is_deterministic():
    result_a = calibrate(CALIBRATION_SEED)
    result_b = calibrate(CALIBRATION_SEED)
    assert result_a == result_b


def test_calibrated_decay_is_in_open_unit_interval():
    result = calibrate(CALIBRATION_SEED)
    assert 0.0 < result.decay < 1.0


def test_calibrated_p_base_mean_is_a_probability():
    result = calibrate(CALIBRATION_SEED)
    assert 0.0 < result.p_base_mean < 1.0


def test_rendered_module_is_byte_identical_across_runs():
    result_a = calibrate(CALIBRATION_SEED)
    result_b = calibrate(CALIBRATION_SEED)
    assert render_calibrated_module(result_a) == render_calibrated_module(result_b)


def test_calibrated_module_matches_the_committed_generated_file():
    """sampark/allocator/calibrated.py must be current with respect to
    sim/calibration.py's methodology — if this fails, someone edited
    calibrated.py by hand or ran calibration under a different
    methodology and forgot to regenerate."""
    from sampark.allocator import calibrated

    result = calibrate(CALIBRATION_SEED)
    assert calibrated.DECAY == result.decay
    assert calibrated.P_BASE_MEAN == result.p_base_mean
    assert calibrated.CALIBRATION_SEED == result.seed
