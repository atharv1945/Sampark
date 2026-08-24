"""sim/metrics.py — pure function over ContactOutcome, unit-scale
fixtures (no generator dependency)."""

from __future__ import annotations

import datetime as dt

from agents.types import ContactOutcome
from sim.metrics import compute_metrics


def _outcome(
    agent_id: str,
    recovered: bool,
    amount_recovered_paise: int = 0,
    incentive_paise: int = 0,
    risk_id: str = "r-1",
    customer_id: str = "c-1",
) -> ContactOutcome:
    return ContactOutcome(
        outcome_id=f"{agent_id}:{risk_id}",
        agent_id=agent_id,
        customer_id=customer_id,
        risk_id=risk_id,
        channel="sms",
        incentive_bps=0,
        contacted_at=dt.datetime(2025, 9, 1, tzinfo=dt.timezone.utc),
        recovered=recovered,
        amount_recovered_paise=amount_recovered_paise,
        incentive_paise=incentive_paise,
    )


def test_metrics_declare_risk_item_as_the_recovery_unit() -> None:
    """Phase 2 accounting clarification: recovered_amount_paise is a
    synthetic risk-item-level figure, not deduplicated customer revenue
    — the output must say so explicitly, present regardless of whether
    any outcomes recovered."""
    assert compute_metrics(())["recovery_unit"] == "risk_item"
    outcomes = [_outcome("payment_retry_agent", recovered=True, amount_recovered_paise=10_000)]
    assert compute_metrics(outcomes)["recovery_unit"] == "risk_item"


def test_empty_outcomes_yields_zeroed_metrics() -> None:
    metrics = compute_metrics(())
    assert metrics["total_contacts"] == 0
    assert metrics["total_recoveries"] == 0
    assert metrics["recovered_amount_paise"] == 0
    assert metrics["incentive_spend_paise"] == 0
    assert metrics["recovered_amount_per_contact_paise"] == 0.0
    assert metrics["by_agent"]["payment_retry_agent"] == {
        "contacts": 0,
        "recoveries": 0,
        "recovered_amount_paise": 0,
        "incentive_spend_paise": 0,
    }


def test_totals_and_per_agent_breakdown() -> None:
    outcomes = [
        _outcome("payment_retry_agent", recovered=True, amount_recovered_paise=10_000, risk_id="r-1"),
        _outcome("payment_retry_agent", recovered=False, risk_id="r-2"),
        _outcome(
            "cart_recovery_agent",
            recovered=True,
            amount_recovered_paise=20_000,
            incentive_paise=1_000,
            risk_id="r-3",
        ),
    ]
    metrics = compute_metrics(outcomes)

    assert metrics["total_contacts"] == 3
    assert metrics["total_recoveries"] == 2
    assert metrics["recovered_amount_paise"] == 30_000
    assert metrics["incentive_spend_paise"] == 1_000
    assert metrics["recovered_amount_per_contact_paise"] == 10_000.0

    assert metrics["by_agent"]["payment_retry_agent"] == {
        "contacts": 2,
        "recoveries": 1,
        "recovered_amount_paise": 10_000,
        "incentive_spend_paise": 0,
    }
    assert metrics["by_agent"]["cart_recovery_agent"] == {
        "contacts": 1,
        "recoveries": 1,
        "recovered_amount_paise": 20_000,
        "incentive_spend_paise": 1_000,
    }
    assert metrics["by_agent"]["mandate_recovery_agent"]["contacts"] == 0
    assert metrics["by_agent"]["receivables_agent"]["contacts"] == 0


def test_unrecovered_outcome_never_contributes_incentive_spend() -> None:
    outcomes = [_outcome("mandate_recovery_agent", recovered=False, amount_recovered_paise=0, incentive_paise=0)]
    metrics = compute_metrics(outcomes)
    assert metrics["incentive_spend_paise"] == 0
    assert metrics["recovered_amount_paise"] == 0


def test_unknown_agent_id_still_counted_at_top_level() -> None:
    outcomes = [_outcome("rogue_agent", recovered=True, amount_recovered_paise=5_000)]
    metrics = compute_metrics(outcomes)
    assert metrics["total_contacts"] == 1
    assert metrics["by_agent"]["rogue_agent"]["contacts"] == 1
