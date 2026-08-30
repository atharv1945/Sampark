"""sampark.attribution.baseline — Phase 7 (spec §8.9, Decision 15's hard
restriction)."""

from __future__ import annotations

import datetime as dt

import pytest

from sampark.attribution.baseline import (
    LEVEL_GLOBAL,
    LEVEL_SOURCE,
    LEVEL_SOURCE_ROOT_CAUSE,
    InsufficientHoldoutDataError,
    build_baseline_estimator,
)
from sim.natural import NaturalOutcome

HORIZON = dt.datetime(2025, 10, 9, tzinfo=dt.timezone.utc)


def _natural(customer_id, source, root_cause, recovered, amount_paise=100_000, i=0):
    return NaturalOutcome(
        risk_id=f"r{customer_id}-{i}", customer_id=customer_id, source=source, root_cause=root_cause,
        amount_paise=amount_paise, p_natural=0.1, recovered=recovered, amount_recovered_paise=amount_paise if recovered else 0,
        observed_at=HORIZON,
    )


def test_raises_on_empty_holdout():
    with pytest.raises(InsufficientHoldoutDataError):
        build_baseline_estimator(frozenset(), [])


def test_endogenous_uncontacted_item_is_silently_excluded_from_the_estimate():
    """THE anti-inflation test for the baseline estimator: a NaturalOutcome
    whose customer is NOT in the held-out set (an allocator-declined
    item, per Decision 1's Option 2) must never influence any rate."""
    held_out = frozenset({"holdout-1"})
    outcomes = [
        _natural("holdout-1", "s", "rc", recovered=True, i=0),
        # A high-value allocator-declined outcome that would massively
        # inflate the rate if it leaked in:
        _natural("declined-1", "s", "rc", recovered=True, i=1),
        _natural("declined-2", "s", "rc", recovered=True, i=2),
        _natural("declined-3", "s", "rc", recovered=True, i=3),
    ]
    estimator = build_baseline_estimator(held_out, outcomes)
    # Only the ONE held-out observation should count -> global rate is
    # exactly 1/1 = 1.0 (not 4/4, which it would coincidentally also be
    # here -- construct a case where they'd differ to make this a real test).
    assert estimator.global_rate.n == 1


def test_endogenous_exclusion_changes_the_actual_rate_value():
    held_out = frozenset({"holdout-1"})
    outcomes = [
        _natural("holdout-1", "s", "rc", recovered=False, i=0),  # holdout: did NOT recover
        _natural("declined-1", "s", "rc", recovered=True, i=1),   # declined: recovered (must be excluded)
        _natural("declined-2", "s", "rc", recovered=True, i=2),
    ]
    estimator = build_baseline_estimator(held_out, outcomes)
    assert estimator.global_rate.rate == 0.0  # if the declined items leaked in, this would be 2/3
    assert estimator.global_rate.n == 1


def test_stratum_hierarchy_prefers_source_root_cause_when_it_clears_the_floor():
    held_out = frozenset(f"c{i}" for i in range(200))
    outcomes = [_natural(f"c{i}", "src", "rc", recovered=(i % 3 == 0), i=i) for i in range(120)]
    estimator = build_baseline_estimator(held_out, outcomes)
    rate = estimator.rate_for("src", "rc")
    assert rate.level == LEVEL_SOURCE_ROOT_CAUSE
    assert rate.n == 120


def test_stratum_hierarchy_falls_back_to_source_when_bucket_is_thin():
    held_out = frozenset(f"c{i}" for i in range(200))
    outcomes = (
        [_natural(f"c{i}", "src", "common_rc", recovered=(i % 5 == 0), i=i) for i in range(50)]
        + [_natural(f"d{i}", "src", "rare_rc", recovered=True, i=i) for i in range(5)]
    )
    estimator = build_baseline_estimator(held_out, outcomes)
    rate = estimator.rate_for("src", "rare_rc")
    assert rate.level == LEVEL_SOURCE
    assert rate.stratum == "src"


def test_stratum_hierarchy_falls_back_to_global_when_source_is_also_thin():
    held_out = frozenset(f"c{i}" for i in range(50))
    outcomes = [_natural(f"c{i}", "rare_src", "rare_rc", recovered=True, i=i) for i in range(5)]
    estimator = build_baseline_estimator(held_out, outcomes)
    rate = estimator.rate_for("rare_src", "rare_rc")
    assert rate.level == LEVEL_GLOBAL


def test_deterministic_given_the_same_inputs():
    held_out = frozenset({"c1", "c2"})
    outcomes = [_natural("c1", "s", "rc", True, i=0), _natural("c2", "s", "rc", False, i=1)]
    a = build_baseline_estimator(held_out, outcomes)
    b = build_baseline_estimator(held_out, outcomes)
    assert a.global_rate == b.global_rate
