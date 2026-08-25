"""PostgresMediationLedger — Phase 4C-2 hardening regression.

Root cause: `risk_items` is a SHARED table across every seed ever
loaded into this Postgres instance (Phase 1's own committed-generator
pattern — seeds are never cleaned up). Two different seeds' synthetic
populations can resolve the same person to the SAME customer_id (spec
§8.2 identity resolution doing exactly what it's supposed to). Before
this fix, `risk_items_for_customer` / `open_candidates_for_customer`
queried that table with no seed scoping, so a customer shared across
seeds leaked a DIFFERENT seed's risk items — including, in the
seed-42/seed-43 case that surfaced this, a `root_cause='disputed'` item
that wrongly fired `interlock.dispute_open` and inflated the fatigue
term enough to flip a candidate from GRANTED to
`allocation.negative_expected_net` — for candidates the single-seed
in-memory reference correctly granted.

This test constructs exactly that shape directly against Postgres: one
customer, two risk items belonging to "this run's seed"
(`run_seed_risk_ids`) and one extra risk item for the SAME customer
that is NOT in that set (standing in for another seed's leaked data),
with `root_cause='disputed'`. Before the fix this test fails (the
disputed item leaks through); after the fix it passes.
"""

from __future__ import annotations

import datetime as dt

import pytest

from sampark.budget.postgres_ledger import PostgresMediationLedger
from sampark.budget.store import MERCHANT_ID

pytestmark = pytest.mark.postgres

DETECTED_AT = dt.datetime(2025, 9, 10, 6, 0, tzinfo=dt.timezone.utc)
DECISION_AT = dt.datetime(2025, 9, 25, 9, 0, tzinfo=dt.timezone.utc)


def test_risk_items_for_customer_excludes_other_seeds_risk_items(pg_env):
    """The exact regression: a customer with risk items from TWO
    different "generations" (this run's seed, and a stand-in for
    another seed's leaked data) must only see the current run's own
    items."""
    this_seed_item_1 = pg_env.insert_risk_item("risk-this-seed-1", amount_paise=500_000, root_cause="price_hesitation")
    this_seed_item_2 = pg_env.insert_risk_item("risk-this-seed-2", amount_paise=300_000, root_cause="intent_lost")
    other_seed_disputed_item = pg_env.insert_risk_item(
        "risk-other-seed-disputed", amount_paise=1_000_000, root_cause="disputed"
    )

    run_seed_risk_ids = frozenset({this_seed_item_1.risk_id, this_seed_item_2.risk_id})
    ledger = PostgresMediationLedger(pg_env.conn, MERCHANT_ID, run_seed_risk_ids)

    items = ledger.risk_items_for_customer(pg_env.customer_id)
    seen_risk_ids = {item.risk_id for item in items}

    assert seen_risk_ids == {this_seed_item_1.risk_id, this_seed_item_2.risk_id}
    assert other_seed_disputed_item.risk_id not in seen_risk_ids
    assert not any(item.root_cause == "disputed" for item in items), (
        "a disputed risk item from a DIFFERENT seed must never leak into this run's "
        "interlock.dispute_open evaluation"
    )


def test_open_candidates_for_customer_excludes_other_seeds_risk_items(pg_env):
    """Same leak, at the fatigue-term's other_open_amounts_paise input."""
    this_seed_item = pg_env.insert_risk_item("risk-this-seed-1", amount_paise=500_000)
    other_seed_item = pg_env.insert_risk_item("risk-other-seed-1", amount_paise=2_000_000)

    run_seed_risk_ids = frozenset({this_seed_item.risk_id})  # deliberately excludes other_seed_item
    ledger = PostgresMediationLedger(pg_env.conn, MERCHANT_ID, run_seed_risk_ids)

    open_items = ledger.open_candidates_for_customer(
        pg_env.customer_id, DECISION_AT, exclude_risk_id="risk-does-not-exist"
    )
    seen_risk_ids = {item.risk_id for item in open_items}

    assert this_seed_item.risk_id in seen_risk_ids
    assert other_seed_item.risk_id not in seen_risk_ids


