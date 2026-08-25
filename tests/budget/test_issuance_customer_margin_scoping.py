"""Cross-seed customer-margin leakage — Phase 4C pre-evidence hardening
(W5). SAME BUG CLASS as the earlier Phase 4C-2 hardening fix applied to
`sampark.budget.postgres_ledger.PostgresMediationLedger.remaining_margin_paise`
(that fix scoped the READ-side PREVIEW to `run_seed_risk_ids`). This file
proves the identical defect is now closed at the AUTHORITATIVE source:
`sampark.budget.issuance._attempt_once`'s own customer-margin-budget
query.

    sampark/budget/issuance.py — before this fix:
        cur.execute(
            "SELECT COALESCE(SUM(amount_paise), 0) AS total FROM risk_items "
            "WHERE customer_id = %s", (customer_id,)
        )

`risk_items` is a SHARED table across every seed ever loaded into this
Postgres instance (Phase 1's committed-generator pattern — seeds are
never cleaned up). Two different seeds' synthetic populations can
resolve the SAME person to the SAME `customer_id` (spec §8.2's identity
resolution doing exactly what it is supposed to). The unscoped query had
no way to distinguish "this run's" risk items from another seed's for
that customer_id, inflating `customer_margin_windows.margin_budget_paise`
beyond what a single-seed evaluation run should ever see.

**The fix** — `sampark.budget.issuance.issue_grant` (and
`_attempt_once`, and `PostgresGrantIssuer.issue_grant`) now accept an
explicit `run_seed_risk_ids: frozenset[str] | None = None` parameter.
When provided, the customer-margin query becomes
`WHERE customer_id = %s AND risk_id = ANY(%s)` — scoped to exactly this
run's own risk items, closing the leak this test demonstrates. The
parameter DEFAULTS to `None` (preserving the original, unscoped query)
rather than being required, SOLELY so its addition does not break
`tests/test_concurrent_grant_issuance.py` — human-owned, explicitly not
to be modified, and unaffected by scoping either way (single customer,
no cross-seed exposure in that fixture). Every production caller
(`sim/arm_b.py`, the official evidence path) passes the real set
explicitly — see `sim/arm_b.py`'s `run_seed_risk_ids` threading.
"""

from __future__ import annotations

import datetime as dt

import pytest

from sampark.budget.issuance import issue_grant
from sampark.budget.store import GrantIssued

pytestmark = pytest.mark.postgres

DECISION_AT = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)


def test_customer_margin_budget_is_not_inflated_by_another_seeds_risk_item(pg_env, make_candidate):
    """A customer with TWO risk items — one standing in for "this run's
    own" item (500,000 paise) and one standing in for "a different
    seed's leaked item" for the SAME customer_id (2,000,000 paise) —
    must have its customer_margin_windows.margin_budget_paise sized from
    ONLY the 500,000-paise item (500bps -> 25,000 paise) when
    `run_seed_risk_ids` is passed scoped to just that item, exactly as
    `PostgresMediationLedger.remaining_margin_paise` already does on the
    read side."""
    other_seed_leaked_item = pg_env.insert_risk_item(
        risk_id="risk-other-seed-leaked", amount_paise=2_000_000
    )
    this_run_item = pg_env.insert_risk_item(risk_id="risk-this-run", amount_paise=500_000)

    candidate = make_candidate(
        customer_id=pg_env.customer_id, risk_id=this_run_item.risk_id, agent_id=pg_env.agent_id,
        amount_paise=500_000, bps=500,
    )
    pg_env.track_window(candidate.window_id)

    result = issue_grant(
        pg_env.conn, candidate, 500, DECISION_AT, run_seed_risk_ids=frozenset({this_run_item.risk_id})
    )
    assert isinstance(result, GrantIssued)

    with pg_env.conn.cursor() as cur:
        cur.execute(
            "SELECT margin_budget_paise FROM customer_margin_windows WHERE customer_id = %s AND window_id = %s",
            (pg_env.customer_id, candidate.window_id),
        )
        (budget,) = cur.fetchone()

    assert budget == 25_000  # 500bps * 500,000 / 10,000 — THIS run's own item only, other_seed_leaked_item excluded


def test_omitting_run_seed_risk_ids_preserves_the_pre_fix_unscoped_behavior(pg_env, make_candidate):
    """Documents the deliberate backward-compatibility default: a caller
    that does NOT pass `run_seed_risk_ids` (like the human-owned
    concurrency test) still gets the original unscoped sum — the SAME
    two-item customer as above, but the budget now includes BOTH items,
    because no scoping was requested."""
    pg_env.insert_risk_item(risk_id="risk-other-seed-leaked-2", amount_paise=2_000_000)
    this_run_item = pg_env.insert_risk_item(risk_id="risk-this-run-2", amount_paise=500_000)

    candidate = make_candidate(
        customer_id=pg_env.customer_id, risk_id=this_run_item.risk_id, agent_id=pg_env.agent_id,
        amount_paise=500_000, bps=500,
    )
    pg_env.track_window(candidate.window_id)

    result = issue_grant(pg_env.conn, candidate, 500, DECISION_AT)  # no run_seed_risk_ids passed
    assert isinstance(result, GrantIssued)

    with pg_env.conn.cursor() as cur:
        cur.execute(
            "SELECT margin_budget_paise FROM customer_margin_windows WHERE customer_id = %s AND window_id = %s",
            (pg_env.customer_id, candidate.window_id),
        )
        (budget,) = cur.fetchone()

    assert budget == 125_000  # 500bps * (500,000 + 2,000,000) / 10,000 — unscoped, both items summed
