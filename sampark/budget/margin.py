"""Margin-pool arithmetic — Design Lock §2, §14.3.

Pure functions only. The pools themselves (reserve/confirm/rollback
against durable state) are mutated only inside the issuance boundary
(owner-authored sampark/budget/issuance.py) or its reference/test
double (sampark/budget/store.py::InMemoryMediationLedger). This module
computes the NUMBERS that boundary needs; it holds no state itself.
"""

from __future__ import annotations

from sampark.allocator.constants import CUSTOMER_MARGIN_BPS


def incentive_ceiling_paise(amount_paise: int, incentive_bps: int) -> int:
    """Design Lock §2: ceiling = amount_paise * incentive_bps / 10_000,
    integer floor division. `amount_paise` must be the ledger's
    authoritative RiskItem.amount_paise, never a request-declared value."""
    return (amount_paise * incentive_bps) // 10_000


def customer_margin_budget_paise(customer_open_at_risk_paise: int) -> int:
    """Design Lock §14.3: 500 bps x the customer's total open at-risk.
    500 bps is the highest existing agent capability ceiling
    (cart_recovery_agent, agents/cart_recovery.py:INCENTIVE_BPS)."""
    return (customer_open_at_risk_paise * CUSTOMER_MARGIN_BPS) // 10_000


def remaining_paise(budget_paise: int, reserved_paise: int, spent_paise: int) -> int:
    """Never stored — always computed, so it cannot disagree with the
    three numbers it derives from (Design Lock §2)."""
    return budget_paise - reserved_paise - spent_paise


def downgrade_to_fit(
    requested_ceiling_paise: int,
    merchant_remaining_paise: int,
    customer_remaining_paise: int,
) -> int:
    """The ceiling actually reservable right now — the tightest of the
    requested amount and what both pools can still cover. Never
    negative: a pool that is already overdrawn (should not happen; the
    schema's not_overdrawn CHECK forbids it) still yields 0, not a
    negative ceiling."""
    fitted = min(requested_ceiling_paise, merchant_remaining_paise, customer_remaining_paise)
    return max(fitted, 0)
