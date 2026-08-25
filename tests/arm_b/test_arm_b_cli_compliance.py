"""sim/arm_b_cli.py::_compute_compliance — Phase 4C hardening (W4).

The official evidence CLI's `_compute_compliance` must produce REAL,
measured compliance/fact-unavailable metrics from `result.outcomes` —
never a hand-waved "0 by construction". Exercised directly (without the
Postgres-only `main()` path) against a lightweight fake ArmBResult, so
this test needs no live database.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from agents.types import ContactOutcome
from sim.arm_b_cli import _compute_compliance
from sim.cli import build_dataset


@dataclass(frozen=True)
class _FakeResult:
    outcomes: tuple
    decisions: tuple


def test_compute_compliance_reports_real_fact_unavailable_counts():
    """Every contact carries an incentive_bps that may or may not be
    positive — `fact_unavailable.fraud_review` counts positive-bps
    contacts (Design Lock §4.3), so a handful of real outcomes from
    seed 42's own dataset must produce a genuinely nonzero count, not a
    hardcoded zero."""
    _population, _signals, ledger = build_dataset(42)
    sample_items = ledger.risk_items[:3]
    customer_ids = [ledger.risk_customer_map[item.risk_id] for item in sample_items]

    outcomes = tuple(
        ContactOutcome(
            outcome_id=f"cart_recovery_agent:{item.risk_id}",
            agent_id="cart_recovery_agent",
            customer_id=customer_id,
            risk_id=item.risk_id,
            channel="whatsapp",
            incentive_bps=500,
            contacted_at=dt.datetime(2025, 9, 10, 10, 0, tzinfo=dt.timezone.utc),
            recovered=False,
            amount_recovered_paise=0,
            incentive_paise=0,
        )
        for item, customer_id in zip(sample_items, customer_ids)
    )
    result = _FakeResult(outcomes=outcomes, decisions=())

    compliance = _compute_compliance(42, result)

    assert compliance["fact_unavailable_counts"]["fact_unavailable.fraud_review"] == 3
    assert compliance["fact_unavailable_counts"]["fact_unavailable.consent_scope"] == 3
    assert compliance["scope_violation_count"] == 0


def test_compute_compliance_returns_a_json_serializable_dict():
    import json

    _population, _signals, ledger = build_dataset(42)
    result = _FakeResult(outcomes=(), decisions=())
    compliance = _compute_compliance(42, result)
    json.dumps(compliance)  # must not raise — this dict is written straight into the result JSON
    assert "quiet_hour_violations" in compliance
    assert "interlock_dispute_open_violations" in compliance
