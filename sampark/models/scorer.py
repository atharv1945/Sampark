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
    per-candidate branching on artifact state.

    `p_hat_mode` (Phase 7 design lock, Decision 5) — `"level"` (default,
    matches the ORIGINAL Phase 6 formula exactly: `p_hat = treated
    response rate`) or `"uplift"` (`p_hat = treated - control`, the
    causally correct quantity, since part of the treated rate would have
    happened anyway). Shipped as two DISTINCT, explicitly-selected modes
    rather than silently switching Phase 6's formula: `phase7_model`
    (level) and `phase7_model_uplift` (uplift) are separate, named
    ablations (`sim/arm_b_cli.py`), so the choice is measured, not argued.

    Neither `treated_response_by_bucket` NOR `hazard_by_bucket` is ever
    read with a silent `.get(key, 0.0)` default (Phase 7 design lock,
    Part 7.2 — the exact defect that made an unseen bucket the MOST
    attractive candidate to contact, backwards). A genuinely missing
    bucket raises `KeyError`, exactly like `UpliftModel.predict_uplift`
    already does — loud and honest, never silently wrong."""

    artifact: ModelArtifact
    p_hat_mode: str = "level"

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
        if self.p_hat_mode not in ("level", "uplift"):
            raise ValueError(f"p_hat_mode must be 'level' or 'uplift', got {self.p_hat_mode!r}")

        source = candidate.risk_item.source
        root_cause = candidate.risk_item.root_cause
        model = self.artifact.uplift_model
        assert model is not None  # guaranteed by build_scorer's is_valid_for_scoring() gate

        treated_rate = model.treated_response_by_bucket[(source, root_cause)]
        if self.p_hat_mode == "level":
            p_hat = treated_rate
        else:
            control_rate = model.control_response_by_bucket[(source, root_cause)]
            p_hat = treated_rate - control_rate

        gross_paise = p_hat * candidate.risk_item.amount_paise
        incentive_expected_paise = gross_paise * effective_incentive_bps / 10_000
        ch_cost = channel_cost.channel_cost_paise(candidate.request.requested_channel)

        hazard_model = self.artifact.fatigue_hazard_model
        assert hazard_model is not None  # same gate
        hazard = hazard_model.hazard_by_bucket[(source, root_cause, n)]
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


def build_scorer(module_name: str = "sampark.models.artifact_data", p_hat_mode: str = "level") -> Scorer:
    """The one factory Phase 6 evidence runs call. Never raises: any
    failure to obtain a valid model artifact is caught here and turned
    into the deterministic heuristic fallback, with the reason logged.
    Two independent calls with the same committed artifact always
    return the same kind of scorer — this function reads no wall clock
    and no RNG.

    `module_name` / `p_hat_mode` (Phase 7, additive — every pre-Phase-7
    call site omits both and gets byte-identical Phase 6 behavior). The
    Phase 7 evidence CLI passes `module_name="sampark.models.artifact_data_phase7"`
    for the `phase7_model`/`phase7_model_uplift` ablations, and
    `p_hat_mode="uplift"` for the latter only (Decision 5)."""
    try:
        artifact = load_committed_artifact(module_name)
    except CommittedArtifactUnavailableError as exc:
        logger.warning("Model artifact (%s) unavailable, falling back to HeuristicScorer: %s", module_name, exc)
        return default_scorer()

    if not artifact.is_valid_for_scoring():
        logger.warning(
            "Model artifact (%s) present but not valid for scoring "
            "(uplift_available=%s, fatigue_hazard_available=%s) -- falling back to HeuristicScorer. "
            "uplift_reason=%r fatigue_hazard_reason=%r",
            module_name,
            artifact.uplift_available,
            artifact.fatigue_hazard_available,
            artifact.uplift_reason,
            artifact.fatigue_hazard_reason,
        )
        return default_scorer()

    return ModelBackedScorer(artifact=artifact, p_hat_mode=p_hat_mode)


__all__ = ["ModelBackedScorer", "build_scorer", "HeuristicScorer"]
