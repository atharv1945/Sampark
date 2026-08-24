"""RecoveryAgent base interface — Arm A locked decision 2 (source filtering
only, no other eligibility state)."""

from __future__ import annotations

import pytest

from agents.base import RecoveryAgent
from agents.types import ContactAction, LedgerView


def test_recovery_agent_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        RecoveryAgent()  # type: ignore[abstract]


class _DummyAgent(RecoveryAgent):
    agent_id = "dummy_agent"
    source = "failed_payment"

    def select_actions(self, view: LedgerView) -> tuple[ContactAction, ...]:
        return ()


def test_eligible_risk_items_filters_by_source(sample_view: LedgerView) -> None:
    agent = _DummyAgent()
    items = agent.eligible_risk_items(sample_view)
    assert {item.risk_id for item in items} == {"fp-1", "fp-2"}


def test_eligible_risk_items_empty_for_unknown_source(sample_view: LedgerView) -> None:
    class _NoMatchAgent(RecoveryAgent):
        agent_id = "no_match_agent"
        source = "nonexistent_source"

        def select_actions(self, view: LedgerView) -> tuple[ContactAction, ...]:
            return ()

    agent = _NoMatchAgent()
    assert agent.eligible_risk_items(sample_view) == ()
