"""PaymentRetryAgent — Arm A canonical baseline configuration (locked).

Simulation baseline parameters only — not a merchant policy, not a
regulatory rule, not an API contract.
"""

from __future__ import annotations

from datetime import timedelta

from agents.base import RecoveryAgent
from agents.types import ContactAction, LedgerView

SOURCE = "failed_payment"
CHANNEL = "sms"
INCENTIVE_BPS = 0
SCHEDULE_OFFSET = timedelta(hours=2)
INTENT = "payment_retry"


class PaymentRetryAgent(RecoveryAgent):
    agent_id = "payment_retry_agent"
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
