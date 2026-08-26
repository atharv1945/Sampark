"""AuditSink — the U-2 wiring layer, Phase 5A/5B's identified integration
point realized.

This is the ONLY new abstraction U-2 introduces. It exists so that
`sampark/mediation/service.py::mediate_window` (a Phase 4 file) and
`sim/arm_b.py` (a Phase 4 file) can call a handful of named methods at
points in their EXISTING control flow without importing psycopg, without
knowing anything about canonicalization or hash chains, and without
changing when or in what order those points already run. Passing
`audit_sink=None` (the default everywhere) makes every call site a no-op
branch — behaviourally identical to before U-2, which is how "zero Phase
4 regression" is verified (tests/audit/test_integration.py runs the same
fixture with and without a sink and diffs the non-audit outputs).

`AuditSink` is a structural `Protocol` (not an ABC) specifically so
`sampark/mediation/service.py` can reference the type under
`TYPE_CHECKING` only — Phase 4 gets no NEW RUNTIME import of
`sampark.audit`, only a type-checking-time one. `PostgresAuditSink` is
the one implementation Phase 5 ships; it owns a real
`psycopg.Connection` and does exactly two things per call: build the
event (via `sampark.audit.emit` — copy-only, never decides) and append
it (via `sampark.audit.chain.append` — the durable, hash-chained write).
It contains no allocation, ranking, admission, or budget logic of any
kind — it is not on the path that decides anything, only the path that
records what was already decided.

**`budget_window_id` / `claim_id` lookup.** `Grant`/`GrantIssued` do not
carry these (CONTRACTS.md deliberately excludes them from `Grant`). The
lookup below is READ-ONLY against Phase 4's existing, unmodified schema
— `grants.budget_window_id` and `contact_slot_claims.grant_id` both
already exist (Design Lock §1.4/§1.5); this adds no column, no table, no
write path. It runs once per GRANTED outcome, after `issue_grant` has
already committed (Phase 5A §8.2's Option B: append strictly follows a
committed business action).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol

import psycopg

from sampark.allocator.outcomes import AllocationOutcome, OutcomeKind
from sampark.audit import chain, emit
from sampark.contracts import Grant, GrantDecision, GrantRequest


class AuditSink(Protocol):
    """The structural contract `mediate_window`/`sim.arm_b` call
    against. `PostgresAuditSink` below is the one implementation; a test
    double satisfying this shape is equally valid (none is currently
    needed — the integration tests use the real thing against an
    isolated schema, per Phase 5B's established pattern)."""

    def record_request_received(self, request: GrantRequest) -> None: ...

    def record_denied_on_scope(
        self, decision: GrantDecision, request: GrantRequest, occurred_at: datetime
    ) -> None: ...

    def record_decision(self, outcome: AllocationOutcome, occurred_at: datetime) -> None: ...

    def record_grant_reserved(self, outcome: AllocationOutcome) -> None: ...

    def record_grant_executing(self, grant: Grant, request: GrantRequest, at: datetime) -> None: ...

    def record_grant_confirmed(
        self, grant: Grant, request: GrantRequest, at: datetime, actual_spend_paise: int
    ) -> None: ...

    def record_grant_rolled_back(self, grant: Grant, request: GrantRequest, at: datetime) -> None: ...

    def record_grant_expired(self, grant_id: uuid.UUID, request_id: uuid.UUID, at: datetime) -> None: ...


class MissingClaimError(RuntimeError):
    """A GRANTED outcome's grant_id has no matching contact_slot_claims
    row. Should be structurally impossible — sampark.budget.issuance's
    SERIALIZABLE transaction inserts the grant and its claim together
    (Design Lock §11, steps 5-6) — raised rather than silently emitting
    a fabricated budget_window_id/claim_id (Phase 5A: never fabricate)."""


class PostgresAuditSink:
    """Wraps `sampark.audit.emit` (event construction) +
    `sampark.audit.chain.append` (persistence) behind the `AuditSink`
    shape, against one real `psycopg.Connection`. Every `record_*` call
    is exactly: build the event, append it. No batching, no
    try/except-and-swallow — a failure here is meant to be visible
    (Phase 5A's failure semantics govern WHAT gets persisted when, not
    whether an error is hidden from the caller)."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def record_request_received(self, request: GrantRequest) -> None:
        chain.append(self._conn, emit.event_for_request_received(request))

    def record_denied_on_scope(
        self, decision: GrantDecision, request: GrantRequest, occurred_at: datetime
    ) -> None:
        chain.append(self._conn, emit.event_for_denied_on_scope(decision, request, occurred_at))

    def record_decision(self, outcome: AllocationOutcome, occurred_at: datetime) -> None:
        chain.append(self._conn, emit.event_for_decision(outcome, occurred_at))

    def record_grant_reserved(self, outcome: AllocationOutcome) -> None:
        if outcome.outcome_kind is not OutcomeKind.GRANTED:
            raise ValueError("record_grant_reserved requires a GRANTED outcome")
        grant = outcome.grant
        assert grant is not None
        budget_window_id, claim_id = self._lookup_grant_metadata(grant.grant_id)
        chain.append(self._conn, emit.event_for_grant_reserved(outcome, budget_window_id, claim_id))

    def record_grant_executing(self, grant: Grant, request: GrantRequest, at: datetime) -> None:
        chain.append(self._conn, emit.event_for_grant_executing(grant, request, at))

    def record_grant_confirmed(
        self, grant: Grant, request: GrantRequest, at: datetime, actual_spend_paise: int
    ) -> None:
        chain.append(self._conn, emit.event_for_grant_confirmed(grant, request, at, actual_spend_paise))

    def record_grant_rolled_back(self, grant: Grant, request: GrantRequest, at: datetime) -> None:
        chain.append(self._conn, emit.event_for_grant_rolled_back(grant, request, at))

    def record_grant_expired(self, grant_id: uuid.UUID, request_id: uuid.UUID, at: datetime) -> None:
        chain.append(self._conn, emit.event_for_grant_expired(grant_id, request_id, at))

    def _lookup_grant_metadata(self, grant_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT g.budget_window_id, c.claim_id FROM grants g "
                "JOIN contact_slot_claims c ON c.grant_id = g.grant_id "
                "WHERE g.grant_id = %s",
                (grant_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise MissingClaimError(
                f"grant_id={grant_id!r} has no contact_slot_claims row — "
                "sampark.budget.issuance's issuance invariant (grant + claim inserted "
                "together) appears violated"
            )
        return row[0], row[1]
