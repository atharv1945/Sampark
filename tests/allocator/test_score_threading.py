"""U-3 — proves `AllocationOutcome.score` is the ALREADY-COMPUTED
`ScoreBreakdown` (data-threading), not a value recomputed for the
outcome/audit layer.

No database. Exercises the REAL `sampark.allocator.greedy.allocate_window`
(the exact function whose `for score_val, candidate in admitted:` /
`score_breakdown_by_risk_id` bookkeeping U-3 touches) — not a mock of the
allocator, only an instrumented `scoring.score` so this test can count
and identify calls without changing what any call computes or returns.
"""

from __future__ import annotations

import datetime as dt

from sampark.allocator import greedy, scoring
from sampark.allocator.outcomes import OutcomeKind
from sampark.allocator.reason_codes import LOST_TO_HIGHER_EXPECTED_NET

DECISION_AT = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)


def _instrument_score(monkeypatch):
    """Wraps the REAL scoring.score with a call log — every call still
    does the real, unmodified computation; nothing about scoring
    behavior changes. Patches the `scoring` module's own attribute (not
    greedy's import), which is exactly what greedy.py reads at call time
    (`from sampark.allocator import scoring` then `scoring.score(...)`),
    so this observes every call greedy.py actually makes. Returns the
    list of (candidate, ScoreBreakdown) pairs, in call order."""
    calls: list[tuple] = []
    real_score = scoring.score

    def wrapper(candidate, *args, **kwargs):
        result = real_score(candidate, *args, **kwargs)
        calls.append((candidate, result))
        return result

    monkeypatch.setattr(scoring, "score", wrapper)
    return calls


def test_winner_score_is_the_exact_object_computed_at_admission(monkeypatch, make_candidate, make_ledger, issuer):
    # A single, well-funded, non-competing candidate: admission computes
    # its score once; nothing downstream (margin downgrade, a rival
    # candidate) gives greedy.py any reason to score it again. Asserting
    # object identity (`is`, not `==`) is the strongest available proof
    # of "threaded, not recomputed" for a pure function whose
    # equal-value outputs would otherwise be indistinguishable either
    # way — a second, independent scoring.score call on identical inputs
    # would return an equal-but-DISTINCT ScoreBreakdown object.
    calls = _instrument_score(monkeypatch)
    candidate = make_candidate(risk_id="risk-solo", customer_id="cust-solo")
    ledger = make_ledger(candidate)

    outcomes = greedy.allocate_window((candidate,), ledger, issuer, DECISION_AT, 0)

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.outcome_kind is OutcomeKind.GRANTED

    solo_calls = [breakdown for c, breakdown in calls if c.risk_item.risk_id == "risk-solo"]
    assert len(solo_calls) == 1, f"expected exactly one scoring.score call for the winner, got {len(solo_calls)}"

    assert outcome.score is not None
    assert outcome.score is solo_calls[0], (
        "outcome.score must be the SAME object scoring.score returned at admission, "
        "not a value recomputed for the outcome"
    )


def test_competitive_losers_score_is_threaded_not_recomputed(monkeypatch, make_candidate, make_ledger, issuer):
    # A second candidate for the SAME customer, guaranteed to lose the
    # allocation round to a higher expected_net rival — proves U-3's
    # other half: sampark.allocator.outcomes.deferred_or_denied's new
    # `score` parameter, not just the GRANTED path.
    # Amounts mirror tests/allocator/test_greedy.py's own
    # LOST_TO_HIGHER_EXPECTED_NET fixture — both well above the
    # ~319,000-paise breakeven where the fatigue term would otherwise
    # push the smaller one negative at ADMISSION (a NEGATIVE_EXPECTED_NET
    # denial, not the competitive loss this test needs).
    winner = make_candidate(risk_id="risk-winner", customer_id="cust-1", amount_paise=1_000_000, bps=0)
    loser = make_candidate(risk_id="risk-loser", customer_id="cust-1", amount_paise=600_000, bps=0)
    ledger = make_ledger(winner, loser)

    calls = _instrument_score(monkeypatch)
    outcomes = greedy.allocate_window((winner, loser), ledger, issuer, DECISION_AT, 0)

    by_risk_id = {o.candidate.risk_item.risk_id: o for o in outcomes}
    assert by_risk_id["risk-winner"].outcome_kind is OutcomeKind.GRANTED
    loser_outcome = by_risk_id["risk-loser"]
    assert loser_outcome.outcome_kind is OutcomeKind.DEFERRED
    assert loser_outcome.reason_code == LOST_TO_HIGHER_EXPECTED_NET

    loser_calls = [breakdown for c, breakdown in calls if c.risk_item.risk_id == "risk-loser"]
    assert len(loser_calls) == 1, f"expected the loser to be scored exactly once (at admission), got {len(loser_calls)}"

    assert loser_outcome.score is not None
    assert loser_outcome.score.expected_net_paise > 0  # it lost on RANKING, not admission
    assert loser_outcome.score is loser_calls[0], "loser_outcome.score must be the exact admission-time object"


def test_negative_expected_net_denial_score_is_also_threaded(monkeypatch, make_candidate, make_ledger, issuer):
    # Phase 4's ALREADY-CORRECT path (score attached for NEGATIVE_EXPECTED_NET
    # denials predates U-3) — re-asserted here as object identity, not
    # just presence, so all three AllocationOutcome-producing branches in
    # greedy.py are covered by the same "threaded, not recomputed" proof.
    tiny = make_candidate(risk_id="risk-tiny", customer_id="cust-tiny", amount_paise=100, bps=0)
    ledger = make_ledger(tiny)

    calls = _instrument_score(monkeypatch)
    outcomes = greedy.allocate_window((tiny,), ledger, issuer, DECISION_AT, 0)

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.outcome_kind is OutcomeKind.DENIED
    assert len(calls) == 1
    assert outcome.score is calls[0][1]


def test_hard_filter_denials_still_carry_no_score():
    # The other side of U-3's contract: deferred_or_denied's new `score`
    # parameter defaults to None, so sampark.mediation.hard_filter's call
    # site (a candidate that never reached scoring at all) is UNCHANGED —
    # U-3 must never fabricate a score for a candidate that was never
    # scored. Already covered behaviorally by
    # tests/allocator/test_greedy.py::test_quiet_hour_candidate_never_reaches_scoring;
    # re-asserted here as the explicit U-3 contract on the function signature.
    import inspect

    from sampark.allocator.outcomes import deferred_or_denied

    assert inspect.signature(deferred_or_denied).parameters["score"].default is None
