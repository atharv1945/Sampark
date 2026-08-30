"""The append-only, hash-chained store — Phase 5A §5, §6, §7, §8.

`audit_events` (sampark/schema.sql, human-owned) already has the seven
base columns this module needs: event_id, event_type, occurred_at,
prev_hash, agent_signature, reason_code, payload. This module adds NO
schema change of its own — the `seq` column, the `UNIQUE(prev_hash)`
index, and the append-only triggers are U-1, an owner-authored migration
whose exact DDL lives in sampark/audit/schema_proposal.sql. **U-1 has
been owner-applied to the live database and verified** (seq, both unique
indexes, and all three triggers confirmed present on `public.audit_events`)
— but sampark/schema.sql itself (the human-owned, fresh-checkout-authoritative
file) has not yet been updated to include it, so a database built from
schema.sql alone still lacks the migration. Every function here checks
for that migration at the point it needs it and fails LOUDLY and
specifically — `MissingSchemaMigrationError` — rather than silently
degrading to a weaker, unindexed ordering. "Report the missing schema
dependency, don't silently skip the exit criterion." Folding U-1 into
sampark/schema.sql itself is an owner action (CLAUDE.md §3 item 1); this
module does not and must not do it.

Concurrency (Phase 5A §7): one Postgres advisory transaction lock
(`pg_advisory_xact_lock`), held for the duration of one append. Appenders
queue; none abort. `UNIQUE(prev_hash)` (live-applied) is the structural
backstop if the lock is ever bypassed — the lock is the performance path,
the index is the correctness guarantee. No Redis, no SERIALIZABLE here
(Phase 5A §7.2 explains why both are the wrong tool for a read-head-then-
insert protocol).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Sequence

import psycopg
from psycopg.rows import dict_row

from sampark.audit.canonical import GENESIS_HASH, hash_event
from sampark.contracts import AuditEvent

# Frozen namespace for every deterministic audit event_id (Phase 5A §3.1).
# uuid5 only — uuid4 is banned on this path for the same reason Design
# Lock §16 bans it on the Phase 4 decision path: the event_id IS the
# idempotency key (§8.6), and a random id could never be re-derived by a
# retry to detect "this already happened."
NS_AUDIT = uuid.UUID("3f5a9c1e-7b6d-4e2a-9c1e-5a3f9c1e7b6d")

# Frozen bigint key for the chain-wide advisory lock. Derived once and
# hard-coded (not recomputed at import time) so it is visibly stable
# across processes without depending on hash() determinism.
CHAIN_ADVISORY_LOCK_KEY = int.from_bytes(hashlib.sha256(b"sampark.audit.chain").digest()[:8], "big", signed=True)

# Sentinel prev_hash a caller-built (not-yet-appended) AuditEvent must
# carry. append() asserts this and replaces it under the lock — it exists
# so a caller can never accidentally pre-compute (and therefore forge)
# its own position in the chain.
PENDING_PREV_HASH = "PENDING"


def event_id_for(*parts: str) -> uuid.UUID:
    """uuid5(NS_AUDIT, "part1:part2:...") — the ONE event_id derivation
    used by every emitter function (Phase 5A §3.1). Never uuid4."""
    return uuid.uuid5(NS_AUDIT, ":".join(parts))


class MissingSchemaMigrationError(RuntimeError):
    """The Phase 5 audit schema migration (seq column, UNIQUE(prev_hash),
    append-only triggers — U-1, approved) has not been applied to this
    database. This is reported, never silently worked around: without
    `seq` there is no independent, indexed total order to check hash
    linkage against, and without `UNIQUE(prev_hash)` fork-prevention
    rests on the advisory lock alone. See
    sampark/audit/schema_proposal.sql for the exact DDL to apply."""


class ChainForkError(RuntimeError):
    """Two events claim the same prev_hash — the chain has forked. This
    should be structurally impossible once UNIQUE(prev_hash) is applied;
    raised here as a defensive check for a database still missing it."""


@dataclass(frozen=True)
class Appended:
    event: AuditEvent
    hash: str


@dataclass(frozen=True)
class AlreadyAppended:
    event: AuditEvent


AppendResult = Appended | AlreadyAppended


def _has_seq_column(conn: psycopg.Connection) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'audit_events' AND column_name = 'seq'"
        )
        return cur.fetchone() is not None


def require_migration(conn: psycopg.Connection) -> None:
    """Raises MissingSchemaMigrationError if the U-1 migration has not
    been applied. Call before any operation that needs `seq` ordering."""
    if not _has_seq_column(conn):
        raise MissingSchemaMigrationError(
            "audit_events.seq does not exist. Apply the Phase 5 audit schema "
            "migration (sampark/audit/schema_proposal.sql — U-1, owner-approved "
            "but not yet applied) before using sampark.audit.chain against this "
            "database."
        )


def row_to_event(row: dict[str, Any]) -> AuditEvent:
    payload = row["payload"]
    if isinstance(payload, str):  # defensive: some psycopg configs return raw text for jsonb
        payload = json.loads(payload)
    return AuditEvent(
        event_id=row["event_id"],
        event_type=row["event_type"],
        occurred_at=row["occurred_at"],
        prev_hash=row["prev_hash"],
        agent_signature=row["agent_signature"],
        reason_code=row["reason_code"],
        payload=payload,
    )


def head(conn: psycopg.Connection) -> AuditEvent | None:
    """The most recently appended event, or None if the chain is empty.
    Requires the seq migration (O(1) lookup via the index it adds)."""
    require_migration(conn)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT event_id, event_type, occurred_at, prev_hash, agent_signature, "
            "reason_code, payload FROM audit_events ORDER BY seq DESC LIMIT 1"
        )
        row = cur.fetchone()
    return None if row is None else row_to_event(row)


def _probe(conn: psycopg.Connection, event_id: uuid.UUID) -> AuditEvent | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT event_id, event_type, occurred_at, prev_hash, agent_signature, "
            "reason_code, payload FROM audit_events WHERE event_id = %s",
            (event_id,),
        )
        row = cur.fetchone()
    return None if row is None else row_to_event(row)


def append(conn: psycopg.Connection, event: AuditEvent) -> AppendResult:
    """The append protocol (Phase 5A §7.1):

        BEGIN
            pg_advisory_xact_lock(CHAIN_ADVISORY_LOCK_KEY)
            probe event_id -> if present, return AlreadyAppended
            read head by seq DESC
            derive prev_hash (GENESIS if no head)
            INSERT
        COMMIT

    `event.prev_hash` MUST be PENDING_PREV_HASH — this function derives
    the real value under the lock and replaces it; a caller that has
    already computed a prev_hash has computed a value that can never be
    trusted (Phase 5A §7.1's whole point is that only the position
    under the lock is authoritative). The advisory lock is a
    transaction-scoped lock (`_xact_lock`): it is released automatically
    at COMMIT or ROLLBACK, so a crash mid-append cannot leave the chain
    wedged.

    Idempotent: if `event.event_id` already exists (a retry re-deriving
    the same deterministic id, Phase 5A §3.1/§8.6), this is a no-op that
    returns AlreadyAppended with the EXISTING row's fields (which may
    differ in `occurred_at`-precision-irrelevant ways from the retry's
    freshly-built draft, but must describe the same fact) — the chain
    never advances twice for one logical event.
    """
    if event.prev_hash != PENDING_PREV_HASH:
        raise ValueError(
            f"append() requires event.prev_hash == {PENDING_PREV_HASH!r}; got {event.prev_hash!r}. "
            "Build the draft event without knowing its chain position — append() derives it under the lock."
        )
    require_migration(conn)

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (CHAIN_ADVISORY_LOCK_KEY,))

        existing = _probe(conn, event.event_id)
        if existing is not None:
            return AlreadyAppended(event=existing)

        head_event = head(conn)
        prev_hash = GENESIS_HASH if head_event is None else hash_event(head_event)

        # Defensive fork check (Phase 5A §7.2): under the advisory lock this
        # can never fire in normal operation — no other appender can have
        # raced to claim this exact prev_hash while we hold the lock. It
        # exists for a database still missing the U-1 UNIQUE(prev_hash)
        # index, where it is the ONLY thing standing between a bug
        # elsewhere and a silent fork.
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM audit_events WHERE prev_hash = %s", (prev_hash,))
            if cur.fetchone() is not None:
                raise ChainForkError(f"an event with prev_hash={prev_hash!r} already exists — refusing to fork")

        final_event = event.model_copy(update={"prev_hash": prev_hash})
        digest = hash_event(final_event)  # computed before INSERT — never trust a stored value

        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO audit_events "
                "(event_id, event_type, occurred_at, prev_hash, agent_signature, reason_code, payload) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    final_event.event_id,
                    final_event.event_type,
                    final_event.occurred_at,
                    final_event.prev_hash,
                    final_event.agent_signature,
                    final_event.reason_code,
                    json.dumps(final_event.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
                ),
            )

    return Appended(event=final_event, hash=digest)


def all_events_ordered(conn: psycopg.Connection, batch_size: int = 5000) -> "Sequence[AuditEvent]":
    """Every event in chain (seq) order, fetched in batches rather than
    materializing a single huge result set (Phase 5A §11.5's ~10^5-row
    volume note). Returns a list — callers needing true streaming should
    use export_jsonl(), which never materializes.

    A named (server-side) cursor requires an open transaction — psycopg
    raises `NoActiveSqlTransaction` otherwise, including under plain
    `autocommit=True` (verified against a real connection while building
    this). `with conn.transaction():` opens one explicitly regardless of
    the connection's autocommit setting, so this works for both a
    dedicated read connection and one shared with a caller already
    holding autocommit on."""
    require_migration(conn)
    events: list[AuditEvent] = []
    with conn.transaction():
        with conn.cursor(name="sampark_audit_all_events", row_factory=dict_row) as cur:
            cur.itersize = batch_size
            cur.execute(
                "SELECT event_id, event_type, occurred_at, prev_hash, agent_signature, "
                "reason_code, payload FROM audit_events ORDER BY seq ASC"
            )
            for row in cur:
                events.append(row_to_event(row))
    return events


@dataclass(frozen=True)
class Divergence:
    seq_index: int  # 0-based position in iteration order
    event_id: uuid.UUID
    expected_prev_hash: str
    actual_prev_hash: str


@dataclass(frozen=True)
class VerificationReport:
    event_count: int
    genesis_ok: bool
    linkage_ok: bool
    first_divergence: Divergence | None
    head_hash: str | None
    missing_grant_reservations: tuple[uuid.UUID, ...]
    # Phase 7 (spec §8.9), additive: every attribution_credits row must
    # have a matching recovery.credited event. Empty tuple both when
    # nothing is missing AND when attribution_credits does not exist on
    # this connection (the schema proposal has not been applied — never
    # a verification failure by itself; see _missing_credit_reconciliations).
    missing_credit_reconciliations: tuple[uuid.UUID, ...] = ()

    @property
    def ok(self) -> bool:
        return (
            self.linkage_ok
            and self.genesis_ok
            and not self.missing_grant_reservations
            and not self.missing_credit_reconciliations
        )

    def summary(self) -> str:
        lines = [
            f"events: {self.event_count}",
            f"genesis_ok: {self.genesis_ok}",
            f"linkage_ok: {self.linkage_ok}",
            f"head_hash: {self.head_hash}",
        ]
        if self.first_divergence is not None:
            d = self.first_divergence
            lines.append(
                f"FIRST DIVERGENCE at index {d.seq_index}, event_id={d.event_id}: "
                f"expected prev_hash={d.expected_prev_hash}, actual={d.actual_prev_hash}"
            )
        if self.missing_grant_reservations:
            lines.append(
                f"MISSING grant.reserved events for grant_ids: "
                f"{[str(g) for g in self.missing_grant_reservations]}"
            )
        if self.missing_credit_reconciliations:
            lines.append(
                f"MISSING recovery.credited events for grant_ids: "
                f"{[str(g) for g in self.missing_credit_reconciliations]}"
            )
        lines.append(f"VALID: {self.ok}")
        return "\n".join(lines)


def _missing_grant_reservations(conn: psycopg.Connection) -> tuple[uuid.UUID, ...]:
    """Phase 5A §8.2's reconciliation query, in-database and streaming:
    every grants row must have a corresponding grant.reserved audit
    event keyed by grant_id. A non-empty result is a HARD verification
    failure — the exit criterion is violated the moment a grant exists
    that the log does not explain."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT g.grant_id FROM grants g "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM audit_events a "
            "  WHERE a.event_type = 'grant.reserved' "
            "    AND a.payload ->> 'grant_id' = g.grant_id::text"
            ") ORDER BY g.grant_id"
        )
        return tuple(row[0] for row in cur.fetchall())


