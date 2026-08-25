"""opt_out — TCCCPR 2018 opt-out honouring, Design Lock §2, §4.3.

Fact source: contact_states.optouts_by_channel, shape {channel: iso8601}.
This fact IS available in the Phase 1 schema — `optouts_by_channel = {}`
is a complete, true statement (this synthetic world has no opt-out
mechanism, so this customer has opted out of nothing), unlike
consent_scope's placeholder emptiness (sampark/policy/hard/consent_scope.py).
This rule therefore never reports FACT_UNAVAILABLE.

A channel present in the mapping is permanently inadmissible on that
channel — DENY, not DEFER: an opt-out does not expire on its own.
"""

from __future__ import annotations

from sampark.allocator.candidate import Candidate
from sampark.allocator.reason_codes import OPT_OUT_ACTIVE
from sampark.policy.types import HardVerdict, PolicyContext


def evaluate(candidate: Candidate, ctx: PolicyContext) -> HardVerdict:
    optouts = ctx.ledger.optouts_by_channel(candidate.customer_id)
    if candidate.request.requested_channel in optouts:
        return HardVerdict.deny(OPT_OUT_ACTIVE)
    return HardVerdict.admissible()
