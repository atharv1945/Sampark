"""InMemoryGrantIssuer — protocol conformance for the callable contract
issue_grant(conn, candidate, effective_incentive_bps, decision_at)
expects, Design Lock §11. This is a reference/test double, NOT a claim
about concurrent correctness (see sampark/budget/store.py's docstring)
— that guarantee is the owner-authored SERIALIZABLE transaction's job.

Every test funds the CUSTOMER margin pool generously by default (a
large placeholder RiskItem per customer — the customer pool is sized
from a customer's total known at-risk amount, Design Lock §14.3) so
that only the MERCHANT pool is ever the constraint under test, unless
a test is specifically about the customer pool.
"""

from __future__ import annotations

import datetime as dt

from sampark.allocator.reason_codes import (
    CONTACT_CAP_24H,
    CONTACT_CAP_7D,
    CONTACT_SLOT_TAKEN,
    MERCHANT_MARGIN_EXHAUSTED,
)
from sampark.budget.contact import CAPACITY_CONSUMING_STATES
from sampark.budget.store import BudgetDenial, GrantIssued, InMemoryGrantIssuer, InMemoryMediationLedger
from sampark.contracts import GrantState, RiskItem
from sampark.mediation import lifecycle

DECISION_AT = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)
_WELL_FUNDED_AMOUNT_PAISE = 100_000_000  # customer pool = 500bps * 1e8 / 1e4 = 5,000,000 paise


def _well_funded_risk_items_by_customer(*customer_ids: str) -> dict[str, tuple[RiskItem, ...]]:
    return {
        customer_id: (
            RiskItem(
                risk_id=f"placeholder-{customer_id}",
                source="abandoned_checkout",
                amount_paise=_WELL_FUNDED_AMOUNT_PAISE,
                root_cause="price_hesitation",
                detected_at=DECISION_AT,
            ),
        )
        for customer_id in customer_ids
    }


def _ledger(*customer_ids: str, merchant_budget_paise_per_window: int = 1_000_000) -> InMemoryMediationLedger:
    customer_ids = customer_ids or ("cust-1",)
    return InMemoryMediationLedger(
        _well_funded_risk_items_by_customer(*customer_ids),
        merchant_budget_paise_per_window=merchant_budget_paise_per_window,
    )


def test_first_issuance_grants(make_candidate):
    ledger = _ledger()
    issuer = InMemoryGrantIssuer()
    candidate = make_candidate()
    result = issuer.issue_grant(ledger, candidate, 500, DECISION_AT)
    assert isinstance(result, GrantIssued)
    assert result.grant.state is GrantState.RESERVED
    assert result.grant.channel == "whatsapp"


def test_idempotent_reissue_returns_same_grant(make_candidate):
    ledger = _ledger()
    issuer = InMemoryGrantIssuer()
    candidate = make_candidate()
    first = issuer.issue_grant(ledger, candidate, 500, DECISION_AT)
    second = issuer.issue_grant(ledger, candidate, 500, DECISION_AT)
    assert isinstance(first, GrantIssued) and isinstance(second, GrantIssued)
    assert first.grant.grant_id == second.grant.grant_id
    # No second reservation was made.
    merchant_remaining, _ = ledger.remaining_margin_paise("cust-1", candidate.window_id)
    assert merchant_remaining == 1_000_000 - first.grant.incentive_ceiling_paise


def test_second_candidate_same_customer_window_is_denied(make_candidate):
    """With CONTACT_CAP_24H == 1, the rolling-24h check (step 4) always
    catches a second same-window, same-customer candidate before the
    claim-uniqueness write (step 6) is even reached — Design Lock §3.2:
    "rolling-24h = 1 subsumes the per-window rule". The claim's own
    guarantee is exercised independently by
    test_rolled_back_claim_frees_the_window_for_a_new_grant, and would
    bind CONTACT_SLOT_TAKEN specifically if the caps were ever loosened
    above 1 (an ablation, or a genuine concurrent race the real
    Postgres transaction — not this single-threaded reference — must
    resolve)."""
    ledger = _ledger()
    issuer = InMemoryGrantIssuer()
    c1 = make_candidate(risk_id="risk-1")
    c2 = make_candidate(risk_id="risk-2")
    r1 = issuer.issue_grant(ledger, c1, 500, DECISION_AT)
    r2 = issuer.issue_grant(ledger, c2, 500, DECISION_AT)
    assert isinstance(r1, GrantIssued)
    assert isinstance(r2, BudgetDenial)
    assert r2.reason_code == CONTACT_CAP_24H
    assert r2.next_eligible_at is not None


def test_claim_uniqueness_independently_blocks_a_second_active_claim(make_candidate, monkeypatch):
    """Directly exercises the claim-uniqueness write (step 6) as the
    BINDING constraint, by loosening the 24h cap so it no longer fires
    first — an ablation-shaped scenario (Design Lock §14.4's FIFO/cap
    variants loosen caps too), proving the claim mechanism itself
    independently of which check happens to win in the shipped
    CONTACT_CAP_24H == 1 configuration."""
    import sampark.budget.store as store_module

    monkeypatch.setattr(store_module, "CAP_24H_LIMIT", 100)
    ledger = _ledger()
    issuer = InMemoryGrantIssuer()
    c1 = make_candidate(risk_id="risk-1")
    c2 = make_candidate(risk_id="risk-2")
    r1 = issuer.issue_grant(ledger, c1, 500, DECISION_AT)
    r2 = issuer.issue_grant(ledger, c2, 500, DECISION_AT)
    assert isinstance(r1, GrantIssued)
    assert isinstance(r2, BudgetDenial)
    assert r2.reason_code == CONTACT_SLOT_TAKEN
    assert r2.next_eligible_at is not None


