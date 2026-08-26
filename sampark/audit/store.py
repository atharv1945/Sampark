"""Read-path conveniences over the audit chain — Phase 5A §9.2, §11.3.

Thin query helpers, not a second source of truth: every function here
reads `audit_events` directly and returns `AuditEvent` objects, the same
shape `sampark.audit.chain.all_events_ordered` returns. No caching, no
Redis (Phase 5A §11.4 — Redis is never consulted on any audit path).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import psycopg
from psycopg.rows import dict_row

from sampark.audit.chain import row_to_event
from sampark.audit.event_types import TYPE_ORDER
from sampark.contracts import AuditEvent

# SQL CASE expression mirroring sampark.audit.event_types.TYPE_ORDER —
# the SAME same-instant tiebreak sampark.audit.explain applies in Python,
# so a caller passing store.py's output straight to explain_request /
# explain_contested_window sees identical ordering to what those
# functions would derive themselves. Built once at import time from the
# single source of truth (TYPE_ORDER), never hand-duplicated.
_TYPE_ORDER_CASE_SQL = "CASE event_type " + " ".join(
    f"WHEN '{event_type}' THEN {order}" for event_type, order in TYPE_ORDER.items()
) + " ELSE 99 END"


def _ordered(conn: psycopg.Connection, where_sql: str, params: tuple[Any, ...]) -> tuple[AuditEvent, ...]:
    """Ordered by `occurred_at` (simulated decision time), tie-broken by
    the same TYPE_ORDER used in sampark.audit.explain (Design Lock §9's
    legal-transition order — e.g. grant.executing before grant.confirmed
    when both share one instant), then by `event_id` for a final,
    deterministic order among same-instant-same-type events. NOT ordered
    by `seq` — these are targeted lookups for one request/window, not a
    full-chain scan, so they do not require the U-1 migration to
    function (a deliberate difference from sampark.audit.chain, which
    always requires it)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT event_id, event_type, occurred_at, prev_hash, agent_signature, reason_code, payload "
            f"FROM audit_events WHERE {where_sql} "
            f"ORDER BY occurred_at ASC, {_TYPE_ORDER_CASE_SQL} ASC, event_id ASC",
            params,
        )
        return tuple(row_to_event(row) for row in cur.fetchall())


def events_for_request(conn: psycopg.Connection, request_id: uuid.UUID) -> tuple[AuditEvent, ...]:
    """The full timeline for one request — request.received through
    whatever terminal event it reached. Matches on payload.request_id,
    which every event type in the vocabulary carries (Phase 5A §3.3)."""
    return _ordered(conn, "payload ->> 'request_id' = %s", (str(request_id),))


def events_for_grant(conn: psycopg.Connection, grant_id: uuid.UUID) -> tuple[AuditEvent, ...]:
    return _ordered(conn, "payload ->> 'grant_id' = %s", (str(grant_id),))


def events_for_customer_window(
    conn: psycopg.Connection, customer_id: str, window_id: date
) -> tuple[AuditEvent, ...]:
    """The full contested set for one (customer, window) allocation round
    — every DENIED/DEFERRED/GRANTED outcome that competed for that slot
    (Phase 5A §9.2). No Phase 4 change needed: window_id/customer_id are
    already in the decision payload shape (Phase 5A §3.3)."""
    return _ordered(
        conn, "payload ->> 'customer_id' = %s AND payload ->> 'window_id' = %s",
        (customer_id, window_id.isoformat()),
    )


def events_for_agent(conn: psycopg.Connection, agent_id: str) -> tuple[AuditEvent, ...]:
    return _ordered(conn, "payload ->> 'agent_id' = %s", (agent_id,))
