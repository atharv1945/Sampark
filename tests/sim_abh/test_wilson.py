"""Wilson score interval — Phase 9B.

Two kinds of test, deliberately:

1. INVARIANT tests, which would catch a wrong formula that happens to agree
   with the committed number by luck (containment, monotone narrowing in n,
   the boundary cases where Wald degenerates).
2. ONE reproduction test against the committed Phase 7 evidence. Phase 7's
   interval was produced by an ad-hoc session script that was never committed
   as code; `sim/abh_table.py` is the first committed implementation. Pinning
   it against `results/phase7_holdout_validity_seed42_f10.json` is what makes
   Phase 9's extension of that check to all ten cells trustworthy — and what
   stops `Z_95` silently drifting to scipy's 1.959963985.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sim.abh_table import Z_95, holdout_validity, wilson_interval

_RESULTS = Path(__file__).resolve().parents[2] / "results"


def test_reproduces_committed_phase7_interval_exactly():
    committed = json.loads(
        (_RESULTS / "phase7_holdout_validity_seed42_f10.json").read_text(encoding="utf-8")
    )
    mine = holdout_validity(42, 0.10)

    c = committed["holdout_estimate"]
    m = mine["holdout_estimate"]
    assert m["n"] == c["n"]
    assert m["rate"] == c["rate"]
    # Exact float equality is the point: a different z, or a Wald interval,
    # would differ in the low decimals and pass a tolerance-based check.
    assert m["wilson_ci_95"][0] == c["wilson_ci_95"][0]
    assert m["wilson_ci_95"][1] == c["wilson_ci_95"][1]
    assert mine["arm_h_ground_truth"]["rate"] == committed["arm_h_ground_truth"]["rate"]
    assert mine["arm_h_within_holdout_ci"] == committed["arm_h_within_holdout_ci"]


def test_z_is_the_committed_constant():
    """Guards the constant itself: 1.96, not scipy's norm.ppf(0.975)."""
    assert Z_95 == 1.96


@pytest.mark.parametrize("successes,n", [(0, 10), (1, 10), (5, 10), (9, 10), (10, 10), (100, 1962), (1057, 20000)])
def test_interval_is_within_unit_range_and_ordered(successes, n):
    lo, hi = wilson_interval(successes, n)
    assert lo <= hi
    assert 0.0 <= lo <= 1.0
    assert 0.0 <= hi <= 1.0


@pytest.mark.parametrize("successes,n", [(1, 10), (5, 10), (9, 10), (100, 1962)])
def test_interval_contains_the_point_estimate(successes, n):
    lo, hi = wilson_interval(successes, n)
    assert lo <= successes / n <= hi


def test_zero_successes_gives_a_strictly_positive_upper_bound():
    """The regime where the normal approximation collapses to [0, 0] and
    Wilson does not. This is why Wilson was chosen."""
    lo, hi = wilson_interval(0, 500)
    assert lo >= 0.0
    assert hi > 0.0


def test_all_successes_gives_an_upper_bound_of_one_or_less():
    lo, hi = wilson_interval(500, 500)
    assert hi <= 1.0
    assert lo < 1.0


def test_interval_narrows_as_n_grows_at_a_fixed_rate():
    widths = []
    for n in (100, 1_000, 10_000, 100_000):
        lo, hi = wilson_interval(n // 20, n)  # hold p at 5%
        widths.append(hi - lo)
    assert widths == sorted(widths, reverse=True), widths


def test_rejects_impossible_inputs():
    with pytest.raises(ValueError):
        wilson_interval(5, 0)
    with pytest.raises(ValueError):
        wilson_interval(-1, 10)
    with pytest.raises(ValueError):
        wilson_interval(11, 10)
