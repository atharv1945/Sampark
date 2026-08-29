"""sampark.models.uplift — honest-fail detection + the generic T-learner
fit, exercised against synthetic data (never against Arm A, which this
module documents cannot support a real fit today)."""

from __future__ import annotations

import datetime as dt

import pytest

from sampark.models.training_data import TrainingRow
from sampark.models.uplift import (
    UpliftModel,
    detect_treatment_control_split,
    fit_uplift_model,
    train_uplift_model,
)

_NOW = dt.datetime(2025, 9, 10, 6, 0, tzinfo=dt.timezone.utc)


def _row(source="abandoned_checkout", root_cause="price_hesitation", incentive_bps=0, recovered=False, risk_id="r1"):
    return TrainingRow(
        agent_id="a", customer_id="c", risk_id=risk_id, source=source, root_cause=root_cause,
        channel="whatsapp", incentive_bps=incentive_bps, amount_paise=100_000, contact_index=0,
        recovered=recovered, amount_recovered_paise=100_000 if recovered else 0, incentive_paise=0,
    )


def test_arm_a_seed_42_has_no_uncontacted_control_population():
    """The empirical fact this whole module exists to detect: on the
    real dataset, Arm A contacts every eligible risk item, so there is
    no untreated observation for any source."""
    report = detect_treatment_control_split(seed=42)
    assert report.has_uncontacted_control is False
    assert all(frac == 0.0 for frac in report.uncontacted_fraction_by_source.values())


def test_arm_a_seed_42_has_no_incentive_variation_within_source():
    report = detect_treatment_control_split(seed=42)
    assert report.has_incentive_variation is False
    for values in report.distinct_incentive_bps_by_source.values():
        assert len(values) == 1


def test_train_uplift_model_on_seed_42_is_honestly_unavailable():
    result = train_uplift_model(seed=42)
    assert result.available is False
    assert result.model is None
    assert result.reason is not None
    assert "control" in result.reason or "collinear" in result.reason


def test_fit_uplift_model_recovers_a_known_synthetic_uplift():
    """Real infrastructure test: construct rows with a KNOWN uplift (the
    treated bucket recovers strictly more often than control) and prove
    fit_uplift_model measures it -- independent of whether Arm A can
    ever supply such rows."""
    rows = (
        *([_row(incentive_bps=500, recovered=True, risk_id=f"t{i}") for i in range(80)]),
        *([_row(incentive_bps=500, recovered=False, risk_id=f"t{i}f") for i in range(20)]),
        *([_row(incentive_bps=0, recovered=True, risk_id=f"c{i}") for i in range(20)]),
        *([_row(incentive_bps=0, recovered=False, risk_id=f"c{i}f") for i in range(80)]),
    )
    model = fit_uplift_model(rows, is_treated=lambda r: r.incentive_bps > 0)
    assert isinstance(model, UpliftModel)
    uplift = model.predict_uplift("abandoned_checkout", "price_hesitation")
    assert uplift == pytest.approx(0.8 - 0.2, abs=1e-9)


def test_fit_uplift_model_raises_key_error_for_unseen_bucket():
    rows = (_row(recovered=True),)
    model = fit_uplift_model(rows, is_treated=lambda r: True)
    with pytest.raises(KeyError):
        model.predict_uplift("mandate_failure", "unknown")
