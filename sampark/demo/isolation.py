"""Demo schema isolation — Phase 8's single most important safety property.

`sampark/audit/chain.py` maintains ONE hash chain per PostgreSQL schema:
`head()` reads `audit_events ORDER BY seq DESC LIMIT 1`, and
`UNIQUE(prev_hash)` structurally forbids a fork. A demo event appended into
`public.audit_events` would therefore extend the real, protected 560-event
Phase 0-7 chain — irreversibly, since that table is append-only by trigger
and there is no DELETE to undo it with.

So the demo gets its OWN schema and never touches `public` at all:

    CREATE SCHEMA sampark_demo_<unix_ts>_<hex>;
    SET search_path TO sampark_demo_<...>;      -- NOTE: no ", public"
    <apply sampark/schema.sql verbatim>

Three facts make this work, each verified rather than assumed:

1. `sampark/schema.sql` is fully schema-relative — zero `public.`-qualified
   references, zero CREATE EXTENSION, zero SET. Applied under search_path it
   builds a complete independent copy of the system: every table, plus
   audit_events.seq, both unique indexes, the sampark_audit_immutable()
   function, all three append-only triggers, and the merchants reference row.
2. Omitting ", public" from search_path is deliberate, and is a STRENGTHENING
   over tests/audit/conftest.py's existing isolation fixture (which does append
   ", public" so its verify_chain join can reach real `grants`). With no
   fallthrough, a demo query cannot read the 120k-row shared `risk_items` table
   or write the protected chain even by mistake. The isolation is structural,
   not a convention.
3. DROP SCHEMA ... CASCADE is DDL. It is NOT intercepted by the BEFORE DELETE /
   BEFORE TRUNCATE triggers that make audit_events append-only, so cleanup is
   fully compatible with — and never weakens, disables or bypasses — that
   invariant. Same reasoning tests/audit/conftest.py already relies on.

The schema name carries its own creation timestamp so sweep_stale() can reclaim
orphans WITHOUT a metadata table. That matters: the Phase 6 incident left 399
orphaned rows precisely because a `finally`-block cleanup could not run against
an already-dead connection. A sweep at startup recovers from exactly that class
of failure; a `finally` block by construction cannot.
"""

from __future__ import annotations

import re
import time
import uuid
from pathlib import Path

import psycopg

SCHEMA_PREFIX = "sampark_demo_"

# <prefix><unix seconds>_<16 hex>. Anchored, and the only shape this module
# will ever create or drop.
_SCHEMA_NAME_RE = re.compile(SCHEMA_PREFIX + r"(\d{10,})_[0-9a-f]{16}\Z")

_SCHEMA_SQL_PATH = Path(__file__).resolve().parent.parent / "schema.sql"

_FORBIDDEN = frozenset({"public", "information_schema", "pg_catalog", "pg_toast"})


class UnsafeSchemaError(RuntimeError):
    """A name was passed to a create/drop helper that is not a demo schema
    this module created. Raised INSTEAD of executing the DDL — the point of
    this module is that it can only ever affect namespaces matching
    _SCHEMA_NAME_RE, so a bug elsewhere cannot turn into a DROP SCHEMA public."""


def new_schema_name(now: float | None = None) -> str:
    ts = int(time.time() if now is None else now)
    return SCHEMA_PREFIX + str(ts) + "_" + uuid.uuid4().hex[:16]


def _require_demo_schema(schema_name: str) -> None:
    if schema_name.lower() in _FORBIDDEN or not _SCHEMA_NAME_RE.match(schema_name):
        raise UnsafeSchemaError(
            repr(schema_name) + " is not a SAMPARK demo schema (expected "
            + SCHEMA_PREFIX + "<unix_ts>_<16 hex>). Refusing to touch it."
        )


def schema_sql() -> str:
    """sampark/schema.sql verbatim. Read, never rewritten — the file is
    human-owned (CLAUDE.md §3), and NOT transforming it is what makes the demo
    schema a faithful copy of production rather than an approximation."""
    return _SCHEMA_SQL_PATH.read_text(encoding="utf-8")


def create_demo_schema(conn: psycopg.Connection, schema_name: str | None = None) -> str:
    """Create the schema, point `conn` at it, build the full system inside it."""
    schema_name = schema_name or new_schema_name()
    _require_demo_schema(schema_name)
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA " + schema_name)
        # Deliberately WITHOUT ", public" — module docstring, point 2.
        cur.execute("SET search_path TO " + schema_name)
        cur.execute(schema_sql())
    return schema_name


def set_search_path(conn: psycopg.Connection, schema_name: str) -> None:
    """Point an ADDITIONAL connection (the SSE reader) at an existing demo
    schema. Same no-public-fallthrough rule."""
    _require_demo_schema(schema_name)
    with conn.cursor() as cur:
        cur.execute("SET search_path TO " + schema_name)


def drop_demo_schema(conn: psycopg.Connection, schema_name: str) -> None:
    """Drop the schema and leave the connection pointing at NOTHING.

    `SET search_path TO ''` is deliberate and is a safety fix, not tidiness.
    Repointing at `public` here caused a real leak: a reset issued while the
    runner thread was still mid-window dropped the schema out from under it
    and left the shared connection resolving unqualified names against
    `public`, so the thread's next `seed_budget_window` wrote a row into
    `public.budget_windows`. With an empty search_path that statement instead
    fails immediately with "relation does not exist" — loud, contained, and
    impossible to mistake for success. `sampark.demo.runner` also stops the
    thread before teardown reaches here; this is the backstop for the case
    where it cannot.
    """
    _require_demo_schema(schema_name)
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS " + schema_name + " CASCADE")
        cur.execute("SET search_path TO ''")


def list_demo_schemas(conn: psycopg.Connection) -> tuple[str, ...]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT nspname FROM pg_namespace WHERE nspname LIKE %s ORDER BY nspname",
            (SCHEMA_PREFIX + "%",),
        )
        return tuple(r[0] for r in cur.fetchall() if _SCHEMA_NAME_RE.match(r[0]))


def sweep_stale(
    conn: psycopg.Connection, max_age_seconds: int = 6 * 3600, now: float | None = None
) -> tuple[str, ...]:
    """Drop demo schemas older than max_age_seconds, by the timestamp in their
    own name. Returns what was dropped. This is the recovery path for the
    Phase 6 failure mode (see module docstring)."""
    cutoff = int(time.time() if now is None else now) - max_age_seconds
    dropped: list[str] = []
    for name in list_demo_schemas(conn):
        match = _SCHEMA_NAME_RE.match(name)
        assert match is not None  # list_demo_schemas already filtered
        if int(match.group(1)) < cutoff:
            drop_demo_schema(conn, name)
            dropped.append(name)
    return tuple(dropped)


def public_audit_fingerprint(conn: psycopg.Connection) -> tuple[int, str | None]:
    """(row count, max event_id) of the REAL public.audit_events.

    Used only by the safety test and /api/health, and only ever as a READ.
    Explicitly public.-qualified so it reports the protected chain regardless
    of this connection's current search_path."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*), max(event_id::text) FROM public.audit_events")
        row = cur.fetchone()
    return int(row[0]), row[1]
