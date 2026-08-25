"""Grant lifecycle transitions — Design Lock §9.

Legal transitions, exactly:

    RESERVED  -> EXECUTING
    EXECUTING -> CONFIRMED
    RESERVED  -> ROLLED_BACK
    EXECUTING -> ROLLED_BACK
    RESERVED  -> EXPIRED

Every other transition is illegal and raises IllegalTransitionError.
Idempotency: execute/confirm/rollback are keyed by grant_id — see each
function's docstring for its specific no-op-on-repeat behaviour.

This module operates against sampark.budget.store's GrantIssuer/ledger
protocol surface (currently InMemoryMediationLedger). It does not
depend on the owner-authored issuance transaction; it is the layer
ABOVE issuance that a real Postgres-backed ledger would also need,
independently of how RESERVE itself is implemented.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sampark.budget.expiry import find_expired_grant_ids
from sampark.budget.store import InMemoryMediationLedger
from sampark.contracts import Grant, GrantState

_LEGAL_TRANSITIONS: dict[GrantState, frozenset[GrantState]] = {
    GrantState.RESERVED: frozenset({GrantState.EXECUTING, GrantState.ROLLED_BACK, GrantState.EXPIRED}),
    GrantState.EXECUTING: frozenset({GrantState.CONFIRMED, GrantState.ROLLED_BACK}),
    GrantState.CONFIRMED: frozenset(),
    GrantState.ROLLED_BACK: frozenset(),
    GrantState.EXPIRED: frozenset(),
}


class IllegalTransitionError(RuntimeError):
    pass


class UnknownGrantError(RuntimeError):
    pass


def _require_grant(ledger: InMemoryMediationLedger, grant_id: uuid.UUID):
    record = ledger.get_grant_by_grant_id(grant_id)
    if record is None:
        raise UnknownGrantError(f"unknown grant_id: {grant_id!r}")
    return record


def _transition(ledger: InMemoryMediationLedger, grant_id: uuid.UUID, to: GrantState, at: datetime) -> Grant:
    record = _require_grant(ledger, grant_id)
    current = record.grant.state
    if to not in _LEGAL_TRANSITIONS[current]:
        raise IllegalTransitionError(f"{current} -> {to} is not a legal transition (grant_id={grant_id!r})")
    return ledger.update_grant_state(grant_id, to, at)


def execute(ledger: InMemoryMediationLedger, grant_id: uuid.UUID, at: datetime) -> Grant:
    """RESERVED -> EXECUTING. Idempotent: calling this on an already-
    EXECUTING grant is a no-op (channel adapters key sends by grant_id;
    re-execution must not double-send — see agents/mediated.py)."""
    record = _require_grant(ledger, grant_id)
    if record.grant.state is GrantState.EXECUTING:
        return record.grant
    return _transition(ledger, grant_id, GrantState.EXECUTING, at)


def confirm(
    ledger: InMemoryMediationLedger, grant_id: uuid.UUID, at: datetime, actual_spend_paise: int
) -> Grant:
    """EXECUTING -> CONFIRMED. Settles the margin reservation to actual
    spend (Design Lock §2) and marks the risk item resolved so it drops
    out of open_candidates_for_customer's fatigue accounting."""
    record = _require_grant(ledger, grant_id)
    if record.grant.state is GrantState.CONFIRMED:
        return record.grant
    grant = _transition(ledger, grant_id, GrantState.CONFIRMED, at)
    ledger.settle_margin(record.request_id, actual_spend_paise)
    return grant


def rollback(ledger: InMemoryMediationLedger, grant_id: uuid.UUID, at: datetime) -> Grant:
    """RESERVED|EXECUTING -> ROLLED_BACK (provider failure). Releases the
    full margin reservation and, via the claim's active-index release,
    frees the contact slot for a retry under the same grant_id or a
    fresh grant for the same (customer, window)."""
    record = _require_grant(ledger, grant_id)
    if record.grant.state is GrantState.ROLLED_BACK:
        return record.grant
    grant = _transition(ledger, grant_id, GrantState.ROLLED_BACK, at)
    ledger.release_margin(record.request_id)
    return grant


def expire(ledger: InMemoryMediationLedger, grant_id: uuid.UUID, at: datetime) -> Grant:
    """RESERVED -> EXPIRED (TTL sweep, never executed). Releases the
    full margin reservation and the contact slot, same as rollback."""
    record = _require_grant(ledger, grant_id)
    if record.grant.state is GrantState.EXPIRED:
        return record.grant
    grant = _transition(ledger, grant_id, GrantState.EXPIRED, at)
    ledger.release_margin(record.request_id)
    return grant


@dataclass(frozen=True)
class ExpirySweepResult:
    expired_grant_ids: tuple[uuid.UUID, ...]


def sweep_expired(ledger: InMemoryMediationLedger, now: datetime) -> ExpirySweepResult:
    """Design Lock §9: past-expires_at RESERVED grants -> EXPIRED,
    releasing both resources. `now` is passed explicitly — this module
    never reads a wall clock (Design Lock §3.5)."""
    expired = tuple(expire(ledger, grant_id, now).grant_id for grant_id in find_expired_grant_ids(ledger, now))
    return ExpirySweepResult(expired_grant_ids=expired)
