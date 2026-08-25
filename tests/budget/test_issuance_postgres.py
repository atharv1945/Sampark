"""sampark/budget/issuance.py against REAL PostgreSQL — Design Lock §11.

Mirrors tests/budget/test_store_issuance.py's in-memory scenarios, but
against the real SERIALIZABLE transaction and the real schema additions
(sampark/schema.sql tables 9-12). Every test is `@pytest.mark.postgres`
and uses the `pg_env` fixture (tests/budget/conftest.py), which skips
cleanly if Postgres is unreachable or unmigrated, and cleans up every
row it creates — including the shared `budget_windows` pool for any
window_id a test touches (`pg_env.track_window`).
"""

from __future__ import annotations

import datetime as dt

import pytest

from sampark.allocator.reason_codes import (
    CONTACT_CAP_24H,
    CONTACT_CAP_7D,
    CUSTOMER_MARGIN_EXHAUSTED,
    MERCHANT_MARGIN_EXHAUSTED,
)
from sampark.budget.issuance import issue_grant
from sampark.budget.store import BudgetDenial, GrantIssued
from sampark.contracts import GrantState

pytestmark = pytest.mark.postgres

DECISION_AT = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)


def _candidate_for(pg_env, make_candidate, risk_id, **kwargs):
    kwargs.setdefault("amount_paise", 500_000)
    item = pg_env.insert_risk_item(risk_id=risk_id, amount_paise=kwargs["amount_paise"])
    candidate = make_candidate(
        customer_id=pg_env.customer_id, risk_id=risk_id, agent_id=pg_env.agent_id, **kwargs
    )
    pg_env.track_window(candidate.window_id)
    return candidate


def test_first_issuance_grants(pg_env, make_candidate):
    candidate = _candidate_for(pg_env, make_candidate, "risk-1", bps=500)
    result = issue_grant(pg_env.conn, candidate, 500, DECISION_AT)
    assert isinstance(result, GrantIssued)
    assert result.grant.state is GrantState.RESERVED
    assert result.grant.incentive_ceiling_paise == 25_000  # 500_000 * 500 / 10_000


def test_idempotent_reissue_returns_same_grant(pg_env, make_candidate):
    candidate = _candidate_for(pg_env, make_candidate, "risk-1", bps=500)
    first = issue_grant(pg_env.conn, candidate, 500, DECISION_AT)
    second = issue_grant(pg_env.conn, candidate, 500, DECISION_AT)
    assert isinstance(first, GrantIssued) and isinstance(second, GrantIssued)
    assert first.grant.grant_id == second.grant.grant_id

    with pg_env.conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM grants WHERE request_id = %s", (candidate.request.request_id,))
        assert cur.fetchone()[0] == 1  # no second row was inserted


def test_second_candidate_same_customer_window_denied_by_contact_cap(pg_env, make_candidate):
    c1 = _candidate_for(pg_env, make_candidate, "risk-1", bps=0, amount_paise=1_000_000)
    c2 = _candidate_for(pg_env, make_candidate, "risk-2", bps=0, amount_paise=1_000_000)
    r1 = issue_grant(pg_env.conn, c1, 0, DECISION_AT)
    r2 = issue_grant(pg_env.conn, c2, 0, DECISION_AT)
    assert isinstance(r1, GrantIssued)
    assert isinstance(r2, BudgetDenial)
    assert r2.reason_code == CONTACT_CAP_24H  # subsumes the claim, Design Lock §3.2
    assert r2.next_eligible_at is not None

    with pg_env.conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM contact_slot_claims WHERE customer_id = %s AND state IN "
            "('RESERVED','EXECUTING','CONFIRMED')",
            (pg_env.customer_id,),
        )
        assert cur.fetchone()[0] == 1  # exactly one active claim, not two


def test_rolled_back_claim_frees_the_window(pg_env, make_candidate):
    c1 = _candidate_for(pg_env, make_candidate, "risk-1", bps=0, amount_paise=1_000_000)
    r1 = issue_grant(pg_env.conn, c1, 0, DECISION_AT)
    assert isinstance(r1, GrantIssued)

    with pg_env.conn.cursor() as cur:
        cur.execute(
            "UPDATE grants SET state = 'ROLLED_BACK' WHERE grant_id = %s", (r1.grant.grant_id,)
        )
        cur.execute(
            "UPDATE contact_slot_claims SET state = 'ROLLED_BACK', released_at = %s WHERE grant_id = %s",
            (DECISION_AT, r1.grant.grant_id),
        )
        cur.execute(
            "UPDATE budget_windows SET margin_reserved_paise = margin_reserved_paise - %s "
            "WHERE merchant_id = 'merchant-sim' AND window_id = %s",
            (r1.grant.incentive_ceiling_paise, c1.window_id),
        )
        cur.execute(
            "UPDATE customer_margin_windows SET margin_reserved_paise = margin_reserved_paise - %s "
            "WHERE customer_id = %s AND window_id = %s",
            (r1.grant.incentive_ceiling_paise, pg_env.customer_id, c1.window_id),
        )

    c2 = _candidate_for(pg_env, make_candidate, "risk-2", bps=0, amount_paise=1_000_000)
    r2 = issue_grant(pg_env.conn, c2, 0, DECISION_AT)
    assert isinstance(r2, GrantIssued), "a rolled-back claim must free the (customer, window) slot"


