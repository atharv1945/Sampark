"""sampark.attribution.credit — Phase 7 (spec §8.9) arithmetic."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from sampark.attribution.baseline import BaselineRate
from sampark.attribution.credit import NS_ATTRIBUTION, compute_credit, credit_id_for
from sim.natural import NATURAL_MODEL_VERSION

OBSERVED_AT = dt.datetime(2025, 10, 9, tzinfo=dt.timezone.utc)


def _rate(rate, n=500, level="source", stratum="src"):
    return BaselineRate(stratum=stratum, level=level, rate=rate, n=n)


def test_credit_id_is_uuid5_deterministic():
    grant_id = uuid.uuid4()
    a = credit_id_for(grant_id)
    b = credit_id_for(grant_id)
    assert a == b
    assert a == uuid.uuid5(NS_ATTRIBUTION, str(grant_id))


def test_positive_credit_when_observed_exceeds_expected():
    credit = compute_credit(
        grant_id=uuid.uuid4(), observed_recovered_paise=100_000, amount_paise=100_000,
        baseline=_rate(0.05), holdout_fraction=0.10, observed_at=OBSERVED_AT,
    )
    assert credit.natural_rate_bps == 500
    assert credit.expected_natural_paise == 5_000
    assert credit.credited_recovery_paise == 95_000


def test_negative_credit_when_item_did_not_recover():
    """An unrecovered item still consumed a contact against a positive
    baseline — the credit is negative, and must NOT be clamped."""
    credit = compute_credit(
        grant_id=uuid.uuid4(), observed_recovered_paise=0, amount_paise=100_000,
        baseline=_rate(0.10), holdout_fraction=0.10, observed_at=OBSERVED_AT,
    )
    assert credit.natural_rate_bps == 1000
    assert credit.expected_natural_paise == 10_000
    assert credit.credited_recovery_paise == -10_000


def test_natural_rate_bps_is_integer_never_float():
    credit = compute_credit(
        grant_id=uuid.uuid4(), observed_recovered_paise=100_000, amount_paise=100_000,
        baseline=_rate(0.05285), holdout_fraction=0.10, observed_at=OBSERVED_AT,
    )
    assert isinstance(credit.natural_rate_bps, int)
    assert isinstance(credit.expected_natural_paise, int)
    assert isinstance(credit.credited_recovery_paise, int)


def test_holdout_fraction_bps_conversion():
    credit = compute_credit(
        grant_id=uuid.uuid4(), observed_recovered_paise=0, amount_paise=1000,
        baseline=_rate(0.0), holdout_fraction=0.20, observed_at=OBSERVED_AT,
    )
    assert credit.holdout_fraction_bps == 2000


def test_baseline_metadata_carried_through():
    credit = compute_credit(
        grant_id=uuid.uuid4(), observed_recovered_paise=0, amount_paise=1000,
        baseline=_rate(0.1, n=317, level="source_root_cause", stratum="mandate_failure.insufficient_funds"),
        holdout_fraction=0.10, observed_at=OBSERVED_AT,
    )
    assert credit.baseline_level == "source_root_cause"
    assert credit.baseline_stratum == "mandate_failure.insufficient_funds"
    assert credit.baseline_holdout_n == 317


def test_natural_model_version_matches_sim_natural():
    credit = compute_credit(
        grant_id=uuid.uuid4(), observed_recovered_paise=0, amount_paise=1000,
        baseline=_rate(0.0), holdout_fraction=0.10, observed_at=OBSERVED_AT,
    )
    assert credit.natural_model_version == NATURAL_MODEL_VERSION


def test_arithmetic_invariant_always_holds():
    """credited = observed - expected, for a range of realistic values —
    the same invariant the DB CHECK constraint enforces independently."""
    for observed, amount, rate in [(0, 50_000, 0.02), (50_000, 50_000, 0.30), (200_000, 500_000, 0.0)]:
        credit = compute_credit(
            grant_id=uuid.uuid4(), observed_recovered_paise=observed, amount_paise=amount,
            baseline=_rate(rate), holdout_fraction=0.10, observed_at=OBSERVED_AT,
        )
        assert credit.credited_recovery_paise == credit.observed_recovered_paise - credit.expected_natural_paise