def test_rolled_back_claim_frees_the_window_for_a_new_grant(make_candidate):
    """The behavioural test the Design Lock asks for instead of parsing
    DDL that does not exist yet: a ROLLED_BACK claim frees its window."""
    ledger = _ledger()
    issuer = InMemoryGrantIssuer()
    c1 = make_candidate(risk_id="risk-1")
    r1 = issuer.issue_grant(ledger, c1, 500, DECISION_AT)
    assert isinstance(r1, GrantIssued)

    lifecycle.rollback(ledger, r1.grant.grant_id, at=DECISION_AT)

    c2 = make_candidate(risk_id="risk-2")
    r2 = issuer.issue_grant(ledger, c2, 500, DECISION_AT)
    assert isinstance(r2, GrantIssued), "a rolled-back claim must free the (customer, window) slot"


def test_contact_cap_24h_denies_second_grant_within_24h(make_candidate):
    ledger = _ledger()
    issuer = InMemoryGrantIssuer()
    c1 = make_candidate(risk_id="risk-1", proposed_send_after=dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc))
    r1 = issuer.issue_grant(ledger, c1, 500, dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc))
    assert isinstance(r1, GrantIssued)

    # Different window_id so the claim-uniqueness path doesn't fire first.
    c2 = make_candidate(risk_id="risk-2", proposed_send_after=dt.datetime(2025, 9, 11, 9, 0, tzinfo=dt.timezone.utc))
    r2 = issuer.issue_grant(ledger, c2, 500, dt.datetime(2025, 9, 11, 8, 0, tzinfo=dt.timezone.utc))  # 23h later
    assert isinstance(r2, BudgetDenial)
    assert r2.reason_code == CONTACT_CAP_24H


def test_contact_cap_7d_denies_third_grant_within_7_days(make_candidate):
    ledger = _ledger()
    issuer = InMemoryGrantIssuer()
    base = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)
    c1 = make_candidate(risk_id="risk-1", proposed_send_after=base)
    r1 = issuer.issue_grant(ledger, c1, 500, base)
    assert isinstance(r1, GrantIssued)

    c2 = make_candidate(risk_id="risk-2", proposed_send_after=base + dt.timedelta(days=2))
    r2 = issuer.issue_grant(ledger, c2, 500, base + dt.timedelta(days=2))
    assert isinstance(r2, GrantIssued)  # 2nd grant OK, cap is 2/7d

    c3 = make_candidate(risk_id="risk-3", proposed_send_after=base + dt.timedelta(days=4))
    r3 = issuer.issue_grant(ledger, c3, 500, base + dt.timedelta(days=4))
    assert isinstance(r3, BudgetDenial)
    assert r3.reason_code == CONTACT_CAP_7D


def test_merchant_margin_shortfall_downgrades_instead_of_denying(make_candidate):
    ledger = _ledger(merchant_budget_paise_per_window=100)  # tiny merchant pool
    issuer = InMemoryGrantIssuer()
    candidate = make_candidate(amount_paise=500_000, bps=500)  # ceiling 25,000 >> 100
    result = issuer.issue_grant(ledger, candidate, 500, DECISION_AT)
    assert isinstance(result, GrantIssued)  # downgraded to fit, not denied outright
    assert result.grant.incentive_ceiling_paise <= 100


def test_merchant_margin_fully_exhausted_denies_outright(make_candidate):
    ledger = _ledger("cust-1", "cust-2", merchant_budget_paise_per_window=100)
    issuer = InMemoryGrantIssuer()
    c1 = make_candidate(risk_id="risk-1", proposed_send_after=dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc))
    r1 = issuer.issue_grant(ledger, c1, 500, dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc))
    assert isinstance(r1, GrantIssued)
    assert r1.grant.incentive_ceiling_paise == 100  # pool fully consumed

    c2 = make_candidate(
        customer_id="cust-2", risk_id="risk-2",
        proposed_send_after=dt.datetime(2025, 9, 10, 10, 0, tzinfo=dt.timezone.utc),
    )
    r2 = issuer.issue_grant(ledger, c2, 500, dt.datetime(2025, 9, 10, 10, 0, tzinfo=dt.timezone.utc))
    assert isinstance(r2, BudgetDenial)
    assert r2.reason_code == MERCHANT_MARGIN_EXHAUSTED


def test_downgrade_never_exceeds_requested_ceiling(make_candidate):
    ledger = _ledger(merchant_budget_paise_per_window=1_000_000_000)
    issuer = InMemoryGrantIssuer()
    candidate = make_candidate(amount_paise=500_000, bps=500)  # ceiling 25,000
    result = issuer.issue_grant(ledger, candidate, 500, DECISION_AT)
    assert isinstance(result, GrantIssued)
    assert result.grant.incentive_ceiling_paise == 25_000


def test_capacity_consuming_states_match_active_index_predicate():
    """CAPACITY_CONSUMING_STATES is the single source of truth shared by
    the rolling-count query and (conceptually) the partial unique index."""
    assert CAPACITY_CONSUMING_STATES == frozenset({"RESERVED", "EXECUTING", "CONFIRMED"})
