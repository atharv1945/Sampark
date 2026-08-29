"""sampark.models.fatigue_hazard — honest-fail detection + the generic
hazard fit, exercised against synthetic labels (never against Arm A,
which carries no opt-out labels at all)."""

from __future__ import annotations

import dataclasses

import pytest

from agents.types import ContactOutcome
from sampark.models.fatigue_hazard import (
    FatigueHazardModel,
    detect_opt_out_labels,
    fit_fatigue_hazard_model,
    train_fatigue_hazard_model,
)
from sampark.models.training_data import TrainingRow


def _row(source="abandoned_checkout", root_cause="price_hesitation", contact_index=0, risk_id="r1"):
    return TrainingRow(
        agent_id="a", customer_id="c", risk_id=risk_id, source=source, root_cause=root_cause,
        channel="whatsapp", incentive_bps=0, amount_paise=100_000, contact_index=contact_index,
        recovered=False, amount_recovered_paise=0, incentive_paise=0,
    )


def test_contact_outcome_carries_no_opt_out_field():
    """Structural, not incidental: this is what makes the fatigue-hazard
    model unavailable, checked against the live dataclass rather than
    trusted from a comment."""
    field_names = {f.name for f in dataclasses.fields(ContactOutcome)}
    assert not any("opt_out" in name or "optout" in name for name in field_names)


def test_detect_opt_out_labels_reports_unavailable():
    report = detect_opt_out_labels()
    assert report.has_opt_out_labels is False
    assert "no opt-out-related field" in report.reason


def test_train_fatigue_hazard_model_is_honestly_unavailable():
    result = train_fatigue_hazard_model(seed=42)
    assert result.available is False
    assert result.model is None
    assert result.reason is not None


def test_fit_fatigue_hazard_model_recovers_a_known_synthetic_hazard():
    rows = tuple(_row(contact_index=n, risk_id=f"r{n}-{i}") for n in (0, 1) for i in range(50))
    # contact_index 0: 10/50 opted out; contact_index 1: 40/50 opted out
    labels = [i < 10 for _n in (0,) for i in range(50)] + [i < 40 for _n in (1,) for i in range(50)]

    model = fit_fatigue_hazard_model(rows, labels)
    assert isinstance(model, FatigueHazardModel)
    assert model.predict_hazard("abandoned_checkout", "price_hesitation", 0) == pytest.approx(0.2)
    assert model.predict_hazard("abandoned_checkout", "price_hesitation", 1) == pytest.approx(0.8)


def test_fit_fatigue_hazard_model_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        fit_fatigue_hazard_model((_row(),), [])
