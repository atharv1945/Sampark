"""AllocationOutcome / OutcomeKind / the deferral-exhaustion helper —
Phase 4C hardening (W3): shared by `sampark.mediation.hard_filter` (which
produces DENIED/DEFERRED outcomes for hard-INADMISSIBLE candidates,
Design Lock §5.1) and `sampark.allocator.greedy` (which produces
DENIED/DEFERRED/GRANTED outcomes for hard-admissible survivors, Design
Lock §8) — so both layers emit the identical outcome shape without
either one depending on the other's evaluation logic.

This module imports NOTHING from `sampark.policy` — it has no notion of
a HardVerdict, a Verdict, or a policy evaluator, only the outcome
dataclass and the aging/exhaustion arithmetic (Design Lock §7, which is
the ALLOCATOR's fairness responsibility, applied uniformly regardless
of whether a candidate is being deferred by a hard-policy rule or by
losing a competitive allocation round).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from sampark.allocator import scoring
from sampark.allocator.candidate import Candidate
from sampark.allocator.constants import MAX_DEFERRAL_WINDOWS
from sampark.allocator.reason_codes import DEFERRAL_EXHAUSTED
from sampark.budget.windows import window_id_for
from sampark.contracts import Grant


class OutcomeKind(Enum):
    GRANTED = "GRANTED"
    DENIED = "DENIED"
    DEFERRED = "DEFERRED"


@dataclass(frozen=True)
class AllocationOutcome:
    candidate: Candidate
    outcome_kind: OutcomeKind
    reason_code: str | None
    next_eligible_at: datetime | None
    grant: Grant | None
    fact_unavailable_reason_codes: tuple[str, ...]
    score: scoring.ScoreBreakdown | None
    # For DEFERRED only: the candidate to re-queue, with windows_deferred
    # incremented and window_id/proposed_send_after advanced.
    rescheduled_candidate: Candidate | None
    # For GRANTED only: the incentive actually reserved, in bps — may be
    # below candidate.request.requested_max_incentive_bps after a margin
    # downgrade (Design Lock §8).
    effective_incentive_bps: int | None = None


def deferred_or_denied(
    candidate: Candidate,
    reason_code: str,
    next_eligible_at: datetime,
    ledger: Any,  # duck-typed: only .mark_terminally_denied(risk_id) is called
    fact_unavailable_reason_codes: tuple[str, ...] = (),
    score: "scoring.ScoreBreakdown | None" = None,
) -> AllocationOutcome:
    """Applies the deferral-exhaustion rule (Design Lock §7): a candidate
    that would exceed MAX_DEFERRAL_WINDOWS is DENIED (terminal) instead
    of DEFERRED, regardless of WHY it was about to be deferred — a
    hard-policy defer (quiet hours, contact cap, an interlock) and an
    allocator competitive loss (lost to a higher expected_net) age
    identically.

    `score` — Phase 5 U-3 (data-threading only, no scoring change): the
    ALREADY-COMPUTED `ScoreBreakdown` for callers that reached scoring
    before calling this (sampark.allocator.greedy's competitive-loss
    path). Defaults to `None`, unchanged, for
    sampark.mediation.hard_filter's call site — a hard-policy
    INADMISSIBLE candidate never reaches scoring at all, so attaching a
    score there would be fabricating one, not threading a real one."""
    if candidate.windows_deferred + 1 >= MAX_DEFERRAL_WINDOWS:
        ledger.mark_terminally_denied(candidate.risk_item.risk_id)
        return AllocationOutcome(
            candidate=candidate,
            outcome_kind=OutcomeKind.DENIED,
            reason_code=DEFERRAL_EXHAUSTED,
            next_eligible_at=None,
            grant=None,
            fact_unavailable_reason_codes=fact_unavailable_reason_codes,
            score=score,
            rescheduled_candidate=None,
        )
    aged = candidate.aged()
    rescheduled = aged.rescheduled(window_id_for(next_eligible_at), next_eligible_at)
    return AllocationOutcome(
        candidate=candidate,
        outcome_kind=OutcomeKind.DEFERRED,
        reason_code=reason_code,
        next_eligible_at=next_eligible_at,
        grant=None,
        fact_unavailable_reason_codes=fact_unavailable_reason_codes,
        score=score,
        rescheduled_candidate=rescheduled,
    )
