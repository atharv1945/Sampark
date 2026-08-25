"""GrantIssuer protocol conformance — InMemoryGrantIssuer vs
PostgresGrantIssuer, Phase 4C-2 Blocker 1.

Runs the SAME scenario functions against both issuers, parametrized,
asserting identical GrantIssued/BudgetDenial SHAPES (reason codes,
grant field values) for the same inputs. This is protocol-level
conformance ("where applicable" — the task's own qualifier): it does
NOT assert that a full ~20,000-candidate Arm B run produces
byte-identical outcomes across backends (it does not, at the margin —
a small number of grants can differ at full scale; see Phase 4C-2's
verification report). What it DOES prove is that for a controlled
scenario, both issuers make the same decision for the same reason.

The Postgres half of each scenario is `@pytest.mark.postgres` and uses
`tests/budget/conftest.py`'s `pg_env` fixture, skipping cleanly if
Postgres is unreachable or unmigrated — same convention as every other
Postgres test in this suite.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Callable

import pytest

from sampark.allocator.candidate import build_candidate
from sampark.allocator.reason_codes import CONTACT_CAP_24H, MERCHANT_MARGIN_EXHAUSTED
from sampark.budget.issuance import issue_grant as postgres_issue_grant
from sampark.budget.store import BudgetDenial, GrantIssued, InMemoryGrantIssuer, InMemoryMediationLedger
from sampark.contracts import GrantRequest, GrantState, RiskItem
from uuid import uuid4

DECISION_AT = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)
DETECTED_AT = dt.datetime(2025, 9, 10, 6, 0, tzinfo=dt.timezone.utc)


@dataclass(frozen=True)
class Scenario:
    name: str
    amount_paise: int
    bps: int
    proposed_send_after: dt.datetime


def _candidate(scenario: Scenario, customer_id: str, risk_id: str, agent_id: str = "cart_recovery_agent"):
    item = RiskItem(
        risk_id=risk_id, source="abandoned_checkout", amount_paise=scenario.amount_paise,
        root_cause="price_hesitation", detected_at=DETECTED_AT,
    )
    request = GrantRequest(
        request_id=uuid4(), agent_id=agent_id, customer_id=customer_id, risk_id=risk_id,
        intent="cart_recovery", requested_channel="whatsapp", requested_max_incentive_bps=scenario.bps,
        issued_at=DETECTED_AT, signature="sig",
    )
    return build_candidate(request, item, customer_id, scenario.proposed_send_after)


BASIC = Scenario("basic_grant", amount_paise=1_000_000, bps=500, proposed_send_after=dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc))


def _well_funded_ledger(*candidates, merchant_budget_paise_per_window: int = 1_000_000_000) -> InMemoryMediationLedger:
    """Every in-memory scenario below must seed risk_items_by_customer
    with the SAME risk items the candidates reference — the customer
    margin pool is sized from a customer's total known at-risk amount
    (Design Lock §14.3), so an empty dict here floors it to zero and
    every request is denied on customer-margin exhaustion regardless of
    what the scenario is actually testing."""
    by_customer: dict[str, list[RiskItem]] = {}
    for c in candidates:
        by_customer.setdefault(c.customer_id, []).append(c.risk_item)
    return InMemoryMediationLedger(
        {cid: tuple(items) for cid, items in by_customer.items()},
        merchant_budget_paise_per_window=merchant_budget_paise_per_window,
    )


def test_in_memory_first_issuance_grants_expected_ceiling():
    candidate = _candidate(BASIC, "cust-1", "risk-1")
    ledger = _well_funded_ledger(candidate)
    issuer = InMemoryGrantIssuer()
    result = issuer.issue_grant(ledger, candidate, BASIC.bps, DECISION_AT)
    assert isinstance(result, GrantIssued)
    assert result.grant.state is GrantState.RESERVED
    assert result.grant.incentive_ceiling_paise == 50_000  # 1,000,000 * 500 / 10,000


@pytest.mark.postgres
def test_postgres_first_issuance_grants_expected_ceiling(pg_env):
    candidate_risk_item = pg_env.insert_risk_item("risk-1", amount_paise=BASIC.amount_paise)
    from sampark.allocator.candidate import build_candidate as bc

    request = GrantRequest(
        request_id=uuid4(), agent_id=pg_env.agent_id, customer_id=pg_env.customer_id, risk_id="risk-1",
        intent="cart_recovery", requested_channel="whatsapp", requested_max_incentive_bps=BASIC.bps,
        issued_at=DETECTED_AT, signature="sig",
    )
    candidate = bc(request, candidate_risk_item, pg_env.customer_id, BASIC.proposed_send_after)
    pg_env.track_window(candidate.window_id)

    result = postgres_issue_grant(pg_env.conn, candidate, BASIC.bps, DECISION_AT)
    assert isinstance(result, GrantIssued)
    assert result.grant.state is GrantState.RESERVED
    assert result.grant.incentive_ceiling_paise == 50_000  # SAME shape as the in-memory scenario


def test_in_memory_contact_cap_24h_denial_shape():
    base = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)
    c1 = _candidate(Scenario("c1", 500_000, 0, base), "cust-1", "risk-1")
    c2 = _candidate(Scenario("c2", 500_000, 0, base + dt.timedelta(hours=23)), "cust-1", "risk-2")
    ledger = _well_funded_ledger(c1, c2)
    issuer = InMemoryGrantIssuer()

    r1 = issuer.issue_grant(ledger, c1, 0, base)
    assert isinstance(r1, GrantIssued)
    r2 = issuer.issue_grant(ledger, c2, 0, base + dt.timedelta(hours=23))

    assert isinstance(r2, BudgetDenial)
    assert r2.reason_code == CONTACT_CAP_24H
    assert r2.next_eligible_at is not None


@pytest.mark.postgres
def test_postgres_contact_cap_24h_denial_shape(pg_env):
    from sampark.allocator.candidate import build_candidate as bc

    base = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)
    item1 = pg_env.insert_risk_item("risk-1", amount_paise=500_000)
    req1 = GrantRequest(
        request_id=uuid4(), agent_id=pg_env.agent_id, customer_id=pg_env.customer_id, risk_id="risk-1",
        intent="cart_recovery", requested_channel="whatsapp", requested_max_incentive_bps=0,
        issued_at=DETECTED_AT, signature="sig",
    )
    c1 = bc(req1, item1, pg_env.customer_id, base)
    pg_env.track_window(c1.window_id)
    r1 = postgres_issue_grant(pg_env.conn, c1, 0, base)
    assert isinstance(r1, GrantIssued)

    second_at = base + dt.timedelta(hours=23)
    item2 = pg_env.insert_risk_item("risk-2", amount_paise=500_000)
    req2 = GrantRequest(
        request_id=uuid4(), agent_id=pg_env.agent_id, customer_id=pg_env.customer_id, risk_id="risk-2",
        intent="cart_recovery", requested_channel="whatsapp", requested_max_incentive_bps=0,
        issued_at=DETECTED_AT, signature="sig",
    )
    c2 = bc(req2, item2, pg_env.customer_id, second_at)
    pg_env.track_window(c2.window_id)
    r2 = postgres_issue_grant(pg_env.conn, c2, 0, second_at)

    assert isinstance(r2, BudgetDenial)
    assert r2.reason_code == CONTACT_CAP_24H  # SAME shape as the in-memory scenario
    assert r2.next_eligible_at is not None


def test_in_memory_idempotent_reissue_shape():
    candidate = _candidate(BASIC, "cust-1", "risk-1")
    ledger = _well_funded_ledger(candidate)
    issuer = InMemoryGrantIssuer()
    first = issuer.issue_grant(ledger, candidate, BASIC.bps, DECISION_AT)
    second = issuer.issue_grant(ledger, candidate, BASIC.bps, DECISION_AT)
    assert isinstance(first, GrantIssued) and isinstance(second, GrantIssued)
    assert first.grant.grant_id == second.grant.grant_id


@pytest.mark.postgres
def test_postgres_idempotent_reissue_shape(pg_env):
    from sampark.allocator.candidate import build_candidate as bc

    item = pg_env.insert_risk_item("risk-1", amount_paise=BASIC.amount_paise)
    request = GrantRequest(
        request_id=uuid4(), agent_id=pg_env.agent_id, customer_id=pg_env.customer_id, risk_id="risk-1",
        intent="cart_recovery", requested_channel="whatsapp", requested_max_incentive_bps=BASIC.bps,
        issued_at=DETECTED_AT, signature="sig",
    )
    candidate = bc(request, item, pg_env.customer_id, BASIC.proposed_send_after)
    pg_env.track_window(candidate.window_id)

    first = postgres_issue_grant(pg_env.conn, candidate, BASIC.bps, DECISION_AT)
    second = postgres_issue_grant(pg_env.conn, candidate, BASIC.bps, DECISION_AT)
    assert isinstance(first, GrantIssued) and isinstance(second, GrantIssued)
    assert first.grant.grant_id == second.grant.grant_id  # SAME idempotency shape as the in-memory scenario


def test_in_memory_merchant_exhaustion_denies_a_second_customer():
    c1 = _candidate(Scenario("c1", 1_000_000, 500, DECISION_AT), "cust-1", "risk-1")
    c2 = _candidate(Scenario("c2", 1_000_000, 500, DECISION_AT), "cust-2", "risk-2")
    ledger = _well_funded_ledger(c1, c2, merchant_budget_paise_per_window=1_000)
    issuer = InMemoryGrantIssuer()
    r1 = issuer.issue_grant(ledger, c1, 500, DECISION_AT)
    assert isinstance(r1, GrantIssued)
    assert r1.grant.incentive_ceiling_paise == 1_000

    r2 = issuer.issue_grant(ledger, c2, 500, DECISION_AT)
    assert isinstance(r2, BudgetDenial)
    assert r2.reason_code == MERCHANT_MARGIN_EXHAUSTED


@pytest.mark.postgres
def test_postgres_merchant_exhaustion_denies_a_second_customer(pg_env):
    from sampark.allocator.candidate import build_candidate as bc

    item1 = pg_env.insert_risk_item("risk-1", amount_paise=1_000_000)
    req1 = GrantRequest(
        request_id=uuid4(), agent_id=pg_env.agent_id, customer_id=pg_env.customer_id, risk_id="risk-1",
        intent="cart_recovery", requested_channel="whatsapp", requested_max_incentive_bps=500,
        issued_at=DETECTED_AT, signature="sig",
    )
    c1 = bc(req1, item1, pg_env.customer_id, DECISION_AT)
    pg_env.track_window(c1.window_id)
    with pg_env.conn.cursor() as cur:
        cur.execute(
            "INSERT INTO budget_windows (budget_window_id, merchant_id, window_id, margin_budget_paise) "
            "VALUES (gen_random_uuid(), 'merchant-sim', %s, 1000) "
            "ON CONFLICT (merchant_id, window_id) DO UPDATE SET margin_budget_paise = 1000",
            (c1.window_id,),
        )
    r1 = postgres_issue_grant(pg_env.conn, c1, 500, DECISION_AT)
    assert isinstance(r1, GrantIssued)
    assert r1.grant.incentive_ceiling_paise == 1_000

    other_customer = f"{pg_env.customer_id}-other"
    pg_env.insert_customer(other_customer)
    item2 = pg_env.insert_risk_item("risk-2", amount_paise=1_000_000, customer_id_override=other_customer)
    req2 = GrantRequest(
        request_id=uuid4(), agent_id=pg_env.agent_id, customer_id=other_customer, risk_id="risk-2",
        intent="cart_recovery", requested_channel="whatsapp", requested_max_incentive_bps=500,
        issued_at=DETECTED_AT, signature="sig",
    )
    c2 = bc(req2, item2, other_customer, DECISION_AT)
    r2 = postgres_issue_grant(pg_env.conn, c2, 500, DECISION_AT)
    assert isinstance(r2, BudgetDenial)
    assert r2.reason_code == MERCHANT_MARGIN_EXHAUSTED  # SAME shape as the in-memory scenario
