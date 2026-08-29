"""ModelBackedScorer — Phase 6's `Scorer` implementation, and the
deterministic fallback to the Phase 4 heuristic.

Implements `sampark.allocator.scorer.Scorer` (structural — this module
imports nothing from `sampark.allocator.scorer` beyond the protocol
it is satisfying at the type level). `sampark.allocator.greedy` never
imports `sampark.models` — the dependency runs the other way, exactly
like `sampark.allocator` never imports `sampark.policy.hard`
(`tests/allocator/test_structural_boundaries.py`).

`build_scorer` is the ONE place the fallback decision is made, and it
is made ONCE, at construction time, not per-candidate:

    - the committed artifact fails to load (missing, corrupt, or
      partial — `sampark.models.artifact.CommittedArtifactUnavailableError`)
    - or it loads but `ModelArtifact.is_valid_for_scoring()` is False
      (which is what actually happens on this dataset today, per
      `sampark/models/artifact_data.py`: both components report
      `available=False`)

either way, `build_scorer` returns a `sampark.allocator.scorer.HeuristicScorer`
— the SAME default every pre-Phase-6 caller already uses — and logs
(module logger `"sampark.models.scorer"`, WARNING level) exactly why.
This is spec §12.3's "graceful degradation": the allocator keeps
issuing grants under the heuristic; nothing about admission, ranking,
or budget arithmetic depends on whether a model was actually available.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from sampark.allocator import scoring
from sampark.allocator.candidate import Candidate
from sampark.allocator.constants import FORWARD_HORIZON_DAYS, LAMBDA_PER_CUSTOMER_DAY, MEAN_AMOUNT_PAISE
from sampark.allocator.scorer import HeuristicScorer, Scorer, default_scorer
from sampark.models.artifact import CommittedArtifactUnavailableError, ModelArtifact, load_committed_artifact
from sampark.policy.soft import channel_cost

logger = logging.getLogger("sampark.models.scorer")


@dataclass(frozen=True)
class ModelBackedScorer:
    """Scores using the fitted uplift/fatigue-hazard lookup tables
    instead of `sampark.allocator.calibrated`'s frozen constants. Only
    ever constructed by `build_scorer` after `artifact.is_valid_for_scoring()`
    has already been confirmed True — this class itself does not
    re-check validity per call, to keep the hot scoring path free of
    per-candidate branching on artifact state."""

    artifact: ModelArtifact

    def score(
        self,
        candidate: Candidate,
        effective_incentive_bps: int,
        n: int,
        other_open_amounts_paise: Sequence[int],
    ) -> scoring.ScoreBreakdown:
        if effective_incentive_bps > candidate.request.requested_max_incentive_bps:
            raise ValueError(
                "effective_incentive_bps must not exceed the requested ceiling: "
                f"{effective_incentive_bps!r} > {candidate.request.requested_max_incentive_bps!r}"
            )

        source = candidate.risk_item.source
        root_cause = candidate.risk_item.root_cause
        model = self.artifact.uplift_model
        assert model is not None  # guaranteed by build_scorer's is_valid_for_scoring() gate
        p_hat = model.treated_response_by_bucket.get(
            (source, root_cause), model.control_response_by_bucket.get((source, root_cause), 0.0)
        )

        gross_paise = p_hat * candidate.risk_item.amount_paise
        incentive_expected_paise = gross_paise * effective_incentive_bps / 10_000
        ch_cost = channel_cost.channel_cost_paise(candidate.request.requested_channel)

        hazard_model = self.artifact.fatigue_hazard_model
        assert hazard_model is not None  # same gate
        hazard = hazard_model.hazard_by_bucket.get((source, root_cause, n), 0.0)
        future_count = LAMBDA_PER_CUSTOMER_DAY * FORWARD_HORIZON_DAYS
        v_forward_items = len(other_open_amounts_paise) + future_count
        v_forward = (
            (sum(other_open_amounts_paise) + future_count * MEAN_AMOUNT_PAISE) / v_forward_items
            if v_forward_items > 0
            else 0.0
        )
        fatigue_cost_paise = hazard * v_forward

        expected_net_paise = gross_paise - ch_cost - incentive_expected_paise - fatigue_cost_paise
        return scoring.ScoreBreakdown(
            p_hat=p_hat,
            gross_paise=gross_paise,
            incentive_expected_paise=incentive_expected_paise,
            channel_cost_paise=ch_cost,
            fatigue_cost_paise=fatigue_cost_paise,
            expected_net_paise=expected_net_paise,
        )


def build_scorer() -> Scorer:
    """The one factory Phase 6 evidence runs call. Never raises: any
    failure to obtain a valid model artifact is caught here and turned
    into the deterministic heuristic fallback, with the reason logged.
    Two independent calls with the same committed artifact always
    return the same kind of scorer — this function reads no wall clock
    and no RNG."""
    try:
        artifact = load_committed_artifact()
    except CommittedArtifactUnavailableError as exc:
        logger.warning("Phase 6 model artifact unavailable, falling back to HeuristicScorer: %s", exc)
        return default_scorer()

    if not artifact.is_valid_for_scoring():
        logger.warning(
            "Phase 6 model artifact present but not valid for scoring "
            "(uplift_available=%s, fatigue_hazard_available=%s) -- falling back to HeuristicScorer. "
            "uplift_reason=%r fatigue_hazard_reason=%r",
            artifact.uplift_available,
            artifact.fatigue_hazard_available,
            artifact.uplift_reason,
            artifact.fatigue_hazard_reason,
        )
        return default_scorer()

    return ModelBackedScorer(artifact=artifact)


__all__ = ["ModelBackedScorer", "build_scorer", "HeuristicScorer"]
