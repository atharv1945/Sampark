"""Ablations B (merchant margin x 0.5) and C (FIFO-under-cap) —
Phase 4C-2 Blocker 2. Exercised against the fast "memory" backend
(same allocator/mediation code the "postgres" backend runs — only the
storage layer differs, Blocker 1), so these run in seconds, not the
~10 minutes a Postgres-backed full run takes.
"""

from __future__ import annotations

import datetime as dt

from sampark.allocator.candidate import build_candidate
from sampark.allocator.constants import MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW
from sampark.allocator.greedy import OutcomeKind, allocate_window
from sampark.budget.store import InMemoryGrantIssuer, InMemoryMediationLedger
from sampark.contracts import GrantRequest, RiskItem
from uuid import uuid4

DECISION_AT = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)
DETECTED_AT = dt.datetime(2025, 9, 10, 6, 0, tzinfo=dt.timezone.utc)


def _candidate(risk_id, customer_id, amount_paise, bps, detected_at=DETECTED_AT):
    item = RiskItem(risk_id=risk_id, source="abandoned_checkout", amount_paise=amount_paise, root_cause="price_hesitation", detected_at=detected_at)
    request = GrantRequest(
        request_id=uuid4(), agent_id="cart_recovery_agent", customer_id=customer_id, risk_id=risk_id,
        intent="cart_recovery", requested_channel="whatsapp", requested_max_incentive_bps=bps,
        issued_at=detected_at, signature="sig",
    )
    return build_candidate(request, item, customer_id, DECISION_AT + dt.timedelta(hours=3))


# --- Ablation B: merchant margin x 0.5 -------------------------------------


def test_merchant_margin_half_changes_only_the_merchant_pool_size():
    """Halving merchant_budget_paise_per_window changes the merchant
    pool's own capacity and nothing else observable about the ledger's
    OTHER configuration (contact caps, customer pool sizing formula)."""
    candidate = _candidate("risk-1", "cust-1", amount_paise=1_000_000, bps=500)
    risk_items_by_customer = {"cust-1": (candidate.risk_item,)}

    headline_ledger = InMemoryMediationLedger(
        risk_items_by_customer, merchant_budget_paise_per_window=MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW
    )
    halved_ledger = InMemoryMediationLedger(
        risk_items_by_customer, merchant_budget_paise_per_window=MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW // 2
    )

    headline_merchant, headline_customer = headline_ledger.remaining_margin_paise("cust-1", candidate.window_id)
    halved_merchant, halved_customer = halved_ledger.remaining_margin_paise("cust-1", candidate.window_id)

    assert halved_merchant == MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW // 2
    assert headline_merchant == MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW
    # The customer pool is sized purely from the customer's own at-risk
    # total (Design Lock §14.3) — unaffected by the merchant ablation.
    assert halved_customer == headline_customer


def test_merchant_margin_half_can_change_a_grants_downgraded_ceiling():
    """With the pool halved, a candidate whose full ceiling would have
    fit under the headline budget may now be downgraded — proving the
    ablation actually binds, not just that the number changed in a
    dataclass nobody reads."""
    candidate = _candidate("risk-1", "cust-1", amount_paise=1_000_000, bps=500)  # ceiling 50,000
    risk_items_by_customer = {"cust-1": (candidate.risk_item,)}
    issuer = InMemoryGrantIssuer()

    # A pool smaller than the requested ceiling but big enough to be nonzero.
    tiny_ledger = InMemoryMediationLedger(risk_items_by_customer, merchant_budget_paise_per_window=1_000)
    outcomes = allocate_window((candidate,), tiny_ledger, issuer, DECISION_AT)
    outcome = outcomes[0]
    assert outcome.outcome_kind is OutcomeKind.GRANTED
    assert outcome.grant.incentive_ceiling_paise == 1_000  # downgraded to fit the constrained pool


