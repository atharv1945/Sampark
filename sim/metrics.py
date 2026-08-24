"""Pure metrics function — Arm A, and unchanged for Arm B later.

Consumes only a sequence of ContactOutcome and returns a plain dict; no
seed- or arm-specific state beyond what's passed in explicitly, so
Phase 4/6 can call this exact function against Arm B's outcome log for a
directly comparable number (locked decision: "Use exactly the same
metrics function later for Arm B").

Recovery-unit semantics (Phase 2 accounting clarification — read before
interpreting any figure below): every count and every paise figure here
is at RISK-ITEM granularity, not unique-customer or unique-economic-
payment granularity. One customer can own several risk items (e.g. one
failed payment and one overdue invoice in the same month), and each is
counted and summed independently if it recovers.
`recovered_amount_paise` is simply the sum of `RiskItem.amount_paise`
over recovered risk items — it is a synthetic risk-item-level figure,
NOT deduplicated customer-level revenue. The output's `recovery_unit`
key states this explicitly so a downstream consumer of the JSON cannot
mistake it for something it isn't.

This is also NOT credited recovery: there is no holdout-baseline
subtraction and no cross-agent attribution here. That correction is
Phase 7's responsibility (spec §8.9, "Credited recovery = observed
recovery − expected natural recovery") and is deliberately not
implemented in Phase 2.
"""

from __future__ import annotations

from typing import Sequence

from agents.types import ContactOutcome

_AGENT_IDS = (
    "payment_retry_agent",
    "cart_recovery_agent",
    "mandate_recovery_agent",
    "receivables_agent",
)


def _empty_agent_metrics() -> dict[str, int]:
    return {
        "contacts": 0,
        "recoveries": 0,
        "recovered_amount_paise": 0,
        "incentive_spend_paise": 0,
    }


def compute_metrics(outcomes: Sequence[ContactOutcome]) -> dict:
    by_agent: dict[str, dict[str, int]] = {
        agent_id: _empty_agent_metrics() for agent_id in _AGENT_IDS
    }

    total_contacts = 0
    total_recoveries = 0
    recovered_amount_paise = 0
    incentive_spend_paise = 0

    for outcome in outcomes:
        agent_metrics = by_agent.setdefault(outcome.agent_id, _empty_agent_metrics())

        total_contacts += 1
        agent_metrics["contacts"] += 1

        if outcome.recovered:
            total_recoveries += 1
            recovered_amount_paise += outcome.amount_recovered_paise
            incentive_spend_paise += outcome.incentive_paise
            agent_metrics["recoveries"] += 1
            agent_metrics["recovered_amount_paise"] += outcome.amount_recovered_paise
            agent_metrics["incentive_spend_paise"] += outcome.incentive_paise

    recovered_amount_per_contact_paise = (
        recovered_amount_paise / total_contacts if total_contacts else 0.0
    )

    return {
        "recovery_unit": "risk_item",
        "total_contacts": total_contacts,
        "total_recoveries": total_recoveries,
        "recovered_amount_paise": recovered_amount_paise,
        "incentive_spend_paise": incentive_spend_paise,
        "recovered_amount_per_contact_paise": recovered_amount_per_contact_paise,
        "by_agent": by_agent,
    }
