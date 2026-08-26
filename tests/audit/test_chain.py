"""T-6..T-15 — hash chain construction, database enforcement, concurrency
(Phase 5A §5, §6, §7).

Post-U-1 (Phase 5B): the audit schema migration (`sampark/audit/
schema_proposal.sql`) is owner-applied to the live database — `seq`,
`UNIQUE(prev_hash)`, and the append-only triggers all exist. Every test
here runs against an ISOLATED per-test schema (see conftest.py's
`pg_conn`/`_audit_schema`) with an exact structural copy of that
migration, so `MissingSchemaMigrationError` is no longer an expected
outcome anywhere in this file — the graceful "report the blocker instead
of asserting" scaffolding the pre-U-1 version of this file needed has
been removed; a `MissingSchemaMigrationError` here now would be a
genuine fixture bug (the isolated schema failing to include the U-1
DDL), not an environmental condition to route around.
"""

from __future__ import annotations

import datetime as dt
import threading
import uuid

import psycopg
import pytest

from sampark.audit.canonical import hash_event
from sampark.audit.chain import (
    GENESIS_HASH,
    PENDING_PREV_HASH,
    AlreadyAppended,
    Appended,
    append,
    head,
    verify_chain,
)
from sampark.contracts import AuditEvent

pytestmark = pytest.mark.postgres


def _draft_event(**overrides) -> AuditEvent:
    fields = dict(
        event_id=uuid.uuid4(),
        event_type="request.received",
        occurred_at=dt.datetime(2025, 9, 10, 9, 0, 0, tzinfo=dt.timezone.utc),
        prev_hash=PENDING_PREV_HASH,
        agent_signature="sig-" + uuid.uuid4().hex[:8],
        reason_code=None,
        payload={"v": 1, "request_id": f"audit-chain-test-{uuid.uuid4().hex[:12]}"},
    )
    fields.update(overrides)
    return AuditEvent(**fields)