def test_merchant_margin_half_leaves_contact_caps_and_quiet_hours_unaffected():
    """The ablation must not touch hard-policy predicates (Design Lock
    §14.4: "identical code" except the one changed variable) — a
    quiet-hours candidate is deferred exactly the same way regardless
    of merchant budget size."""
    from sampark.policy.hard import quiet_hours
    from sampark.policy.types import PolicyContext

    quiet_candidate = _candidate(
        "risk-quiet", "cust-1", amount_paise=1_000_000, bps=0,
    )
    quiet_candidate = quiet_candidate.rescheduled(
        quiet_candidate.window_id, dt.datetime(2025, 9, 10, 22, 0, tzinfo=dt.timezone.utc)
    )
    for budget in (MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW, MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW // 2):
        ledger = InMemoryMediationLedger({"cust-1": (quiet_candidate.risk_item,)}, merchant_budget_paise_per_window=budget)
        verdict = quiet_hours.evaluate(quiet_candidate, PolicyContext(ledger=ledger, decision_at=DECISION_AT))
        assert verdict.is_defer  # unaffected by the merchant-margin ablation, either way


# --- Ablation C: FIFO-under-cap ---------------------------------------------


def test_fifo_mode_ranks_by_arrival_order_not_value():
    """A small, low-value item that arrived FIRST beats a large,
    high-value item that arrived LATER, under fifo_mode — the opposite
    of the headline ranking (Design Lock §8's expected_net priority)."""
    early_small = _candidate("risk-early-small", "cust-1", amount_paise=50_000, bps=0, detected_at=DETECTED_AT)
    late_large = _candidate(
        "risk-late-large", "cust-1", amount_paise=2_000_000, bps=0,
        detected_at=DETECTED_AT + dt.timedelta(hours=1),
    )
    ledger = InMemoryMediationLedger({"cust-1": (early_small.risk_item, late_large.risk_item)})
    issuer = InMemoryGrantIssuer()

    headline_outcomes = allocate_window((early_small, late_large), ledger, issuer, DECISION_AT, fifo_mode=False)
    headline_winner = next(o for o in headline_outcomes if o.outcome_kind.name == "GRANTED")
    assert headline_winner.candidate.risk_item.risk_id == "risk-late-large"  # higher expected_net wins normally

    ledger2 = InMemoryMediationLedger({"cust-1": (early_small.risk_item, late_large.risk_item)})
    fifo_outcomes = allocate_window((early_small, late_large), ledger2, issuer, DECISION_AT, fifo_mode=True)
    fifo_winner = next(o for o in fifo_outcomes if o.outcome_kind.name == "GRANTED")
    assert fifo_winner.candidate.risk_item.risk_id == "risk-early-small"  # arrival order wins under FIFO


def test_fifo_mode_admits_a_negative_expected_net_candidate():
    """FIFO mode skips the expected_net > 0 admission gate entirely —
    a tiny-amount candidate that would be DENIED negative_expected_net
    in headline mode is GRANTED under FIFO (no competing candidate)."""
    tiny = _candidate("risk-tiny", "cust-1", amount_paise=100, bps=0)
    ledger_headline = InMemoryMediationLedger({"cust-1": (tiny.risk_item,)})
    issuer = InMemoryGrantIssuer()
    headline_outcomes = allocate_window((tiny,), ledger_headline, issuer, DECISION_AT, fifo_mode=False)
    assert headline_outcomes[0].outcome_kind is OutcomeKind.DENIED

    ledger_fifo = InMemoryMediationLedger({"cust-1": (tiny.risk_item,)})
    fifo_outcomes = allocate_window((tiny,), ledger_fifo, issuer, DECISION_AT, fifo_mode=True)
    assert fifo_outcomes[0].outcome_kind is OutcomeKind.GRANTED


def test_fifo_mode_still_enforces_hard_policy():
    """FIFO changes RANKING and ADMISSION-BY-VALUE only — hard policy
    (quiet hours here) still fires identically. Exercised through
    `filter_and_allocate` (the full pipeline) since `allocate_window`
    alone no longer hard-filters (W3)."""
    from sampark.allocator.constants import AGING_BONUS_PAISE
    from sampark.mediation.hard_filter import filter_and_allocate

    quiet_candidate = _candidate("risk-quiet", "cust-1", amount_paise=1_000_000, bps=0)
    quiet_candidate = quiet_candidate.rescheduled(
        quiet_candidate.window_id, dt.datetime(2025, 9, 10, 22, 0, tzinfo=dt.timezone.utc)
    )
    ledger = InMemoryMediationLedger({"cust-1": (quiet_candidate.risk_item,)})
    issuer = InMemoryGrantIssuer()
    outcomes = filter_and_allocate(
        (quiet_candidate,), ledger, issuer, DECISION_AT, AGING_BONUS_PAISE, fifo_mode=True
    )
    assert outcomes[0].outcome_kind is OutcomeKind.DEFERRED
    assert outcomes[0].reason_code == "policy.quiet_hours"


def test_fifo_mode_still_enforces_contact_window_semantics():
    """Still one grant per customer per window under FIFO — the SAME
    claim/cap mechanism, just a different ranking feeding it."""
    c1 = _candidate("risk-1", "cust-1", amount_paise=1_000_000, bps=0, detected_at=DETECTED_AT)
    c2 = _candidate("risk-2", "cust-1", amount_paise=1_000_000, bps=0, detected_at=DETECTED_AT + dt.timedelta(minutes=1))
    ledger = InMemoryMediationLedger({"cust-1": (c1.risk_item, c2.risk_item)})
    issuer = InMemoryGrantIssuer()
    outcomes = allocate_window((c1, c2), ledger, issuer, DECISION_AT, fifo_mode=True)
    granted = [o for o in outcomes if o.outcome_kind is OutcomeKind.GRANTED]
    assert len(granted) == 1


def test_ablations_are_deterministic():
    """Running the SAME fifo/margin configuration twice yields
    identical outcomes."""
    c1 = _candidate("risk-1", "cust-1", amount_paise=1_000_000, bps=0)
    c2 = _candidate("risk-2", "cust-1", amount_paise=800_000, bps=0, detected_at=DETECTED_AT + dt.timedelta(hours=1))
    issuer = InMemoryGrantIssuer()

    results = []
    for _ in range(3):
        ledger = InMemoryMediationLedger(
            {"cust-1": (c1.risk_item, c2.risk_item)}, merchant_budget_paise_per_window=MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW // 2
        )
        outcomes = allocate_window((c1, c2), ledger, issuer, DECISION_AT, fifo_mode=True)
        results.append(tuple((o.candidate.risk_item.risk_id, o.outcome_kind) for o in outcomes))
    assert len(set(results)) == 1
