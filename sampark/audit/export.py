"""Canonical JSONL export — Phase 5A §11.6.

One line per event, in chain (seq) order, each line being exactly the
same canonical bytes `sampark.audit.canonical.canonical_bytes` produces
for that event (so a reader can re-hash any line directly and get the
value that event's successor's `prev_hash` must equal). A trailing line
records the export's own head hash and event count — not part of the
chain itself, clearly distinguished by its own `{"export_summary": ...}`
key, which no event payload shape uses.

Streams via a named (server-side) cursor — never materializes the full
chain in memory (Phase 5A §11.5's ~10^5-row volume note).
"""

from __future__ import annotations

import json
from typing import IO

import psycopg
from psycopg.rows import dict_row

from sampark.audit.canonical import canonical_bytes, hash_event
from sampark.audit.chain import GENESIS_HASH, require_migration, row_to_event


def export_jsonl(conn: psycopg.Connection, out: IO[str], batch_size: int = 5000) -> int:
    """Writes the full chain to `out` as canonical JSONL, streaming.
    Returns the number of events written. Raises MissingSchemaMigrationError
    (via require_migration) if the seq-ordering migration is absent —
    export order without it would not be guaranteed to match chain order."""
    require_migration(conn)

    count = 0
    last_hash = GENESIS_HASH
    # A named (server-side) cursor requires an open transaction (psycopg
    # raises NoActiveSqlTransaction otherwise, including under plain
    # autocommit=True — verified against a real connection). `with
    # conn.transaction():` opens one explicitly.
    with conn.transaction():
        with conn.cursor(name="sampark_audit_export", row_factory=dict_row) as cur:
            cur.itersize = batch_size
            cur.execute(
                "SELECT event_id, event_type, occurred_at, prev_hash, agent_signature, "
                "reason_code, payload FROM audit_events ORDER BY seq ASC"
            )
            for row in cur:
                event = row_to_event(row)
                out.write(canonical_bytes(event).decode("utf-8"))
                out.write("\n")
                last_hash = hash_event(event)
                count += 1

    out.write(json.dumps({"export_summary": {"event_count": count, "head_hash": last_hash}}, sort_keys=True))
    out.write("\n")
    return count
