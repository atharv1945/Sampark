"""Arm A — four thin, unmediated recovery agents (Phase 2).

Each agent reads only its own source-specific slice of the Phase 1
ledger (agents.types.LedgerView) and independently decides who to
contact. No agent receives another agent's actions or outcomes, any
shared contact/budget state, or Population.hidden_response — see
sim/environment.py for the one component allowed to touch any of that.
"""

from __future__ import annotations

from agents.base import RecoveryAgent
from agents.cart_recovery import CartRecoveryAgent
from agents.mandate_recovery import MandateRecoveryAgent
from agents.payment_retry import PaymentRetryAgent
from agents.receivables import ReceivablesAgent
from agents.types import ContactAction, ContactOutcome, LedgerView

__all__ = [
    "CartRecoveryAgent",
    "ContactAction",
    "ContactOutcome",
    "LedgerView",
    "MandateRecoveryAgent",
    "PaymentRetryAgent",
    "ReceivablesAgent",
    "RecoveryAgent",
]
