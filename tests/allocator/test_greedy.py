"""Budgeted greedy allocator — Design Lock §8."""

from __future__ import annotations

import datetime as dt

from sampark.allocator.constants import AGING_BONUS_PAISE, MAX_DEFERRAL_WINDOWS
from sampark.allocator.greedy import OutcomeKind, allocate_window
from sampark.allocator.reason_codes import DEFERRAL_EXHAUSTED, LOST_TO_HIGHER_EXPECTED_NET, NEGATIVE_EXPECTED_NET
from sampark.budget.windows import window_id_for

DECISION_AT = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)
SEND_AFTER = dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc)


def _by_risk_id(outcomes, risk_id):
    matches = [o for o in outcomes if o.candidate.risk_item.risk_id == risk_id]
    assert len(matches) == 1
    return matches[0]


def test_higher_expected_net_wins_between_two_competing_candidates(make_candidate, make_ledger, issuer):
    # Both amounts are chosen well above the ~319,000-paise breakeven
    # where the forward-looking fatigue term (calibrated against the
    # real dataset's ~387,607-paise mean amount) would otherwise push
    # expected_net negative and the smaller one would never even be
    # ADMITTED (see test_negative_expected_net_is_denied_and_never_admitted).
    high_value = make_candidate(risk_id="risk-high", customer_id="cust-1", amount_paise=1_000_000, bps=0)
    low_value = make_candidate(risk_id="risk-low", customer_id="cust-1", amount_paise=600_000, bps=0)
    ledger = make_ledger(high_value, low_value)

    outcomes = allocate_window((high_value, low_value), ledger, issuer, DECISION_AT)

    winner = _by_risk_id(outcomes, "risk-high")
    loser = _by_risk_id(outcomes, "risk-low")
    assert winner.outcome_kind is OutcomeKind.GRANTED
    assert loser.outcome_kind is OutcomeKind.DEFERRED
    assert loser.reason_code == LOST_TO_HIGHER_EXPECTED_NET
    assert loser.rescheduled_candidate is not None
    assert loser.rescheduled_candidate.windows_deferred == 1


def test_tie_break_prefers_larger_amount_then_earlier_detected_at(make_candidate, make_ledger, issuer):
    # Equal amount_paise and bps -> equal expected_net -> tie-break by
    # (amount desc [equal], detected_at asc).
    earlier = make_candidate(
        risk_id="risk-earlier", customer_id="cust-1", amount_paise=500_000, bps=0,
        detected_at=dt.datetime(2025, 9, 9, 0, 0, tzinfo=dt.timezone.utc),
    )
    later = make_candidate(
        risk_id="risk-later", customer_id="cust-1", amount_paise=500_000, bps=0,
        detected_at=dt.datetime(2025, 9, 10, 0, 0, tzinfo=dt.timezone.utc),
    )
    ledger = make_ledger(earlier, later)

    outcomes = allocate_window((later, earlier), ledger, issuer, DECISION_AT)  # deliberately reversed input order

    winner = _by_risk_id(outcomes, "risk-earlier")
    assert winner.outcome_kind is OutcomeKind.GRANTED


def test_allocation_is_deterministic_across_repeated_calls(make_candidate, make_ledger):
    from sampark.budget.store import InMemoryGrantIssuer

    c1 = make_candidate(risk_id="risk-1", customer_id="cust-1", amount_paise=300_000, bps=0)
    c2 = make_candidate(risk_id="risk-2", customer_id="cust-1", amount_paise=300_000, bps=0)

    results = []
    for _ in range(3):
        ledger = make_ledger(c1, c2)
        outcomes = allocate_window((c1, c2), ledger, InMemoryGrantIssuer(), DECISION_AT)
        results.append(tuple((o.candidate.risk_item.risk_id, o.outcome_kind) for o in outcomes))
    assert len(set(results)) == 1


def test_negative_expected_net_is_denied_and_never_admitted(make_candidate, make_ledger, issuer):
    # The forward-looking fatigue term (Design Lock §6.4) is calibrated
    # against the real dataset's ~387,607-paise mean amount and a
    # ~30-day horizon — at a tiny amount, gross recovery value cannot
    # cover it, giving a genuinely negative expected_net.
    candidate = make_candidate(risk_id="risk-1", customer_id="cust-1", amount_paise=100, bps=0)
    ledger = make_ledger(candidate)
    outcomes = allocate_window((candidate,), ledger, issuer, DECISION_AT)
    outcome = outcomes[0]
    assert outcome.outcome_kind is OutcomeKind.DENIED
    assert outcome.reason_code == NEGATIVE_EXPECTED_NET


