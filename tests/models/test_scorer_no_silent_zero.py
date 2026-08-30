"""ModelBackedScorer — Phase 7 fix: no silent `.get(key, 0.0)` default
(design lock Part 7.2), and the level/uplift p_hat_mode selection
(Decision 5)."""

from __future__ import annotations

import datetime as dt
from uuid import uuid4

import pytest

from sampark.allocator.candidate import build_candidate
from sampark.contracts import GrantRequest, RiskItem
from sampark.models.artifact import ModelArtifact
from sampark.models.fatigue_hazard import FatigueHazardModel
from sampark.models.scorer import ModelBackedScorer
from sampark.models.uplift import UpliftModel

DECISION_AT = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)
DETECTED_AT = dt.datetime(2025, 9, 10, 6, 0, tzinfo=dt.timezone.utc)


def _candidate(source="abandoned_checkout", root_cause="price_hesitation"):
    item = RiskItem(
        risk_id="r1", source=source, amount_paise=1_000_000, root_cause=root_cause, detected_at=DETECTED_AT,
    )
    request = GrantRequest(
        request_id=uuid4(), agent_id="cart_recovery_agent", customer_id="c1", risk_id="r1",
        intent="cart_recovery", requested_channel="whatsapp", requested_max_incentive_bps=500,
        issued_at=DETECTED_AT, signature="sig",
    )
    return build_candidate(request, item, "c1", DECISION_AT)


def _artifact_missing_bucket():
    return ModelArtifact(
        seed=42, uplift_available=True, uplift_reason=None,
        uplift_model=UpliftModel(
            treated_response_by_bucket={("failed_payment", "insufficient_funds"): 0.5},
            control_response_by_bucket={("failed_payment", "insufficient_funds"): 0.2},
        ),
        fatigue_hazard_available=True, fatigue_hazard_reason=None,
        fatigue_hazard_model=FatigueHazardModel(hazard_by_bucket={("failed_payment", "insufficient_funds", 0): 0.1}),
    )


def test_missing_uplift_bucket_raises_not_silently_zero():
    """The candidate's (source, root_cause) is NOT in the artifact at
    all -- the OLD behavior silently returned p_hat=0.0; the fix raises."""
    scorer = ModelBackedScorer(artifact=_artifact_missing_bucket())
    candidate = _candidate(source="abandoned_checkout", root_cause="price_hesitation")
    with pytest.raises(KeyError):
        scorer.score(candidate, effective_incentive_bps=0, n=0, other_open_amounts_paise=())


def test_missing_hazard_bucket_raises_not_silently_zero():
    """Uplift bucket present, but n=1 was never fitted -- the OLD
    behavior silently priced fatigue at 0.0 (the anti-conservative
    defect: an unseen bucket becomes the MOST attractive candidate).
    The fix raises instead."""
    artifact = ModelArtifact(
        seed=42, uplift_available=True, uplift_reason=None,
        uplift_model=UpliftModel(
            treated_response_by_bucket={("abandoned_checkout", "price_hesitation"): 0.5},
            control_response_by_bucket={("abandoned_checkout", "price_hesitation"): 0.2},
        ),
        fatigue_hazard_available=True, fatigue_hazard_reason=None,
        # ONLY n=0 fitted -- n=1 deliberately absent.
        fatigue_hazard_model=FatigueHazardModel(
            hazard_by_bucket={("abandoned_checkout", "price_hesitation", 0): 0.1}
        ),
    )
    scorer = ModelBackedScorer(artifact=artifact)
    candidate = _candidate()
    with pytest.raises(KeyError):
        scorer.score(candidate, effective_incentive_bps=0, n=1, other_open_amounts_paise=())


def test_present_bucket_scores_without_error():
    artifact = ModelArtifact(
        seed=42, uplift_available=True, uplift_reason=None,
        uplift_model=UpliftModel(
            treated_response_by_bucket={("abandoned_checkout", "price_hesitation"): 0.5},
            control_response_by_bucket={("abandoned_checkout", "price_hesitation"): 0.2},
        ),
        fatigue_hazard_available=True, fatigue_hazard_reason=None,
        fatigue_hazard_model=FatigueHazardModel(
            hazard_by_bucket={("abandoned_checkout", "price_hesitation", 0): 0.1}
        ),
    )
    scorer = ModelBackedScorer(artifact=artifact)
    candidate = _candidate()
    result = scorer.score(candidate, effective_incentive_bps=0, n=0, other_open_amounts_paise=())
    assert result.p_hat == 0.5


def test_p_hat_mode_level_uses_treated_rate_directly():
    artifact = ModelArtifact(
        seed=42, uplift_available=True, uplift_reason=None,
        uplift_model=UpliftModel(
            treated_response_by_bucket={("abandoned_checkout", "price_hesitation"): 0.5},
            control_response_by_bucket={("abandoned_checkout", "price_hesitation"): 0.2},
        ),
        fatigue_hazard_available=True, fatigue_hazard_reason=None,
        fatigue_hazard_model=FatigueHazardModel(hazard_by_bucket={("abandoned_checkout", "price_hesitation", 0): 0.0}),
    )
    scorer = ModelBackedScorer(artifact=artifact, p_hat_mode="level")
    result = scorer.score(_candidate(), effective_incentive_bps=0, n=0, other_open_amounts_paise=())
    assert result.p_hat == pytest.approx(0.5)


def test_p_hat_mode_uplift_subtracts_control_rate():
    artifact = ModelArtifact(
        seed=42, uplift_available=True, uplift_reason=None,
        uplift_model=UpliftModel(
            treated_response_by_bucket={("abandoned_checkout", "price_hesitation"): 0.5},
            control_response_by_bucket={("abandoned_checkout", "price_hesitation"): 0.2},
        ),
        fatigue_hazard_available=True, fatigue_hazard_reason=None,
        fatigue_hazard_model=FatigueHazardModel(hazard_by_bucket={("abandoned_checkout", "price_hesitation", 0): 0.0}),
    )
    scorer = ModelBackedScorer(artifact=artifact, p_hat_mode="uplift")
    result = scorer.score(_candidate(), effective_incentive_bps=0, n=0, other_open_amounts_paise=())
    assert result.p_hat == pytest.approx(0.5 - 0.2)


def test_invalid_p_hat_mode_raises():
    artifact = _artifact_missing_bucket()
    scorer = ModelBackedScorer(artifact=artifact, p_hat_mode="bogus")
    candidate = _candidate(source="failed_payment", root_cause="insufficient_funds")
    with pytest.raises(ValueError):
        scorer.score(candidate, effective_incentive_bps=0, n=0, other_open_amounts_paise=())