def test_contact_cap_24h_denies_across_different_windows(pg_env, make_candidate):
    c1 = _candidate_for(
        pg_env, make_candidate, "risk-1", bps=0,
        proposed_send_after=dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc),
    )
    r1 = issue_grant(pg_env.conn, c1, 0, dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc))
    assert isinstance(r1, GrantIssued)

    c2 = _candidate_for(
        pg_env, make_candidate, "risk-2", bps=0,
        proposed_send_after=dt.datetime(2025, 9, 11, 9, 0, tzinfo=dt.timezone.utc),
    )
    r2 = issue_grant(pg_env.conn, c2, 0, dt.datetime(2025, 9, 11, 8, 0, tzinfo=dt.timezone.utc))  # 23h later
    assert isinstance(r2, BudgetDenial)
    assert r2.reason_code == CONTACT_CAP_24H


def test_contact_cap_7d_denies_third_grant(pg_env, make_candidate):
    base = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)
    c1 = _candidate_for(pg_env, make_candidate, "risk-1", bps=0, proposed_send_after=base)
    r1 = issue_grant(pg_env.conn, c1, 0, base)
    assert isinstance(r1, GrantIssued)

    c2 = _candidate_for(pg_env, make_candidate, "risk-2", bps=0, proposed_send_after=base + dt.timedelta(days=2))
    r2 = issue_grant(pg_env.conn, c2, 0, base + dt.timedelta(days=2))
    assert isinstance(r2, GrantIssued)

    c3 = _candidate_for(pg_env, make_candidate, "risk-3", bps=0, proposed_send_after=base + dt.timedelta(days=4))
    r3 = issue_grant(pg_env.conn, c3, 0, base + dt.timedelta(days=4))
    assert isinstance(r3, BudgetDenial)
    assert r3.reason_code == CONTACT_CAP_7D


def test_merchant_margin_shortfall_downgrades(pg_env, make_candidate):
    # Constrain the merchant pool artificially small before issuance.
    candidate = _candidate_for(pg_env, make_candidate, "risk-1", bps=500, amount_paise=1_000_000)
    with pg_env.conn.cursor() as cur:
        cur.execute(
            "INSERT INTO budget_windows (budget_window_id, merchant_id, window_id, margin_budget_paise) "
            "VALUES (gen_random_uuid(), 'merchant-sim', %s, 100) "
            "ON CONFLICT (merchant_id, window_id) DO UPDATE SET margin_budget_paise = 100",
            (candidate.window_id,),
        )
    result = issue_grant(pg_env.conn, candidate, 500, DECISION_AT)
    assert isinstance(result, GrantIssued)
    assert result.grant.incentive_ceiling_paise == 100


def test_merchant_margin_fully_exhausted_denies_outright(pg_env, make_candidate):
    c1 = _candidate_for(pg_env, make_candidate, "risk-1", bps=500, amount_paise=1_000_000)
    with pg_env.conn.cursor() as cur:
        cur.execute(
            "INSERT INTO budget_windows (budget_window_id, merchant_id, window_id, margin_budget_paise) "
            "VALUES (gen_random_uuid(), 'merchant-sim', %s, 1000) "
            "ON CONFLICT (merchant_id, window_id) DO UPDATE SET margin_budget_paise = 1000",
            (c1.window_id,),
        )
    r1 = issue_grant(pg_env.conn, c1, 500, DECISION_AT)
    assert isinstance(r1, GrantIssued)
    assert r1.grant.incentive_ceiling_paise == 1000  # pool fully consumed

    other_customer = f"{pg_env.customer_id}-other"
    pg_env.insert_customer(other_customer)
    item2 = pg_env.insert_risk_item(risk_id="risk-2", amount_paise=1_000_000, customer_id_override=other_customer)
    c2 = make_candidate(
        customer_id=other_customer, risk_id="risk-2", agent_id=pg_env.agent_id, bps=500, amount_paise=1_000_000,
        proposed_send_after=c1.proposed_send_after,
    )
    r2 = issue_grant(pg_env.conn, c2, 500, DECISION_AT)
    assert isinstance(r2, BudgetDenial)
    assert r2.reason_code == MERCHANT_MARGIN_EXHAUSTED


