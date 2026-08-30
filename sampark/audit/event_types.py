"""Closed audit-event-type vocabulary — Phase 5A §2, extended Phase 7.

Twelve types at Phase 5/6 close, traced to spec §8.10 ("Every
registration, revocation, request, grant, denial, reservation, rollback
and outcome is an append-only, hash-chained event"). See Phase 5A §2.1
for the four deliberate non-events (FACT_UNAVAILABLE, decision.granted,
system.error, per-hard-rule pass events) and why each was rejected.

`agent.registered` / `agent.struck` / `agent.revoked` are U-8: audited
with append-after-write semantics, sampark/registry/** unmodified.

One event for a grant (U-4, approved): `grant.reserved`, not two
separate "reservation" and "grant" events — Design Lock's issuance
transaction makes them one atomic fact.

**Phase 7 (spec §8.9) adds three types — an approved, additive extension
of this closed vocabulary, not a reinterpretation of it:**

    holdout.assigned    — ONE digest event per run (never one per
                           customer): the full held-out set is
                           reproducible from `sim.holdout.assign()` alone,
                           so a SHA-256 digest is equally tamper-evident
                           while keeping the chain O(1) instead of
                           O(customers). Unsigned (no agent behind an
                           assignment decision).
    contact.opt_out      — one per CONFIRMED contact whose
                           `ContactOutcome.opt_out` is True. Signed with
                           the originating request's signature.
    recovery.credited    — one per `attribution_credits` row (spec
                           §8.9's "signed against that agent's
                           identity"). Signed with the originating
                           request's signature.

Neither `contact.opt_out` nor `recovery.credited` joins
`TERMINAL_EVENT_TYPES` or is treated as part of the grant lifecycle by
`sampark.audit.explain` — both follow a terminal event (`grant.confirmed`)
and are surfaced as separate OPTIONAL fields, so Phase 5's lifecycle
validation semantics are unchanged (Phase 7 design lock, audit design §H.3).

Natural recovery itself is NOT an event type: the audit log is SAMPARK's
DECISION record, and a natural outcome is something the WORLD did to an
item SAMPARK never touched — it reaches the log only as `natural_rate_bps`
on a `recovery.credited` payload, never as its own event (design lock §2.18).
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

# --- Phase 7 additions (spec §8.9) ---
HOLDOUT_ASSIGNED = "holdout.assigned"
CONTACT_OPT_OUT = "contact.opt_out"
RECOVERY_CREDITED = "recovery.credited"

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
        HOLDOUT_ASSIGNED,
        CONTACT_OPT_OUT,
        RECOVERY_CREDITED,
    }
)

# Event types whose payload always carries a requesting agent's signature
# (Phase 5A §3.2) — every type except the three system/registry-initiated
# ones with no signed request behind them, plus holdout.assigned (Phase 7
# — no agent behind an assignment decision).
SIGNED_EVENT_TYPES: frozenset[str] = EVENT_TYPES - frozenset(
    {AGENT_REGISTERED, AGENT_REVOKED, GRANT_EXPIRED, HOLDOUT_ASSIGNED}
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
    HOLDOUT_ASSIGNED: -1,  # Phase 7 — precedes everything else at the same instant
    REQUEST_RECEIVED: 0,
    REQUEST_DENIED_ON_SCOPE: 1,
    DECISION_DENIED: 1,
    DECISION_DEFERRED: 1,
    GRANT_RESERVED: 2,
    GRANT_EXECUTING: 3,
    GRANT_CONFIRMED: 4,
    GRANT_ROLLED_BACK: 4,
    GRANT_EXPIRED: 4,
    CONTACT_OPT_OUT: 5,  # Phase 7 — follows grant.confirmed
    RECOVERY_CREDITED: 5,  # Phase 7 — follows grant.confirmed
}