def _missing_credit_reconciliations(conn: psycopg.Connection) -> tuple[uuid.UUID, ...]:
    """Phase 7 (spec §8.9), mirroring `_missing_grant_reservations`'s exact
    pattern: every `attribution_credits` row must have a corresponding
    `recovery.credited` event keyed by grant_id. Returns `()` (never a
    failure) if `attribution_credits` does not exist at all on this
    connection's search_path — the Phase 7 schema proposal is an owner
    decision (CLAUDE.md §3) and a fresh checkout without it applied must
    not have its audit verification fail because of a table that was
    never supposed to exist there yet."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('attribution_credits')")
        if cur.fetchone()[0] is None:
            return ()
        cur.execute(
            "SELECT c.grant_id FROM attribution_credits c "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM audit_events a "
            "  WHERE a.event_type = 'recovery.credited' "
            "    AND a.payload ->> 'grant_id' = c.grant_id::text"
            ") ORDER BY c.grant_id"
        )
        return tuple(row[0] for row in cur.fetchall())


def verify_chain(conn: psycopg.Connection) -> VerificationReport:
    """The verification algorithm (Phase 5A §5.3): recompute every hash,
    check linkage, check genesis, check the grant reconciliation (and,
    Phase 7, the attribution-credit reconciliation). NEVER writes
    anything — a verification failure is reported, never appended to the
    chain it is inspecting (Phase 5A §6.2's failure-mode boundary)."""
    require_migration(conn)
    events = all_events_ordered(conn)

    genesis_ok = True
    linkage_ok = True
    first_divergence: Divergence | None = None
    expected_prev = GENESIS_HASH
    last_hash: str | None = None

    for i, event in enumerate(events):
        if i == 0 and event.prev_hash != GENESIS_HASH:
            genesis_ok = False
        if event.prev_hash != expected_prev:
            linkage_ok = False
            if first_divergence is None:
                first_divergence = Divergence(
                    seq_index=i, event_id=event.event_id,
                    expected_prev_hash=expected_prev, actual_prev_hash=event.prev_hash,
                )
        digest = hash_event(event)
        expected_prev = digest
        last_hash = digest

    missing = _missing_grant_reservations(conn)
    missing_credits = _missing_credit_reconciliations(conn)

    return VerificationReport(
        event_count=len(events), genesis_ok=genesis_ok, linkage_ok=linkage_ok,
        first_divergence=first_divergence, head_hash=last_hash,
        missing_grant_reservations=missing,
        missing_credit_reconciliations=missing_credits,
    )
