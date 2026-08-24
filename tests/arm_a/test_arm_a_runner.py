"""Arm A end-to-end runner — one outcome per action, every reference
resolves against the real ledger. Runs the full Phase 1 generator via
sim.cli.build_dataset, same as tests/sim_generator/*, so fixtures are
module-scoped to avoid regenerating the dataset per test."""

from __future__ import annotations

import pytest

from agents import CartRecoveryAgent, MandateRecoveryAgent, PaymentRetryAgent, ReceivablesAgent
from sim.arm_a import _build_ledger_view, run_arm_a
from sim.cli import build_dataset

_SEED = 42


@pytest.fixture(scope="module")
def ledger():
    _, _, ledger = build_dataset(_SEED)
    return ledger


@pytest.fixture(scope="module")
def outcomes():
    return run_arm_a(_SEED)


def test_one_outcome_per_action(ledger, outcomes) -> None:
    view = _build_ledger_view(ledger)
    expected_action_count = sum(
        len(agent.select_actions(view))
        for agent in (
            PaymentRetryAgent(),
            CartRecoveryAgent(),
            MandateRecoveryAgent(),
            ReceivablesAgent(),
        )
    )
    assert expected_action_count > 0
    assert len(outcomes) == expected_action_count


def test_every_outcome_references_a_real_risk_item_and_customer(ledger, outcomes) -> None:
    valid_risk_ids = {r.risk_id for r in ledger.risk_items}
    valid_customer_ids = {c.customer_id for c in ledger.customers}
    for outcome in outcomes:
        assert outcome.risk_id in valid_risk_ids
        assert outcome.customer_id in valid_customer_ids


def test_every_outcome_agent_id_is_one_of_the_four_baseline_agents(outcomes) -> None:
    expected_agent_ids = {
        "payment_retry_agent",
        "cart_recovery_agent",
        "mandate_recovery_agent",
        "receivables_agent",
    }
    assert {o.agent_id for o in outcomes} <= expected_agent_ids


def test_no_risk_item_is_contacted_more_than_once(outcomes) -> None:
    risk_ids = [o.risk_id for o in outcomes]
    assert len(risk_ids) == len(set(risk_ids))
