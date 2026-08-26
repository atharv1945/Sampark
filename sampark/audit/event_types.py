"""Closed audit-event-type vocabulary — Phase 5A §2.

Twelve types, traced to spec §8.10 ("Every registration, revocation,
request, grant, denial, reservation, rollback and outcome is an
append-only, hash-chained event"). Nothing here is invented beyond that
list — see Phase 5A §2.1 for the four deliberate non-events
(FACT_UNAVAILABLE, decision.granted, system.error, per-hard-rule pass
events) and why each was rejected.

`agent.registered` / `agent.struck` / `agent.revoked` are U-8: audited
with append-after-write semantics, sampark/registry/** unmodified.

One event for a grant (U-4, approved): `grant.reserved`, not two
separate "reservation" and "grant" events — Design Lock's issuance
transaction makes them one atomic fact.
"""

from __future__ import annotations

AGENT_REGISTERED = "agent.registered"
AGENT_STRUCK = "agent.struck"
AGENT_REVOKED = "agent.revoked"

REQUEST_RECEIVED = "request.received"
REQUEST_DENIED_ON_SCOPE = "request.denied_on_scope"

DECISION_DENIED = "decision.denied"
DECISION_DEFERRED = "decision.deferred"

GRANT_RESERVED = "grant.reserved"
GRANT_EXECUTING = "grant.executing"
GRANT_CONFIRMED = "grant.confirmed"
GRANT_ROLLED_BACK = "grant.rolled_back"
GRANT_EXPIRED = "grant.expired"

EVENT_TYPES: frozenset[str] = frozenset(
    {
        AGENT_REGISTERED,
        AGENT_STRUCK,
        AGENT_REVOKED,
        REQUEST_RECEIVED,
        REQUEST_DENIED_ON_SCOPE,
        DECISION_DENIED,
        DECISION_DEFERRED,
        GRANT_RESERVED,
        GRANT_EXECUTING,
        GRANT_CONFIRMED,
        GRANT_ROLLED_BACK,
        GRANT_EXPIRED,
    }
)

# Event types whose payload always carries a requesting agent's signature
# (Phase 5A §3.2) — every type except the three system/registry-initiated
# ones with no signed request behind them.
SIGNED_EVENT_TYPES: frozenset[str] = EVENT_TYPES - frozenset(
    {AGENT_REGISTERED, AGENT_REVOKED, GRANT_EXPIRED}
)

# Legal successor types for one request/grant's lifecycle (Phase 5A §2.2).
# Used only by explain.py to validate a reconstructed timeline — never by
# the emitter, which never decides, only copies.
TERMINAL_EVENT_TYPES: frozenset[str] = frozenset(
    {
        REQUEST_DENIED_ON_SCOPE,
        DECISION_DENIED,
        GRANT_CONFIRMED,
        GRANT_ROLLED_BACK,
        GRANT_EXPIRED,
    }
)

# A fixed total order over the vocabulary, used ONLY to break ties among
# events that share the same `occurred_at` — common in practice, since
# Design Lock §9's real call sites stamp grant.executing and
# grant.confirmed with the IDENTICAL simulated instant
# (`at=grant.send_after`). This encodes no fact beyond what the
# vocabulary's own legal-transition rules (Design Lock §9, Phase 5A §2.2)
# already guarantee — it is a presentation tiebreak, shared by
# sampark.audit.explain (Python-side sort) and sampark.audit.store
# (SQL-side ORDER BY) so both layers agree on the same-instant order.
TYPE_ORDER: dict[str, int] = {
    REQUEST_RECEIVED: 0,
    REQUEST_DENIED_ON_SCOPE: 1,
    DECISION_DENIED: 1,
    DECISION_DEFERRED: 1,
    GRANT_RESERVED: 2,
    GRANT_EXECUTING: 3,
    GRANT_CONFIRMED: 4,
    GRANT_ROLLED_BACK: 4,
    GRANT_EXPIRED: 4,
}
