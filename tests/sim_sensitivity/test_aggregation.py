"""Sensitivity aggregation, crossing detection and prediction scoring — Phase 9A.

Fast tests over synthetic payloads. They exist so the reporting logic is
exercised at boundaries the real sweep may never visit — in particular the
crossing case, which the real data may not contain at all. A crossing detector
that has never been run against a crossing is not a detector.
"""

from __future__ import annotations

import pytest

from sim.sensitivity import (
    DIMENSION_BETA_FATIGUE,
    DIMENSION_BETA_INCENTIVE,
    _betas_for,
    _is_monotone_non_decreasing,
    _is_monotone_non_increasing,
    aggregate,
    evaluate_predictions,
    find_crossing,
)
from sim.environment import BETA_FATIGUE, BETA_INCENTIVE


def _point(value, seed, a_per, b_per, a_total=1000, b_total=900, contacts_a=20000, contacts_b=10000):
    return {
        "dimension": DIMENSION_BETA_FATIGUE,
        "value": value,
        "seed": seed,
        "a_per_contact_paise": a_per,
        "b_per_contact_paise": b_per,
        "a_recovered_paise": a_total,
        "b_recovered_paise": b_total,
        "a_contacts": contacts_a,
        "b_contacts": contacts_b,
        "uplift_ratio": b_per / a_per,
    }


def _payload(points, values, frozen=1.0):
    return {"points": points, "values": values, "frozen_value": frozen}


# --- only one coefficient moves per point -----------------------------------


def test_beta_fatigue_dimension_holds_incentive_at_its_frozen_value():
    bf, bi = _betas_for(DIMENSION_BETA_FATIGUE, 0.25)
    assert bf == 0.25
    assert bi == BETA_INCENTIVE


def test_beta_incentive_dimension_holds_fatigue_at_its_frozen_value():
    bf, bi = _betas_for(DIMENSION_BETA_INCENTIVE, 8.0)
    assert bf == BETA_FATIGUE
    assert bi == 8.0


def test_unknown_dimension_is_rejected():
    with pytest.raises(ValueError):
        _betas_for("beta_something_else", 1.0)


# --- aggregation arithmetic -------------------------------------------------


def test_aggregate_means_are_the_arithmetic_means_over_seeds():
    points = [_point(1.0, 7, 100.0, 200.0), _point(1.0, 42, 200.0, 500.0)]
    agg = aggregate(_payload(points, [1.0]))[0]
    assert agg["mean_a_per_contact_paise"] == 150.0
    assert agg["mean_b_per_contact_paise"] == 350.0
    assert agg["mean_uplift_ratio"] == pytest.approx(350.0 / 150.0)
    assert agg["n_seeds"] == 2


def test_aggregate_uplift_is_ratio_of_means_not_mean_of_ratios():
    """Deliberate: this matches sim/gate.py's own gate definition. The two
    differ whenever seeds are unbalanced, and the gate's convention wins."""
    points = [_point(1.0, 7, 100.0, 200.0), _point(1.0, 42, 200.0, 500.0)]
    agg = aggregate(_payload(points, [1.0]))[0]
    mean_of_ratios = (200.0 / 100.0 + 500.0 / 200.0) / 2
    assert agg["mean_uplift_ratio"] != pytest.approx(mean_of_ratios)
    assert agg["mean_uplift_ratio"] == pytest.approx(350.0 / 150.0)


def test_aggregate_marks_the_frozen_anchor():
    points = [_point(0.5, 7, 100.0, 200.0), _point(1.0, 7, 100.0, 200.0)]
    rows = aggregate(_payload(points, [0.5, 1.0], frozen=1.0))
    assert [r["is_frozen_anchor"] for r in rows] == [False, True]


def test_total_recovery_ratio_is_summed_not_averaged():
    points = [_point(1.0, 7, 100.0, 200.0, a_total=1000, b_total=500),
              _point(1.0, 42, 100.0, 200.0, a_total=3000, b_total=2500)]
    agg = aggregate(_payload(points, [1.0]))[0]
    assert agg["total_a_recovered_paise"] == 4000
    assert agg["total_b_recovered_paise"] == 3000
    assert agg["total_recovery_ratio"] == pytest.approx(0.75)


# --- crossing detection -----------------------------------------------------


def test_no_crossing_reported_when_b_always_wins():
    rows = [{"value": v, "b_beats_a_on_per_contact": True} for v in (0.0, 1.0, 2.0)]
    c = find_crossing(rows)
    assert c["crossing_exists_in_tested_range"] is False
    assert c["crossing_bracket"] is None


def test_crossing_is_reported_as_a_bracket_between_tested_points():
    rows = [
        {"value": 0.0, "b_beats_a_on_per_contact": False},
        {"value": 0.5, "b_beats_a_on_per_contact": True},
        {"value": 1.0, "b_beats_a_on_per_contact": True},
    ]
    c = find_crossing(rows)
    assert c["crossing_exists_in_tested_range"] is True
    assert c["crossing_bracket"] == [0.0, 0.5]
    assert c["losing_values"] == [0.0]


