"""CartRecoveryAgent — Arm A canonical baseline configuration (locked).

Simulation baseline parameters only — not a merchant policy, not a
regulatory rule, not an API contract.
"""

from __future__ import annotations

from datetime import timedelta

from agents.base import RecoveryAgent
from agents.types import ContactAction, LedgerView

SOURCE = "abandoned_checkout"
CHANNEL = "whatsapp"
INCENTIVE_BPS = 500
SCHEDULE_OFFSET = timedelta(hours=6)
INTENT = "cart_recovery"


class CartRecoveryAgent(RecoveryAgent):
    agent_id = "cart_recovery_agent"
    source = SOURCE

    def select_actions(self, view: LedgerView) -> tuple[ContactAction, ...]:
        return tuple(
            ContactAction(
                agent_id=self.agent_id,
                risk_id=item.risk_id,
                customer_id=view.customer_id_by_risk_id[item.risk_id],
                channel=CHANNEL,
                intent=INTENT,
                incentive_bps=INCENTIVE_BPS,
                scheduled_at=item.detected_at + SCHEDULE_OFFSET,
            )
            for item in self.eligible_risk_items(view)
        )