def test_customer_margin_pool_sized_from_total_at_risk(pg_env, make_candidate):
    """customer_margin_windows.margin_budget_paise = 500bps * customer's
    total known at-risk (Design Lock §14.3) — verified against the real
    lazily-created row, not the in-memory reference's Python dict."""
    candidate = _candidate_for(pg_env, make_candidate, "risk-1", bps=500, amount_paise=1_000_000)
    result = issue_grant(pg_env.conn, candidate, 500, DECISION_AT)
    assert isinstance(result, GrantIssued)

    with pg_env.conn.cursor() as cur:
        cur.execute(
            "SELECT margin_budget_paise FROM customer_margin_windows WHERE customer_id = %s AND window_id = %s",
            (pg_env.customer_id, candidate.window_id),
        )
        (budget,) = cur.fetchone()
        assert budget == 50_000  # 500bps * 1,000,000 / 10,000


def test_budget_not_overdrawn_invariant_holds_after_issuance(pg_env, make_candidate):
    candidate = _candidate_for(pg_env, make_candidate, "risk-1", bps=500, amount_paise=1_000_000)
    result = issue_grant(pg_env.conn, candidate, 500, DECISION_AT)
    assert isinstance(result, GrantIssued)

    with pg_env.conn.cursor() as cur:
        cur.execute(
            "SELECT margin_budget_paise, margin_reserved_paise, margin_spent_paise "
            "FROM budget_windows WHERE merchant_id = 'merchant-sim' AND window_id = %s",
            (candidate.window_id,),
        )
        budget, reserved, spent = cur.fetchone()
        assert reserved + spent <= budget


def test_cache_conformance_after_issuance(pg_env, make_candidate):
    """contact_states.contacts_24h/7d after a grant matches a fresh
    recomputation from the authoritative grants table — Design Lock §3.6."""
    candidate = _candidate_for(pg_env, make_candidate, "risk-1", bps=0)
    result = issue_grant(pg_env.conn, candidate, 0, DECISION_AT)
    assert isinstance(result, GrantIssued)

    with pg_env.conn.cursor() as cur:
        cur.execute(
            "SELECT contacts_24h, contacts_7d, last_contact_at FROM contact_states WHERE customer_id = %s",
            (pg_env.customer_id,),
        )
        cached_24h, cached_7d, cached_last = cur.fetchone()

        cur.execute(
            "SELECT "
            "  count(*) FILTER (WHERE g.send_after > %(decision_at)s - INTERVAL '24 hours'), "
            "  count(*) FILTER (WHERE g.send_after > %(decision_at)s - INTERVAL '7 days') "
            "FROM grants g JOIN grant_requests r ON r.request_id = g.request_id "
            "WHERE r.customer_id = %(customer_id)s AND g.state IN ('RESERVED','EXECUTING','CONFIRMED')",
            {"decision_at": DECISION_AT, "customer_id": pg_env.customer_id},
        )
        authoritative_24h, authoritative_7d = cur.fetchone()

    assert cached_24h == authoritative_24h == 1
    assert cached_7d == authoritative_7d == 1
    assert cached_last == candidate.proposed_send_after


def test_zero_budget_customer_pool_denies_even_a_zero_incentive_candidate(pg_env, make_candidate):
    """DOCUMENTED FINDING, not a Phase 4B-2 regression: both issuance
    implementations (this real-Postgres one and the in-memory reference
    in sampark/budget/store.py) check `remaining <= 0` BEFORE computing
    whether the candidate's own downgraded ceiling would even be
    nonzero. A customer whose total at-risk floors their margin pool to
    exactly 0 paise (500bps * 1 paise // 10_000 == 0) is therefore
    denied CUSTOMER_MARGIN_EXHAUSTED even for a bps=0 request that would
    reserve nothing. This is inherited, consistent behaviour across both
    implementations (Design Lock did not resolve it either way), NOT
    something this task should silently change — flagged in the
    verification report instead."""
    zero_risk_customer = f"{pg_env.customer_id}-zero"
    pg_env.insert_customer(zero_risk_customer)
    pg_env.insert_risk_item(risk_id="risk-zero", amount_paise=1, customer_id_override=zero_risk_customer)
    candidate = make_candidate(
        customer_id=zero_risk_customer, risk_id="risk-zero", agent_id=pg_env.agent_id,
        amount_paise=1, bps=0,
    )
    pg_env.track_window(candidate.window_id)

    result = issue_grant(pg_env.conn, candidate, 0, DECISION_AT)

    assert isinstance(result, BudgetDenial)
    assert result.reason_code == CUSTOMER_MARGIN_EXHAUSTED
