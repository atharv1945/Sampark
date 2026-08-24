"""Shared Arm A agent fixtures — a small, hand-built LedgerView instead of
running the full Phase 1 generator, so per-agent tests can assert exact
expected values without depending on generator output."""

from __future__ import annotations

import datetime as dt

import pytest

from agents.types import LedgerView
from sampark.contracts import Customer, RiskItem

DETECTED_AT = dt.datetime(2025, 9, 1, 10, 0, tzinfo=dt.timezone.utc)

_RISK_ITEMS = (
    RiskItem(
        risk_id="fp-1",
        source="failed_payment",
        amount_paise=10_000,
        root_cause="insufficient_funds",
        detected_at=DETECTED_AT,
    ),
    RiskItem(
        risk_id="fp-2",
        source="failed_payment",
        amount_paise=20_000,
        root_cause="unknown",
        detected_at=DETECTED_AT,
    ),
    RiskItem(
        risk_id="ac-1",
        source="abandoned_checkout",
        amount_paise=15_000,
        root_cause="price_hesitation",
        detected_at=DETECTED_AT,
    ),
    RiskItem(
        risk_id="mf-1",
        source="mandate_failure",
        amount_paise=30_000,
        root_cause="mandate_expired",
        detected_at=DETECTED_AT,
    ),
    RiskItem(
        risk_id="oi-1",
        source="overdue_invoice",
        amount_paise=50_000,
        root_cause="disputed",
        detected_at=DETECTED_AT,
    ),
)

_CUSTOMER_ID_BY_RISK_ID = {
    "fp-1": "cust-1",
    "fp-2": "cust-1",
    "ac-1": "cust-2",
    "mf-1": "cust-3",
    "oi-1": "cust-4",
}


@pytest.fixture()
def detected_at() -> dt.datetime:
    return DETECTED_AT


@pytest.fixture()
def sample_view() -> LedgerView:
    risk_items_by_source: dict[str, list[RiskItem]] = {}
    for item in _RISK_ITEMS:
        risk_items_by_source.setdefault(item.source, []).append(item)

    return LedgerView(
        customers_by_id={
            cid: Customer(customer_id=cid) for cid in set(_CUSTOMER_ID_BY_RISK_ID.values())
        },
        risk_items_by_source={
            source: tuple(items) for source, items in risk_items_by_source.items()
        },
        customer_id_by_risk_id=dict(_CUSTOMER_ID_BY_RISK_ID),
    )
