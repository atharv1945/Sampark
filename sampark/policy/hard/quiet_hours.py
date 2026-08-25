"""quiet_hours — TCCCPR 2018 21:00-09:00 IST blackout, Design Lock §3.7.

DEFERRED, never "grant now with a later send_after": admitting a 22:00
candidate now, with send_after pinned to 09:00, would let it claim
tomorrow's slot ahead of a better candidate that legitimately arrives
at 06:00. Deferral makes it re-compete in its actual eligible window.
"""

from __future__ import annotations

from sampark.allocator.candidate import Candidate
from sampark.allocator.reason_codes import QUIET_HOURS
from sampark.budget.windows import is_quiet_hours, next_quiet_hours_boundary
from sampark.policy.types import HardVerdict, PolicyContext


def evaluate(candidate: Candidate, ctx: PolicyContext) -> HardVerdict:
    if is_quiet_hours(candidate.proposed_send_after):
        next_eligible = next_quiet_hours_boundary(candidate.proposed_send_after)
        return HardVerdict.defer(QUIET_HOURS, next_eligible)
    return HardVerdict.admissible()