def test_risk_items_for_customer_still_returns_this_runs_own_items(pg_env):
    """The fix must not become a fail-closed no-op — this run's own
    risk items must still be visible."""
    item = pg_env.insert_risk_item("risk-1", amount_paise=500_000)
    ledger = PostgresMediationLedger(pg_env.conn, MERCHANT_ID, frozenset({item.risk_id}))

    items = ledger.risk_items_for_customer(pg_env.customer_id)
    assert {i.risk_id for i in items} == {item.risk_id}


# --- remaining_margin_paise: lazily-created rows must preview as FULL
# --- budget, not zero (Phase 4C-2 hardening, second finding) ----------


def test_remaining_margin_previews_full_merchant_budget_before_the_row_exists(pg_env):
    """budget_windows is created lazily inside issue_grant — before any
    grant has ever been issued for a window, there is no row at all.
    That must preview as the FULL configured budget, not zero (a zero
    preview silently downgrades every incentive-bearing candidate's
    ceiling to 0, as seed 42 demonstrated: every cart_recovery_agent and
    mandate_recovery_agent outcome, 4,395 of 10,298, lost its incentive)."""
    item = pg_env.insert_risk_item("risk-1", amount_paise=500_000)
    ledger = PostgresMediationLedger(
        pg_env.conn, MERCHANT_ID, frozenset({item.risk_id}), merchant_budget_paise_per_window=3_679_105
    )

    with pg_env.conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM budget_windows WHERE merchant_id = %s AND window_id = %s",
            (MERCHANT_ID, DECISION_AT.date()),
        )
        assert cur.fetchone()[0] == 0, "precondition: no budget_windows row exists yet"

    merchant_remaining, _customer_remaining = ledger.remaining_margin_paise(pg_env.customer_id, DECISION_AT.date())
    assert merchant_remaining == 3_679_105


def test_remaining_margin_previews_correct_customer_budget_before_the_row_exists(pg_env):
    """customer_margin_windows is ALSO created lazily — before this
    customer's first-ever grant in a window, there is no row. The
    preview must compute the same 500bps-of-total-at-risk value
    issue_grant would seed the row with, not zero."""
    item = pg_env.insert_risk_item("risk-1", amount_paise=1_000_000)
    ledger = PostgresMediationLedger(pg_env.conn, MERCHANT_ID, frozenset({item.risk_id}))

    with pg_env.conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM customer_margin_windows WHERE customer_id = %s AND window_id = %s",
            (pg_env.customer_id, DECISION_AT.date()),
        )
        assert cur.fetchone()[0] == 0, "precondition: no customer_margin_windows row exists yet"

    _merchant_remaining, customer_remaining = ledger.remaining_margin_paise(pg_env.customer_id, DECISION_AT.date())
    assert customer_remaining == 50_000  # 500bps * 1,000,000 / 10,000


def test_remaining_margin_reflects_actual_reservations_once_the_row_exists(pg_env):
    """Once a row DOES exist (a prior grant already reserved margin in
    this window), the preview must read the REAL reserved amount, not
    fall back to the full-budget default a second time."""
    item = pg_env.insert_risk_item("risk-1", amount_paise=1_000_000)
    ledger = PostgresMediationLedger(pg_env.conn, MERCHANT_ID, frozenset({item.risk_id}))

    with pg_env.conn.cursor() as cur:
        cur.execute(
            "INSERT INTO customer_margin_windows (customer_margin_window_id, customer_id, window_id, "
            "margin_budget_paise, margin_reserved_paise) VALUES (gen_random_uuid(), %s, %s, 50000, 30000)",
            (pg_env.customer_id, DECISION_AT.date()),
        )

    _merchant_remaining, customer_remaining = ledger.remaining_margin_paise(pg_env.customer_id, DECISION_AT.date())
    assert customer_remaining == 20_000  # 50,000 budget - 30,000 already reserved
