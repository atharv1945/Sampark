"""Opt-out write-back — Phase 7, world v2, Postgres backend only.

Writes one channel's opt-out timestamp into `contact_states.optouts_by_channel`
so `sampark/policy/hard/opt_out.py`'s existing, UNMODIFIED hard-policy rule
denies this customer's requests on that channel in a LATER mediation
window. A standalone SQL helper against the schema's existing
`optouts_by_channel` column (`sampark/schema.sql`, already `NOT NULL` with
a `jsonb_typeof = 'object'` CHECK) — this module does NOT modify, import
from, or otherwise touch `sampark/budget/**`, which stays exactly as Phase
4 left it.

The in-memory backend has no equivalent:
`sampark.budget.store.InMemoryMediationLedger.optouts_by_channel` is
hardcoded to return `{}` (that module's own documented Phase 1 stub,
frozen). Opt-out ENFORCEMENT (as opposed to the opt-out LABEL itself,
which `Environment.observe` draws identically regardless of backend) is
therefore a Postgres-backend-only Phase 7 feature — matching the existing
project convention that the official evidence CLI never uses the memory
backend for real evidence (`sim/arm_b_cli.py`'s own docstring).

Uses PostgreSQL's JSONB `||` merge operator for an atomic single-statement
update — no read-modify-write race, no dependency on any other module's
in-flight transaction state.
"""

from __future__ import annotations

import json
from datetime import datetime

import psycopg

from sampark.audit.canonical import iso_utc_micros


def write_optout(conn: psycopg.Connection, customer_id: str, channel: str, at: datetime) -> None:
    """Merges `{channel: iso_utc_micros(at)}` into this customer's
    `contact_states.optouts_by_channel`. Idempotent: writing the same
    channel again simply overwrites the timestamp (opt-out is permanent —
    `sampark/policy/hard/opt_out.py` treats ANY key presence as a
    permanent DENY, never reading the timestamp value itself)."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE contact_states "
            "SET optouts_by_channel = optouts_by_channel || %s::jsonb "
            "WHERE customer_id = %s",
            (json.dumps({channel: iso_utc_micros(at)}), customer_id),
        )
