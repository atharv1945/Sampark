"""Candidate — the allocator's unit of work (Design Lock §6).

A Candidate wraps a registry-verified GrantRequest together with the
AUTHORITATIVE RiskItem it references (fetched from the ledger by
risk_id, never trusted from the request — CONTRACTS.md invariant) and
the scheduling/window state the allocator needs to rank and re-queue
it.

Phase 4 mediates only the candidate the agent actually requested:
`request.requested_channel` and `request.intent` are the agent's and
are never rewritten here. The allocator may defer (change `window_id` /
`proposed_send_after`) and may downgrade the incentive (a separate
parameter passed to scoring/issuance, not stored on Candidate) — it
never substitutes a channel or invents an intent.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date, datetime

from sampark.contracts import GrantRequest, RiskItem


@dataclass(frozen=True)
class Candidate:
    request: GrantRequest  # signed, registry-verified, unmodified
    risk_item: RiskItem  # AUTHORITATIVE, fetched from the ledger by risk_id
    customer_id: str  # from the ledger, not the request
    window_id: date  # IST calendar date, Design Lock §3.1
    proposed_send_after: datetime
    requested_incentive_ceiling_paise: int  # risk_item.amount_paise * requested_max_incentive_bps / 10_000
    windows_deferred: int = 0

    def __post_init__(self) -> None:
        if self.risk_item.risk_id != self.request.risk_id:
            raise ValueError(
                f"Candidate risk_item ({self.risk_item.risk_id!r}) does not match "
                f"request.risk_id ({self.request.risk_id!r})"
            )
        if self.request.requested_max_incentive_bps >= 10_000:
            # Design Lock I2: b < 10_000 is required for higher amount-at-risk
            # to never reduce the current-value score contribution.
            raise ValueError(
                f"requested_max_incentive_bps must be < 10_000, got "
                f"{self.request.requested_max_incentive_bps!r}"
            )

    def aged(self) -> "Candidate":
        """Increment the aging counter — Design Lock §7: once per window
        in which a DEFERRED decision was issued, for any reason."""
        return dataclasses.replace(self, windows_deferred=self.windows_deferred + 1)

    def rescheduled(self, window_id: date, send_after: datetime) -> "Candidate":
        """Re-queue this candidate into a later window after a defer."""
        return dataclasses.replace(
            self, window_id=window_id, proposed_send_after=send_after
        )


def build_candidate(
    request: GrantRequest,
    risk_item: RiskItem,
    customer_id: str,
    proposed_send_after: datetime,
) -> Candidate:
    """Construct a Candidate at its point of arrival — `proposed_send_after`
    is the agent's originally intended contact time (e.g. a Phase 2
    ContactAction.scheduled_at), BEFORE any quiet-hours adjustment. The
    hard-filter/allocator loop is what moves a candidate to a later
    window via `rescheduled()`; this constructor only ever produces the
    candidate's first, as-requested appearance."""
    from sampark.budget.windows import window_id_for  # local import: avoids a cycle

    ceiling = (risk_item.amount_paise * request.requested_max_incentive_bps) // 10_000
    return Candidate(
        request=request,
        risk_item=risk_item,
        customer_id=customer_id,
        window_id=window_id_for(proposed_send_after),
        proposed_send_after=proposed_send_after,
        requested_incentive_ceiling_paise=ceiling,
    )
