"""The SSE trace stream — spec §12.1's trace-integrity rule, enforced here.

    "The UI renders the audit log and nothing else. No emit_demo_event(), no
     parallel websocket telemetry, no component reporting its own progress
     to the frontend."

This module is where that rule is either kept or broken, so it is kept
narrowly and visibly:

  * `EVENTS_SQL` below is THE ONLY SQL statement in this module, and the only
    table it names is `audit_events`. `tests/test_ui_renders_only_audit_events.py`
    asserts that statically. If a future change wants to enrich the stream
    from `grants` or `agents`, that test fails — which is the point.
  * Nothing is synthesised. Every field on the wire is a column value from a
    row that `sampark.audit.chain.append` wrote, passed through unchanged.
  * `seq` is used ONLY as the transport cursor (the SSE `id:` field). Logical
    identity is `event_id` (a uuid5, stable across replays); the client
    de-duplicates on it. Nothing treats `seq` as identity, because it is a
    per-schema counter, not a fact about the decision.

Ordering is `seq ASC`. That is exact rather than approximate: `append` holds
`pg_advisory_xact_lock` for the whole insert, so `seq` order IS chain order.

Reconnection: the browser's own `EventSource` resends `Last-Event-ID`
automatically, and the stream resumes from `seq >` that value. No server-side
per-client buffer exists, so a slow or vanished client costs nothing.
"""

from __future__ import annotations

import json
import time
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

from sampark.audit.canonical import hash_event
from sampark.audit.chain import row_to_event

# THE ONLY QUERY. Names exactly one table: audit_events.
EVENTS_SQL = (
    "SELECT seq, event_id, event_type, occurred_at, prev_hash, agent_signature, "
    "reason_code, payload FROM audit_events WHERE seq > %s ORDER BY seq ASC LIMIT %s"
)

POLL_INTERVAL_SECONDS = 0.15
HEARTBEAT_INTERVAL_SECONDS = 15.0
BATCH_LIMIT = 500


def fetch_events(conn: psycopg.Connection, after_seq: int, limit: int = BATCH_LIMIT) -> list[dict]:
    """Rows strictly after `after_seq`, in chain order, as plain dicts.

    `hash` is RECOMPUTED here with `sampark.audit.canonical.hash_event`, never
    read from a column — there is no hash column, and there must not be: a
    stored hash can be tampered to agree with a tampered payload, a recomputed
    one cannot. Showing it in the UI next to `prev_hash` is what lets a viewer
    check the linkage by eye.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(EVENTS_SQL, (after_seq, limit))
        rows = cur.fetchall()

    out: list[dict] = []
    for row in rows:
        event = row_to_event(row)
        out.append(
            {
                "seq": int(row["seq"]),
                "event_id": str(event.event_id),
                "event_type": event.event_type,
                "occurred_at": event.occurred_at.isoformat(),
                "prev_hash": event.prev_hash,
                "hash": hash_event(event),
                "agent_signature": event.agent_signature,
                "reason_code": event.reason_code,
                "payload": event.payload,
            }
        )
    return out


def _frame(event: dict) -> str:
    return (
        "id: " + str(event["seq"]) + "\n"
        "event: audit\n"
        "data: " + json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n\n"
    )


def event_stream(
    conn: psycopg.Connection,
    after_seq: int,
    is_finished: "callable",
    max_idle_seconds: float = 120.0,
) -> Iterator[str]:
    """Yield SSE frames until the run finishes and the tail is drained.

    `is_finished()` is the session's own "runner thread is done" predicate. It
    is DEMO CONTROL STATE, not system truth: it decides only when to stop
    polling, and never contributes a field to any frame. The frames themselves
    come exclusively from `fetch_events`.

    The connection is closed by the caller (the route), not here, so a client
    disconnect mid-iteration cannot leak it.
    """
    cursor = after_seq
    last_activity = time.monotonic()
    drained_after_finish = False

    while True:
        events = fetch_events(conn, cursor)
        if events:
            for event in events:
                cursor = event["seq"]
                yield _frame(event)
            last_activity = time.monotonic()
            continue

        if is_finished():
            if drained_after_finish:
                yield "event: end\ndata: {\"reason\":\"run_complete\"}\n\n"
                return
            # One more poll after the runner stops, so nothing written in the
            # final moments is lost.
            drained_after_finish = True

        now = time.monotonic()
        if now - last_activity > HEARTBEAT_INTERVAL_SECONDS:
            last_activity = now
            yield ": keep-alive\n\n"
        if now - last_activity > max_idle_seconds:  # pragma: no cover - safety valve
            yield "event: end\ndata: {\"reason\":\"idle_timeout\"}\n\n"
            return

        time.sleep(POLL_INTERVAL_SECONDS)