def test_quiet_hour_candidate_never_reaches_scoring(make_candidate, make_ledger, issuer):
    """A hard-DEFERRED candidate must never be scored or issued — its
    outcome carries no ScoreBreakdown and no Grant.

    Exercised through `filter_and_allocate` (the full hard-filter ->
    allocator pipeline), not `allocate_window` directly — since W3's
    refactor, `allocate_window` no longer hard-filters at all; it
    assumes its input is already hard-admissible (see
    tests/allocator/test_structural_boundaries.py for the structural
    proof that a hard-INADMISSIBLE candidate never reaches it)."""
    from sampark.mediation.hard_filter import filter_and_allocate

    candidate = make_candidate(
        risk_id="risk-1", customer_id="cust-1",
        proposed_send_after=dt.datetime(2025, 9, 10, 22, 0, tzinfo=dt.timezone.utc),  # quiet hours
    )
    ledger = make_ledger(candidate)
    outcomes = filter_and_allocate((candidate,), ledger, issuer, DECISION_AT, AGING_BONUS_PAISE)
    outcome = outcomes[0]
    assert outcome.outcome_kind is OutcomeKind.DEFERRED
    assert outcome.score is None
    assert outcome.grant is None


def test_deferral_exhaustion_denies_instead_of_deferring_again(make_candidate, make_ledger, issuer):
    winner = make_candidate(risk_id="risk-winner", customer_id="cust-1", amount_paise=2_000_000, bps=0)
    aged_loser = make_candidate(risk_id="risk-loser", customer_id="cust-1", amount_paise=1_000_000, bps=0)
    for _ in range(MAX_DEFERRAL_WINDOWS - 1):
        aged_loser = aged_loser.aged()
    assert aged_loser.windows_deferred == MAX_DEFERRAL_WINDOWS - 1

    ledger = make_ledger(winner, aged_loser)
    outcomes = allocate_window((winner, aged_loser), ledger, issuer, DECISION_AT)

    loser_outcome = _by_risk_id(outcomes, "risk-loser")
    assert loser_outcome.outcome_kind is OutcomeKind.DENIED
    assert loser_outcome.reason_code == DEFERRAL_EXHAUSTED
    assert loser_outcome.next_eligible_at is None
    assert loser_outcome.rescheduled_candidate is None


def test_margin_shortfall_downgrades_the_winning_grant_but_still_succeeds(make_candidate, issuer):
    """A starved merchant pool downgrades the winner's incentive ceiling
    below what it requested, but the grant still succeeds: expected_net
    is NON-INCREASING in incentive_bps under Design Lock §6.2's formula
    (p_hat does not depend on bps — the heuristic deliberately has no
    access to HiddenResponseProfile's true conversion response to
    incentive), so a downgrade can only ever raise or preserve
    expected_net, never push an already-admitted candidate below zero.
    The Design Lock §8 "abandon if downgraded score <= 0" branch
    (sampark/allocator/greedy.py) is therefore unreachable under this
    scoring model — kept as defensive code for a future (e.g. Phase 6
    ML-based) scoring function where incentive genuinely affects
    conversion probability, and documented as such rather than tested
    for a behaviour that cannot occur here."""
    from sampark.budget.store import InMemoryMediationLedger

    candidate = make_candidate(risk_id="risk-1", customer_id="cust-1", amount_paise=1_000_000, bps=500)
    risk_items_by_customer = {"cust-1": (candidate.risk_item,)}
    ledger = InMemoryMediationLedger(risk_items_by_customer, merchant_budget_paise_per_window=100)  # starved pool

    outcomes = allocate_window((candidate,), ledger, issuer, DECISION_AT)

    outcome = outcomes[0]
    assert outcome.outcome_kind is OutcomeKind.GRANTED
    assert outcome.grant.incentive_ceiling_paise == 100  # downgraded to fit the starved pool
    assert outcome.effective_incentive_bps is not None
    assert outcome.effective_incentive_bps < 500  # downgraded from the requested bps
