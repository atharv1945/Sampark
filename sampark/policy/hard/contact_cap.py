"""contact_cap — rolling 24h/7d contact caps, Design Lock §3.2-§3.4.

This is a best-effort PRE-CHECK against the mediation ledger's view of
the world at hard-filter time. The AUTHORITATIVE check happens again
inside the (human-owned) SERIALIZABLE issuance transaction, which is
what actually protects against a race — see Design Lock §11. A
candidate that clears this pre-check can still be denied by issuance;
a candidate denied here never reaches issuance at all.

Rolling-24h = 1 subsumes the one-claim-per-window rule (any two
contacts inside one IST calendar day are < 24h apart) — see Design
Lock §3.2. This rule does not separately check the window claim; that
is sampark/policy/hard/interlocks.py's active_grant_in_window row.
"""

from __future__ import annotations

from sampark.allocator.constants import CONTACT_CAP_7D, CONTACT_CAP_24H
from sampark.allocator.candidate import Candidate
from sampark.allocator.reason_codes import CONTACT_CAP_24H as REASON_24H
from sampark.allocator.reason_codes import CONTACT_CAP_7D as REASON_7D
from sampark.budget.windows import next_window_start
from sampark.policy.types import HardVerdict, PolicyContext


def evaluate(candidate: Candidate, ctx: PolicyContext) -> HardVerdict:
    c24, c7 = ctx.ledger.rolling_contact_counts(candidate.customer_id, ctx.decision_at)
    next_eligible = next_window_start(candidate.window_id)
    if c24 >= CONTACT_CAP_24H:
        return HardVerdict.defer(REASON_24H, next_eligible)
    if c7 >= CONTACT_CAP_7D:
        return HardVerdict.defer(REASON_7D, next_eligible)
    return HardVerdict.admissible()
