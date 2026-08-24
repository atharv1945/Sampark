"""ReceivablesAgent — Arm A locked baseline configuration."""

from __future__ import annotations

import datetime as dt

from agents.receivables import ReceivablesAgent
from agents.types import LedgerView


def test_selects_only_its_own_source(sample_view: LedgerView) -> None:
    actions = ReceivablesAgent().select_actions(sample_view)
    assert {a.risk_id for a in actions} == {"oi-1"}


def test_one_action_per_eligible_risk_item(sample_view: LedgerView) -> None:
    actions = ReceivablesAgent().select_actions(sample_view)
    assert len(actions) == 1


def test_exact_baseline_configuration(sample_view: LedgerView, detected_at: dt.datetime) -> None:
    actions = {a.risk_id: a for a in ReceivablesAgent().select_actions(sample_view)}
    action = actions["oi-1"]

    assert action.agent_id == "receivables_agent"
    assert action.customer_id == "cust-4"
    assert action.channel == "voice"
    assert action.incentive_bps == 0
    assert action.scheduled_at == detected_at + dt.timedelta(hours=24)


def test_deterministic_action_generation(sample_view: LedgerView) -> None:
    agent = ReceivablesAgent()
    assert agent.select_actions(sample_view) == agent.select_actions(sample_view)
