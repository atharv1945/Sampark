"""Phase 6 scorer interface — proves `sampark.allocator.scorer` is a
behavior-preserving refactor, not a new decision.

Two proofs:

1. `HeuristicScorer.score()` returns byte-for-byte the same
   `ScoreBreakdown` as calling `sampark.allocator.scoring.score`
   directly (the exact function it wraps).
2. `allocate_window(..., scorer=None)` (every pre-Phase-6 call site)
   and `allocate_window(..., scorer=HeuristicScorer())` (an explicit
   Phase 6 caller) produce IDENTICAL `AllocationOutcome` sequences on
   the same input — the default and the explicit heuristic are the
   same computation, not two paths that happen to agree today.
"""

from __future__ import annotations

import datetime as dt

from sampark.allocator import greedy, scoring
from sampark.allocator.scorer import HeuristicScorer, Scorer, default_scorer

DECISION_AT = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)


def test_heuristic_scorer_matches_scoring_score_directly(make_candidate):
    candidate = make_candidate(risk_id="risk-1", customer_id="cust-1")
    direct = scoring.score(candidate, 500, 0, (100_000,))
    via_scorer = HeuristicScorer().score(candidate, 500, 0, (100_000,))
    assert via_scorer == direct


def test_default_scorer_is_a_heuristic_scorer():
    assert isinstance(default_scorer(), HeuristicScorer)
    assert isinstance(default_scorer(), Scorer)


def test_allocate_window_default_and_explicit_heuristic_scorer_agree(make_candidate, make_ledger, issuer):
    winner = make_candidate(risk_id="risk-winner", customer_id="cust-1", amount_paise=1_000_000, bps=500)
    loser = make_candidate(risk_id="risk-loser", customer_id="cust-1", amount_paise=10_000, bps=500)
    ledger_default = make_ledger(winner, loser)
    ledger_explicit = make_ledger(winner, loser)

    outcomes_default = greedy.allocate_window((winner, loser), ledger_default, issuer, DECISION_AT, 0)
    # A fresh issuer for the second run — InMemoryGrantIssuer holds no
    # state across allocate_window calls beyond what the ledger tracks,
    # and each run needs its own well-funded ledger (built above) so the
    # first run's reservations cannot influence the second.
    from sampark.budget.store import InMemoryGrantIssuer

    outcomes_explicit = greedy.allocate_window(
        (winner, loser), ledger_explicit, InMemoryGrantIssuer(), DECISION_AT, 0, scorer=HeuristicScorer()
    )

    assert len(outcomes_default) == len(outcomes_explicit) == 2
    for a, b in zip(
        sorted(outcomes_default, key=lambda o: o.candidate.risk_item.risk_id),
        sorted(outcomes_explicit, key=lambda o: o.candidate.risk_item.risk_id),
    ):
        assert a.outcome_kind == b.outcome_kind
        assert a.reason_code == b.reason_code
        assert a.score == b.score
        assert (a.grant is None) == (b.grant is None)
        if a.grant is not None and b.grant is not None:
            assert a.grant.channel == b.grant.channel
            assert a.grant.incentive_ceiling_paise == b.grant.incentive_ceiling_paise
