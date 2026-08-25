"""expected_net invariants — Design Lock §6.3.

I1: the admissible set (expected_net > 0) is downward-closed in n —
NOT unconditional monotonicity, which is false whenever B <= 0 (see the
Design Lock's proof and this file's docstring on that specific test).
I2: higher amount-at-risk never reduces the current-value contribution.
I3: determinism — identical inputs, identical float, every time.
"""

from __future__ import annotations

import datetime as dt

import pytest

from sampark.allocator.candidate import build_candidate
from sampark.allocator.scoring import score
from sampark.contracts import GrantRequest
from uuid import uuid4

from sampark.contracts import RiskItem

DETECTED_AT = dt.datetime(2025, 9, 10, 6, 0, tzinfo=dt.timezone.utc)


def _candidate(amount_paise: int = 500_000, bps: int = 500, source="abandoned_checkout", root_cause="price_hesitation"):
    item = RiskItem(
        risk_id="risk-1", source=source, amount_paise=amount_paise, root_cause=root_cause, detected_at=DETECTED_AT,
    )
    request = GrantRequest(
        request_id=uuid4(), agent_id="cart_recovery_agent", customer_id="cust-1", risk_id="risk-1",
        intent="cart_recovery", requested_channel="whatsapp", requested_max_incentive_bps=bps,
        issued_at=DETECTED_AT, signature="sig",
    )
    return build_candidate(request, item, "cust-1", DETECTED_AT + dt.timedelta(hours=6))


def test_admissible_set_is_downward_closed_in_n():
    """Once expected_net(n) <= 0, it stays <= 0 for every n' > n."""
    candidate = _candidate()
    breakdowns = [score(candidate, candidate.request.requested_max_incentive_bps, n, ()) for n in range(0, 12)]
    seen_non_positive = False
    for b in breakdowns:
        if seen_non_positive:
            assert b.expected_net_paise <= 0, "admissible set must be downward-closed in n"
        if b.expected_net_paise <= 0:
            seen_non_positive = True


def test_more_prior_contacts_never_increases_score_once_the_set_is_downward_closed():
    """The corollary that IS safe to assert unconditionally: within the
    admissible region (expected_net > 0), increasing n never increases
    the score. (Unconditional monotonicity across ALL n, including the
    B <= 0 region, is FALSE by the Design Lock's own proof — not tested
    here, deliberately.)"""
    candidate = _candidate()
    n = 0
    prev = score(candidate, candidate.request.requested_max_incentive_bps, n, ()).expected_net_paise
    while prev > 0 and n < 30:
        n += 1
        current = score(candidate, candidate.request.requested_max_incentive_bps, n, ()).expected_net_paise
        assert current <= prev
        prev = current


def test_higher_amount_never_reduces_current_value_contribution():
    small = _candidate(amount_paise=100_000)
    large = _candidate(amount_paise=1_000_000)
    small_score = score(small, small.request.requested_max_incentive_bps, 0, ())
    large_score = score(large, large.request.requested_max_incentive_bps, 0, ())
    # gross - incentive_ex is the "current-value contribution"; fatigue
    # and channel_cost are amount-independent at n=0 with no other_open.
    small_contribution = small_score.gross_paise - small_score.incentive_expected_paise
    large_contribution = large_score.gross_paise - large_score.incentive_expected_paise
    assert large_contribution > small_contribution


def test_candidate_construction_rejects_100_percent_incentive():
    item = RiskItem(risk_id="risk-1", source="abandoned_checkout", amount_paise=10_000, root_cause="price_hesitation", detected_at=DETECTED_AT)
    request = GrantRequest(
        request_id=uuid4(), agent_id="cart_recovery_agent", customer_id="cust-1", risk_id="risk-1",
        intent="cart_recovery", requested_channel="whatsapp", requested_max_incentive_bps=10_000,
        issued_at=DETECTED_AT, signature="sig",
    )
    with pytest.raises(ValueError):
        build_candidate(request, item, "cust-1", DETECTED_AT)


def test_determinism_identical_inputs_identical_score():
    candidate = _candidate()
    results = [score(candidate, 500, 2, (100_000, 200_000)) for _ in range(5)]
    values = {r.expected_net_paise for r in results}
    assert len(values) == 1


def test_negative_n_rejected():
    candidate = _candidate()
    with pytest.raises(ValueError):
        score(candidate, 500, -1, ())


def test_effective_bps_cannot_exceed_requested_ceiling():
    candidate = _candidate(bps=200)
    with pytest.raises(ValueError):
        score(candidate, 300, 0, ())
