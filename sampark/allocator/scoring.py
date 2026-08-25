"""expected_net scoring — Design Lock §6.2, assembled from
sampark.policy.soft's terms. This module is the only place the frozen
formula's four terms are combined; sampark.policy.hard must never
import it (tests/allocator/test_structural_boundaries.py).

Invariants proven in the Design Lock and asserted in
tests/allocator/test_scoring_invariants.py:

    I1 — the admissible set (expected_net > 0) is downward-closed in n:
         once a candidate at contact index n is inadmissible, it stays
         inadmissible at every n' > n. (NOT unconditional monotonicity
         in n — that is false whenever B <= 0, which is always the
         denied region anyway. See the Design Lock's proof.)
    I2 — higher amount-at-risk never reduces the current-value
         contribution: requires b < 10_000, enforced at Candidate
         construction (sampark/allocator/candidate.py).
    I3 — determinism: identical inputs yield an identical float. No RNG,
         no clock, no HiddenResponseProfile, no Environment internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from sampark.allocator.candidate import Candidate
from sampark.allocator.constants import AGING_BONUS_PAISE
from sampark.policy.soft import channel_cost, fatigue, recovery_prior


@dataclass(frozen=True)
class ScoreBreakdown:
    p_hat: float
    gross_paise: float
    incentive_expected_paise: float
    channel_cost_paise: int
    fatigue_cost_paise: float
    expected_net_paise: float


def score(
    candidate: Candidate,
    effective_incentive_bps: int,
    n: int,
    other_open_amounts_paise: Sequence[int],
) -> ScoreBreakdown:
    """Design Lock §6.2's frozen formula, verbatim. `effective_incentive_bps`
    is the (possibly allocator-downgraded) incentive actually being
    scored — always <= candidate.request.requested_max_incentive_bps,
    which is itself <= the Registry's scope ceiling (enforced upstream,
    never re-clamped here)."""
    if effective_incentive_bps > candidate.request.requested_max_incentive_bps:
        raise ValueError(
            "effective_incentive_bps must not exceed the requested ceiling: "
            f"{effective_incentive_bps!r} > {candidate.request.requested_max_incentive_bps!r}"
        )

    p_hat = recovery_prior.p_hat(candidate.risk_item.source, candidate.risk_item.root_cause, n)
    gross_paise = p_hat * candidate.risk_item.amount_paise
    incentive_expected_paise = gross_paise * effective_incentive_bps / 10_000
    ch_cost = channel_cost.channel_cost_paise(candidate.request.requested_channel)
    fatigue_cost_paise = fatigue.fatigue_cost_paise(n, other_open_amounts_paise)

    expected_net_paise = gross_paise - ch_cost - incentive_expected_paise - fatigue_cost_paise

    return ScoreBreakdown(
        p_hat=p_hat,
        gross_paise=gross_paise,
        incentive_expected_paise=incentive_expected_paise,
        channel_cost_paise=ch_cost,
        fatigue_cost_paise=fatigue_cost_paise,
        expected_net_paise=expected_net_paise,
    )


def priority(
    expected_net_paise: float, windows_deferred: int, aging_bonus_paise: int = AGING_BONUS_PAISE
) -> float:
    """Ranking key — Design Lock §7. Aging affects RANKING ONLY; it must
    never be added before the expected_net > 0 admission test (the
    allocator applies this only after admission — see
    sampark/allocator/greedy.py)."""
    return expected_net_paise + aging_bonus_paise * windows_deferred
