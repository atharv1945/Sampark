"""RiskItem — CONTRACTS.md Part 1."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from sampark.contracts import RiskItem


def _now() -> datetime:
    return datetime(2026, 8, 24, 9, 12, tzinfo=timezone.utc)


def test_risk_item_valid_construction():
    item = RiskItem(
        risk_id="risk-1", source="mandate_autopay", amount_paise=410000,
        root_cause="insufficient_funds", detected_at=_now(),
    )
    assert item.amount_paise == 410000


def test_risk_item_rejects_zero_amount_paise():
    with pytest.raises(ValidationError):
        RiskItem(
            risk_id="risk-1", source="cart", amount_paise=0,
            root_cause="unknown", detected_at=_now(),
        )


def test_risk_item_rejects_negative_amount_paise():
    with pytest.raises(ValidationError):
        RiskItem(
            risk_id="risk-1", source="cart", amount_paise=-1,
            root_cause="unknown", detected_at=_now(),
        )


def test_risk_item_accepts_smallest_positive_amount_boundary():
    item = RiskItem(
        risk_id="risk-1", source="cart", amount_paise=1,
        root_cause="unknown", detected_at=_now(),
    )
    assert item.amount_paise == 1


def test_risk_item_has_no_customer_id_field():
    with pytest.raises(ValidationError):
        RiskItem(
            risk_id="risk-1", customer_id="cust-1", source="cart",
            amount_paise=100, root_cause="unknown", detected_at=_now(),
        )
