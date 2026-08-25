"""TTL expiry query — Design Lock §9, §10.3.

Pure read: which RESERVED grants have passed their `expires_at` as of
`now` (passed explicitly — no wall clock, Design Lock §3.5). The
MUTATION (RESERVED -> EXPIRED, releasing both margin reservations and
the contact claim) is sampark.mediation.lifecycle.sweep_expired's job,
which calls this query and then transitions each grant through the
same legal-transition path a live TTL sweeper would use — one state
machine, whether the trigger is a background sweep or (Design Lock
§10.3) a Redis TTL index used only to avoid a full table scan.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sampark.budget.store import InMemoryMediationLedger
from sampark.contracts import GrantState


def find_expired_grant_ids(ledger: InMemoryMediationLedger, now: datetime) -> tuple[uuid.UUID, ...]:
    return tuple(
        record.grant.grant_id
        for record in ledger._grants_by_grant_id.values()  # noqa: SLF001 — query is ledger-internal
        if record.grant.state is GrantState.RESERVED and record.grant.expires_at < now
    )
