from __future__ import annotations

from sampark.budget.margin import (
    customer_margin_budget_paise,
    downgrade_to_fit,
    incentive_ceiling_paise,
    remaining_paise,
)


def test_incentive_ceiling_paise_floor_division():
    assert incentive_ceiling_paise(amount_paise=100_000, incentive_bps=500) == 5_000
    assert incentive_ceiling_paise(amount_paise=99, incentive_bps=1) == 0  # floors to 0, not negative


def test_customer_margin_budget_paise_uses_500_bps():
    assert customer_margin_budget_paise(1_000_000) == 50_000


def test_remaining_paise():
    assert remaining_paise(budget_paise=1000, reserved_paise=200, spent_paise=100) == 700


def test_downgrade_to_fit_returns_requested_when_both_pools_sufficient():
    assert downgrade_to_fit(5_000, merchant_remaining_paise=10_000, customer_remaining_paise=10_000) == 5_000


def test_downgrade_to_fit_uses_tighter_pool():
    assert downgrade_to_fit(5_000, merchant_remaining_paise=2_000, customer_remaining_paise=10_000) == 2_000
    assert downgrade_to_fit(5_000, merchant_remaining_paise=10_000, customer_remaining_paise=1_000) == 1_000


def test_downgrade_to_fit_never_negative():
    assert downgrade_to_fit(5_000, merchant_remaining_paise=-100, customer_remaining_paise=10_000) == 0
