"""Shared fixtures for sampark/allocator/ tests."""

from __future__ import annotations

import datetime as dt
from uuid import uuid4

import pytest

from sampark.allocator.candidate import build_candidate
from sampark.budget.store import InMemoryGrantIssuer, InMemoryMediationLedger
from sampark.contracts import GrantRequest, RiskItem

DETECTED_AT = dt.datetime(2025, 9, 10, 6, 0, tzinfo=dt.timezone.utc)
WELL_FUNDED_AMOUNT_PAISE = 100_000_000


@pytest.fixture()
def make_candidate():
    def _make(
        customer_id: str = "cust-1",
        risk_id: str = "risk-1",
        amount_paise: int = 500_000,
        bps: int = 500,
        agent_id: str = "cart_recovery_agent",
        intent: str = "cart_recovery",
        channel: str = "whatsapp",
        proposed_send_after: dt.datetime = dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc),
        source: str = "abandoned_checkout",
        root_cause: str = "price_hesitation",
        detected_at: dt.datetime = DETECTED_AT,
    ):
        item = RiskItem(
            risk_id=risk_id, source=source, amount_paise=amount_paise,
            root_cause=root_cause, detected_at=detected_at,
        )
        request = GrantRequest(
            request_id=uuid4(), agent_id=agent_id, customer_id=customer_id, risk_id=risk_id,
            intent=intent, requested_channel=channel, requested_max_incentive_bps=bps,
            issued_at=detected_at, signature="sig",
        )
        return build_candidate(request, item, customer_id, proposed_send_after)

    return _make


@pytest.fixture()
def make_ledger():
    """A well-funded InMemoryMediationLedger seeded from EXACTLY the
    candidates it will mediate — mirrors how sim/arm_b.py populates
    risk_items_by_customer from the full ledger, so the customer margin
    pool is never accidentally the binding constraint under test."""

    def _make(*candidates, merchant_budget_paise_per_window: int = 1_000_000_000):
        by_customer: dict[str, list[RiskItem]] = {}
        for candidate in candidates:
            by_customer.setdefault(candidate.customer_id, []).append(candidate.risk_item)
        risk_items_by_customer = {cid: tuple(items) for cid, items in by_customer.items()}
        return InMemoryMediationLedger(
            risk_items_by_customer, merchant_budget_paise_per_window=merchant_budget_paise_per_window
        )

    return _make


@pytest.fixture()
def issuer():
    return InMemoryGrantIssuer()
