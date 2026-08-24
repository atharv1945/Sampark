"""PaymentRetryAgent — Arm A locked baseline configuration."""

from __future__ import annotations

import datetime as dt

from agents.payment_retry import PaymentRetryAgent
from agents.types import LedgerView


def test_selects_only_its_own_source(sample_view: LedgerView) -> None:
    actions = PaymentRetryAgent().select_actions(sample_view)
    assert {a.risk_id for a in actions} == {"fp-1", "fp-2"}


def test_one_action_per_eligible_risk_item(sample_view: LedgerView) -> None:
    actions = PaymentRetryAgent().select_actions(sample_view)
    assert len(actions) == 2
    assert len({a.risk_id for a in actions}) == 2  # no duplicates


def test_exact_baseline_configuration(sample_view: LedgerView, detected_at: dt.datetime) -> None:
    actions = {a.risk_id: a for a in PaymentRetryAgent().select_actions(sample_view)}
    action = actions["fp-1"]

    assert action.agent_id == "payment_retry_agent"
    assert action.customer_id == "cust-1"
    assert action.channel == "sms"
    assert action.incentive_bps == 0
    assert action.scheduled_at == detected_at + dt.timedelta(hours=2)


def test_deterministic_action_generation(sample_view: LedgerView) -> None:
    agent = PaymentRetryAgent()
    assert agent.select_actions(sample_view) == agent.select_actions(sample_view)