def test_crossing_is_never_interpolated():
    """Interpolating would invent a value that was not measured."""
    rows = [
        {"value": 0.0, "b_beats_a_on_per_contact": False},
        {"value": 2.0, "b_beats_a_on_per_contact": True},
    ]
    c = find_crossing(rows)
    assert c["crossing_bracket"] == [0.0, 2.0]
    assert "not interpolated" in c["note"]


# --- monotonicity helpers ---------------------------------------------------


@pytest.mark.parametrize(
    "xs,non_dec,non_inc",
    [
        ([1.0, 1.0, 2.0], True, False),
        ([2.0, 1.0], False, True),
        ([1.0, 1.0], True, True),
        ([1.0, 3.0, 2.0], False, False),
    ],
)
def test_monotonicity_helpers(xs, non_dec, non_inc):
    assert _is_monotone_non_decreasing(xs) is non_dec
    assert _is_monotone_non_increasing(xs) is non_inc


# --- prediction scoring must be able to FAIL --------------------------------


def _fatigue_agg(uplifts, ratios=None):
    values = [0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0][: len(uplifts)]
    ratios = ratios or [0.9] * len(uplifts)
    return [
        {
            "value": v,
            "mean_uplift_ratio": u,
            "total_recovery_ratio": r,
            "b_beats_a_on_per_contact": u > 1.0,
        }
        for v, u, r in zip(values, uplifts, ratios)
    ]


def _incentive_agg(uplifts):
    return [
        {"value": v, "mean_uplift_ratio": u, "total_recovery_ratio": 0.9, "b_beats_a_on_per_contact": u > 1.0}
        for v, u in zip([2.0, 4.0, 8.0], uplifts)
    ]


def _points_payload(contacts_vary: bool):
    pts = []
    for i, v in enumerate([0.0, 1.0]):
        pts.append(_point(v, 42, 100.0, 200.0, contacts_a=20000, contacts_b=10000 + (i if contacts_vary else 0)))
    return {"points": pts}


def test_predictions_pass_on_a_conforming_synthetic_dataset():
    agg_f = _fatigue_agg([1.4, 1.5, 1.6, 1.65, 1.7, 1.8, 1.9], ratios=[0.90, 0.91, 0.92, 0.93, 0.94, 0.95, 0.96])
    results = {p["id"]: p["result"] for p in evaluate_predictions(agg_f, _incentive_agg([1.9, 1.7, 1.5]), _points_payload(False))}
    assert results == {"P1": "PASS", "P2": "PASS", "P3": "PASS", "P4": "PASS", "P5": "PASS", "P6": "PASS"}


def test_p1_fails_on_a_non_monotone_trend():
    agg_f = _fatigue_agg([1.4, 1.9, 1.5])
    results = {p["id"]: p["result"] for p in evaluate_predictions(agg_f, _incentive_agg([1.9, 1.7, 1.5]), _points_payload(False))}
    assert results["P1"] == "FAIL"


def test_p2_fails_when_the_zero_point_is_outside_the_precommitted_band():
    agg_f = _fatigue_agg([1.95, 1.96, 1.97])
    results = {p["id"]: p["result"] for p in evaluate_predictions(agg_f, _incentive_agg([1.9, 1.7, 1.5]), _points_payload(False))}
    assert results["P2"] == "FAIL"


def test_p3_fails_when_a_crossing_exists():
    agg_f = _fatigue_agg([0.9, 1.2, 1.5])
    results = {p["id"]: p["result"] for p in evaluate_predictions(agg_f, _incentive_agg([1.9, 1.7, 1.5]), _points_payload(False))}
    assert results["P3"] == "FAIL"


def test_p4_fails_when_b_out_recovers_a_in_total():
    agg_f = _fatigue_agg([1.4, 1.5, 1.6], ratios=[1.05, 1.06, 1.07])
    results = {p["id"]: p["result"] for p in evaluate_predictions(agg_f, _incentive_agg([1.9, 1.7, 1.5]), _points_payload(False))}
    assert results["P4"] == "FAIL"


def test_p5_fails_when_a_contact_count_moves():
    agg_f = _fatigue_agg([1.4, 1.5, 1.6])
    results = {p["id"]: p["result"] for p in evaluate_predictions(agg_f, _incentive_agg([1.9, 1.7, 1.5]), _points_payload(True))}
    assert results["P5"] == "FAIL"


def test_p6_fails_when_incentive_trend_rises():
    agg_f = _fatigue_agg([1.4, 1.5, 1.6])
    results = {p["id"]: p["result"] for p in evaluate_predictions(agg_f, _incentive_agg([1.5, 1.7, 1.9]), _points_payload(False))}
    assert results["P6"] == "FAIL"
