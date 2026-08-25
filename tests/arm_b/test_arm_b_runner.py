"""Arm B runner — determinism and agent-decision parity with Arm A.

Mirrors tests/arm_a/test_arm_a_reproducibility.py's structure. Each of
run_pair/other_seed_outcomes is a full ~1-minute simulation run — this
module is the most expensive in the Phase 4 suite by design (it is
exercising the real end-to-end pipeline, not a mock).
"""

from __future__ import annotations

import json

import pytest

from agents import CartRecoveryAgent, MandateRecoveryAgent, PaymentRetryAgent, ReceivablesAgent
from sim.arm_a import _build_ledger_view
from sim.arm_b import run_arm_b
from sim.arm_b import _build_ledger_view as arm_b_build_ledger_view
from sim.cli import build_dataset
from sim.metrics import compute_metrics

_SEED = 42
_OTHER_SEED = 7


@pytest.fixture(scope="module")
def run_pair():
    return run_arm_b(_SEED), run_arm_b(_SEED)


@pytest.fixture(scope="module")
def other_seed_result():
    return run_arm_b(_OTHER_SEED)


def test_same_seed_produces_identical_outcomes(run_pair) -> None:
    result_a, result_b = run_pair
    assert result_a.outcomes == result_b.outcomes


def test_same_seed_produces_identical_decisions(run_pair) -> None:
    result_a, result_b = run_pair
    assert result_a.decisions == result_b.decisions


def test_same_seed_produces_byte_identical_metrics_json(run_pair) -> None:
    result_a, result_b = run_pair
    metrics_a = compute_metrics(result_a.outcomes)
    metrics_b = compute_metrics(result_b.outcomes)
    assert json.dumps(metrics_a, sort_keys=True) == json.dumps(metrics_b, sort_keys=True)


def test_different_seed_produces_different_metrics(run_pair, other_seed_result) -> None:
    result_a, _ = run_pair
    metrics_a = compute_metrics(result_a.outcomes)
    metrics_other = compute_metrics(other_seed_result.outcomes)
    assert metrics_a != metrics_other


def test_arm_b_and_arm_a_ledger_views_are_identical() -> None:
    """sim/arm_b.py duplicates sim/arm_a.py's _build_ledger_view (frozen,
    not exported) — this proves the duplicate has not drifted."""
    _, _, ledger = build_dataset(_SEED)
    view_a = _build_ledger_view(ledger)
    view_b = arm_b_build_ledger_view(ledger)
    assert view_a.customers_by_id == view_b.customers_by_id
    assert view_a.risk_items_by_source == view_b.risk_items_by_source
    assert view_a.customer_id_by_risk_id == view_b.customer_id_by_risk_id


def test_arm_b_agent_actions_are_identical_to_arm_a_before_mediation() -> None:
    """Design Lock §13.1: 'the four agent classes and their select_actions
    output are byte-identical across arms.' Mediation is the ONLY
    experimental change — this proves the pre-mediation input is
    unchanged, without re-running either full simulation."""
    _, _, ledger = build_dataset(_SEED)
    view = _build_ledger_view(ledger)
    agents = (PaymentRetryAgent(), CartRecoveryAgent(), MandateRecoveryAgent(), ReceivablesAgent())
    actions_run_1 = tuple(a for agent in agents for a in agent.select_actions(view))
    actions_run_2 = tuple(a for agent in agents for a in agent.select_actions(view))
    assert actions_run_1 == actions_run_2
    assert len(actions_run_1) == 20_000  # one per risk item, spec §11
