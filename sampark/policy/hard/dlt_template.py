"""dlt_template — TCCCPR 2018 DLT template registration, Design Lock §4.1.

A static, committed `(intent, channel) -> template_id` table. Fact
source is a config constant, not ledger state, so this rule never
reports FACT_UNAVAILABLE.

The four registered pairs are exactly the (intent, channel) combinations
the four Phase 2 agents use (agents/payment_retry.py, cart_recovery.py,
mandate_recovery.py, receivables.py) — Phase 4 must not deny the
batch's own well-behaved agents on a template gap it never populated.
A fifth, unregistered pair (e.g. a rogue or future agent's) is denied.
"""

from __future__ import annotations

from sampark.allocator.candidate import Candidate
from sampark.allocator.reason_codes import DLT_TEMPLATE_UNAVAILABLE
from sampark.policy.types import HardVerdict, PolicyContext

REGISTERED_TEMPLATES: dict[tuple[str, str], str] = {
    ("payment_retry", "sms"): "TXN_PAYMENT_RETRY_01",
    ("cart_recovery", "whatsapp"): "TXN_CART_RECOVERY_01",
    ("mandate_retry", "whatsapp"): "TXN_MANDATE_RETRY_01",
    ("receivables_followup", "voice"): "TXN_RECEIVABLES_01",
}


def evaluate(candidate: Candidate, ctx: PolicyContext) -> HardVerdict:
    key = (candidate.request.intent, candidate.request.requested_channel)
    if key not in REGISTERED_TEMPLATES:
        return HardVerdict.deny(DLT_TEMPLATE_UNAVAILABLE)
    return HardVerdict.admissible()
