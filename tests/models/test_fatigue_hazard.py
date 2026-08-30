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


def test_contact_outcome_now_carries_an_opt_out_field():
    """Phase 6 asserted the ABSENCE of this field (that was the honest
    finding at the time). Phase 7 (spec §8.9) deliberately adds it —
    `agents.types.ContactOutcome.opt_out` / `.opt_out_channel`, defaulted
    so every pre-Phase-7 caller is unaffected. This test is UPDATED, not
    deleted, to record the transition explicitly (Phase 7 design lock,
    Decision 13's principle, applied to a second Phase-6 structural test
    this session found in the same position)."""
    field_names = {f.name for f in dataclasses.fields(ContactOutcome)}
    assert any("opt_out" in name or "optout" in name for name in field_names)


def test_detect_opt_out_labels_reports_available_as_of_phase_7():
    """UPDATED from Phase 6's `test_detect_opt_out_labels_reports_unavailable`
    — the structural check is dataset-agnostic by design (its own
    docstring), and the field genuinely exists now. Whether a SPECIFIC
    dataset has any positive label is answered separately by
    `train_fatigue_hazard_model`'s own data-volume check, exercised in
    `test_train_fatigue_hazard_model_is_honestly_unavailable_on_world_v1_arm_a`
    below."""
    report = detect_opt_out_labels()
    assert report.has_opt_out_labels is True
    assert "structurally available" in report.reason


def test_train_fatigue_hazard_model_is_honestly_unavailable_on_world_v1_arm_a():
    """Phase 6's original entry point (no `fraction` argument) still
    reports unavailable — Arm A always builds its Environment at
    world="v1" (sim/arm_a.py is frozen), which never draws an opt-out
    label, for any seed. The REASON text changed (now cites the real
    zero-positive-labels finding instead of "no seed can ever pass"),
    but the conclusion — and hence sampark/models/artifact_data.py's
    FATIGUE_HAZARD_AVAILABLE=False — is unchanged."""
    result = train_fatigue_hazard_model(seed=42)
    assert result.available is False
    assert result.model is None
    assert result.reason is not None
    assert "0 positive labels" in result.reason


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
