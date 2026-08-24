"""CartRecoveryAgent — Arm A locked baseline configuration."""

from __future__ import annotations

import datetime as dt

from agents.cart_recovery import CartRecoveryAgent
from agents.types import LedgerView


def test_selects_only_its_own_source(sample_view: LedgerView) -> None:
    actions = CartRecoveryAgent().select_actions(sample_view)
    assert {a.risk_id for a in actions} == {"ac-1"}


def test_one_action_per_eligible_risk_item(sample_view: LedgerView) -> None:
    actions = CartRecoveryAgent().select_actions(sample_view)
    assert len(actions) == 1


def test_exact_baseline_configuration(sample_view: LedgerView, detected_at: dt.datetime) -> None:
    actions = {a.risk_id: a for a in CartRecoveryAgent().select_actions(sample_view)}
    action = actions["ac-1"]

    assert action.agent_id == "cart_recovery_agent"
    assert action.customer_id == "cust-2"
    assert action.channel == "whatsapp"
    assert action.incentive_bps == 500
    assert action.scheduled_at == detected_at + dt.timedelta(hours=6)


def test_deterministic_action_generation(sample_view: LedgerView) -> None:
    agent = CartRecoveryAgent()
    assert agent.select_actions(sample_view) == agent.select_actions(sample_view)