def _raw_insert(conn: psycopg.Connection, event_id: uuid.UUID, prev_hash: str) -> None:
    """Bypasses append() entirely — for tests proving what the DATABASE
    itself rejects, independent of application-level guards."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO audit_events (event_id, event_type, occurred_at, prev_hash, "
            "agent_signature, reason_code, payload) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (event_id, "request.received", dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc),
             prev_hash, None, None, '{"v": 1}'),
        )


def test_append_succeeds_against_the_applied_migration(pg_conn):
    # Smoke test — U-1 is applied (owner-verified: seq, both unique
    # indexes, all three triggers present on the live database; this
    # test's isolated schema is an exact structural copy). A fresh
    # schema's chain starts empty, so this is also, trivially, a first
    # genesis append.
    draft = _draft_event()
    result = append(pg_conn, draft)
    assert isinstance(result, Appended)
    assert result.event.prev_hash == GENESIS_HASH
    assert result.hash == hash_event(result.event)


def test_genesis_event_uses_genesis_prev_hash(pg_conn):
    # T-6 — every isolated-schema chain starts empty (module docstring's
    # "useful side effect"), so `head()` is always None here and the
    # first append is always genesis-rooted. No pre-U-1 "is there
    # already a head" branching needed anymore.
    assert head(pg_conn) is None
    draft = _draft_event()
    result = append(pg_conn, draft)
    assert result.event.prev_hash == GENESIS_HASH
    assert result.hash == hash_event(result.event)


def test_chain_linkage_across_n_events(pg_conn):
    # T-7
    n = 25
    appended: list[Appended] = []
    for i in range(n):
        draft = _draft_event(payload={"v": 1, "request_id": f"linkage-test-{i:03d}-{uuid.uuid4().hex[:8]}"})
        result = append(pg_conn, draft)
        assert isinstance(result, Appended)
        appended.append(result)

    assert appended[0].event.prev_hash == GENESIS_HASH
    for prev, current in zip(appended, appended[1:]):
        assert current.event.prev_hash == prev.hash
        assert hash_event(current.event) == current.hash


def test_wrong_prev_hash_is_rejected_by_append(pg_conn):
    # T-9: append() itself REJECTS a caller who has already computed a
    # prev_hash — only PENDING_PREV_HASH is accepted, precisely so a
    # caller can never forge its own chain position. Independent of
    # database state.
    bad_draft = _draft_event(prev_hash="1" * 64)
    with pytest.raises(ValueError):
        append(pg_conn, bad_draft)


def test_long_chain_verification(pg_conn):
    # T-10 (scaled down from 100,000 for test runtime — the algorithm's
    # cost is linear in event count).
    n = 200
    for i in range(n):
        draft = _draft_event(payload={"v": 1, "request_id": f"longchain-{i:04d}-{uuid.uuid4().hex[:8]}"})
        result = append(pg_conn, draft)
        assert isinstance(result, Appended)

    report = verify_chain(pg_conn)
    assert report.linkage_ok
    assert report.genesis_ok
    assert report.event_count == n
    # Deliberately NOT asserting report.ok / missing_grant_reservations
    # here — that reconciliation joins against the REAL, shared
    # public.grants (this test's isolated schema has no grants table of
    # its own), which may legitimately contain live evidence-run grants
    # with no audit coverage yet (U-2 is not wired). See
    # test_failure_semantics.py's T-18 for how the reconciliation LOGIC
    # itself is tested without asserting anything about grants this
    # suite does not own.


def test_idempotent_retry_returns_already_appended(pg_conn):
    # Part of T-17
    draft = _draft_event()
    first = append(pg_conn, draft)
    assert isinstance(first, Appended)

    retry_draft = draft.model_copy(update={"prev_hash": PENDING_PREV_HASH})
    second = append(pg_conn, retry_draft)
    assert isinstance(second, AlreadyAppended)
    assert second.event.event_id == draft.event_id

    head_after = head(pg_conn)
    assert head_after is not None and head_after.event_id == draft.event_id  # chain did not advance twice


def test_duplicate_event_id_rejected_by_primary_key(pg_conn):
    # T-13, fixed (Phase 5B): the FIRST version of this test reused
    # GENESIS_HASH for both the original and the duplicate insert, so
    # once UNIQUE(prev_hash) became real (U-1), the SECOND insert could
    # be rejected by EITHER constraint — Postgres does not guarantee
    # which unique index it reports first when both are violated by one
    # row, and it was in fact reaching prev_hash first, telling us
    # nothing about the primary key specifically. Fixed by giving the
    # duplicate a DIFFERENT, non-colliding prev_hash, so the ONLY
    # constraint it can violate is the one this test claims to prove:
    # event_id's PRIMARY KEY.
    event_id = uuid.uuid4()
    _raw_insert(pg_conn, event_id, GENESIS_HASH)

    with pytest.raises(psycopg.errors.UniqueViolation) as exc_info:
        _raw_insert(pg_conn, event_id, "1" * 64)  # same event_id, DIFFERENT prev_hash

    assert exc_info.value.diag.constraint_name == "audit_events_pkey"


def test_fork_is_rejected_by_unique_prev_hash(pg_conn):
    # T-14 (newly real under U-1): two DIFFERENT events claiming the
    # SAME prev_hash — the structural fork guard. Uses a distinct
    # event_id from the first insert so the ONLY constraint this can
    # violate is UNIQUE(prev_hash), isolating it from the PK case above.
    _raw_insert(pg_conn, uuid.uuid4(), GENESIS_HASH)

    with pytest.raises(psycopg.errors.UniqueViolation) as exc_info:
        _raw_insert(pg_conn, uuid.uuid4(), GENESIS_HASH)  # different event_id, SAME prev_hash

    assert exc_info.value.diag.constraint_name == "audit_events_prev_hash_uniq"


# Deliberately NOT tested here: append()'s own defensive ChainForkError
# pre-check (chain.py's "SELECT 1 FROM audit_events WHERE prev_hash = %s"
# just before the real INSERT). An attempt to exercise it directly was
# tried and removed: the only way to make append() observe "a row with
# this prev_hash already exists" is to insert a row extending the
# CURRENT real head with that head's ACTUAL hash as its prev_hash —
# which is not a fork, it's a legitimate next link, so append() correctly
# proceeds and the defensive branch never fires. Now that UNIQUE(prev_hash)
# is genuinely enforced (this test schema is an exact structural copy of
# the U-1 migration), any attempt to construct a TRUE duplicate-prev_hash
# row is rejected by that index before a second append() could ever reach
# its own pre-check — see test_fork_is_rejected_by_unique_prev_hash above,
# which proves the guarantee that actually matters. The defensive check
# remains in chain.py as documented belt-and-braces for a database
# missing the migration (chain.py's own docstring already says so); it
# is not independently testable in an environment where the migration is
# always present, and is not asserted here for that reason rather than
# being contorted into a misleading pass.


def test_update_is_rejected_by_the_append_only_trigger(pg_conn):
    # T-11, fixed (Phase 5B): the pre-U-1 version of this test proved
    # the trigger did NOT exist yet — inverted now that it does. This is
    # a REAL positive test against the actual database behavior (not an
    # application-level convention): the isolated schema's own
    # audit_events_no_update trigger (an exact copy of production's).
    draft = _draft_event()
    result = append(pg_conn, draft)
    assert isinstance(result, Appended)

    with pytest.raises(psycopg.errors.RaiseException):
        with pg_conn.cursor() as cur:
            cur.execute("UPDATE audit_events SET reason_code = 'tampered' WHERE event_id = %s", (draft.event_id,))

    with pg_conn.cursor() as cur:
        cur.execute("SELECT reason_code FROM audit_events WHERE event_id = %s", (draft.event_id,))
        assert cur.fetchone()[0] is None  # unchanged — the rejected UPDATE never took effect


def test_delete_is_rejected_by_the_append_only_trigger(pg_conn):
    # T-12 (DELETE half)
    draft = _draft_event()
    append(pg_conn, draft)

    with pytest.raises(psycopg.errors.RaiseException):
        with pg_conn.cursor() as cur:
            cur.execute("DELETE FROM audit_events WHERE event_id = %s", (draft.event_id,))

    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM audit_events WHERE event_id = %s", (draft.event_id,))
        assert cur.fetchone()[0] == 1  # still there — the rejected DELETE never took effect


def test_truncate_is_rejected_by_the_append_only_trigger(pg_conn):
    # T-12 (TRUNCATE half)
    draft = _draft_event()
    append(pg_conn, draft)

    with pytest.raises(psycopg.errors.RaiseException):
        with pg_conn.cursor() as cur:
            cur.execute("TRUNCATE audit_events")

    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM audit_events")
        assert cur.fetchone()[0] == 1  # still there — the rejected TRUNCATE never took effect


def test_concurrent_appends_produce_one_unforked_chain(audit_schema_name, pg_conn):
    # T-15 — the audit-layer analogue of tests/test_concurrent_grant_issuance.py.
    # Each of the N threads opens its OWN connection (mirroring that
    # test's pattern exactly), points it at the SAME isolated schema via
    # `audit_schema_name`, and calls append() for a distinct,
    # deterministic event. Every append is gated by the SAME advisory
    # lock key, so they must serialize with zero forks and zero
    # exceptions.
    from sim.persistence import PostgresConfig

    n = 50
    conninfo = PostgresConfig.from_env().conninfo()
    drafts = [
        _draft_event(payload={"v": 1, "request_id": f"concurrent-append-{i:03d}-{uuid.uuid4().hex[:8]}"})
        for i in range(n)
    ]

    results: list = [None] * n
    exceptions: list = [None] * n
    barrier = threading.Barrier(n)

    def worker(i: int) -> None:
        conn = None
        try:
            conn = psycopg.connect(conninfo, connect_timeout=5)
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(f"SET search_path TO {audit_schema_name}, public")
            barrier.wait(timeout=30)
            results[i] = append(conn, drafts[i])
        except Exception as exc:  # noqa: BLE001 — captured for the "zero uncaught exceptions" assertion
            exceptions[i] = exc
        finally:
            if conn is not None:
                conn.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert all(e is None for e in exceptions), [repr(e) for e in exceptions if e is not None]

    appended = [r for r in results if isinstance(r, Appended)]
    assert len(appended) == n, f"expected all {n} appends to succeed, got {len(appended)}"

    prev_hashes = [r.event.prev_hash for r in appended]
    assert len(set(prev_hashes)) == n, "two events share a prev_hash — the chain forked"

    report = verify_chain(pg_conn)
    assert report.linkage_ok
    assert report.event_count == n
