"""RecoveryAgent — Arm A common interface (Phase 2).

Deliberately thin: eligibility is one shared filter (source match,
identical across all four agents, so implemented once here rather than
duplicated); action selection is agent-specific and abstract.

A RecoveryAgent must be a pure function of the LedgerView it is given:
no RNG, no wall-clock reads, no reference to another agent, to
sim.environment.Environment, or to Population.hidden_response. Nothing
in this module imports sim/ at all.
"""

from __future__ import annotations

import abc

from agents.types import ContactAction, LedgerView
from sampark.contracts import RiskItem


class RecoveryAgent(abc.ABC):
    agent_id: str
    source: str

    def eligible_risk_items(self, view: LedgerView) -> tuple[RiskItem, ...]:
        return view.risk_items_by_source.get(self.source, ())

    @abc.abstractmethod
    def select_actions(self, view: LedgerView) -> tuple[ContactAction, ...]:
        """Pure function of `view` and this agent's own fixed
        configuration. Must not use randomness or any state beyond
        `view`."""
        raise NotImplementedError
