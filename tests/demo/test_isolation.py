"""Schema isolation — the property every other Phase 8 guarantee rests on.

If these fail, the demo can corrupt the protected 560-event Phase 0-7 audit
chain, which is unrepairable (the table is append-only by trigger). They are
therefore the first tests written and the first that must pass.
"""

from __future__ import annotations

import pytest

from sampark.demo import isolation

pytestmark = pytest.mark.postgres


def test_refuses_every_name_that_is_not_a_demo_schema():
    for bad in (
        "public",
        "pg_catalog",
        "information_schema",
        "pg_toast",
        "sampark_demo_x",
        "sampark_demo_123_zz",
        "sampark_demo_1700000000_ZZZZZZZZZZZZZZZZ",  # not lowercase hex
        "sampark_demo_1700000000_abc",  # too short
        "public; DROP SCHEMA public",
    ):
        with pytest.raises(isolation.UnsafeSchemaError):
            isolation._require_demo_schema(bad)


def test_created_schema_is_a_complete_independent_system(raw_conn, demo_schema):
    with raw_conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s ORDER BY 1",
            (demo_schema,),
        )
        tables = [r[0] for r in cur.fetchall()]
    assert tables == [
        "agents", "audit_events", "budget_windows", "capability_scopes",
        "contact_slot_claims", "contact_states", "customer_margin_windows",
        "customers", "grant_requests", "grants", "merchants", "risk_items",
    ]


def test_audit_migration_and_append_only_triggers_exist_in_the_demo_schema(raw_conn, demo_schema):
    """The demo chain must be as tamper-evident as the real one, or the demo
    would be proving something weaker than production."""
    with raw_conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema = %s "
            "AND table_name = 'audit_events' AND column_name = 'seq'",
            (demo_schema,),
        )
        assert cur.fetchone() is not None, "audit_events.seq (U-1) missing from the demo schema"
        cur.execute(
            "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relname = 'audit_events' AND NOT t.tgisinternal",
            (demo_schema,),
        )
        assert cur.fetchone()[0] == 3, "expected the three append-only triggers"


def test_search_path_has_no_public_fallthrough(raw_conn, demo_schema):
    """Without this, a demo query could silently read the 120k-row shared
    `risk_items` table, or worse, write the protected chain."""
    with raw_conn.cursor() as cur:
        cur.execute("SELECT current_schemas(false)")
        assert cur.fetchone()[0] == [demo_schema]
        cur.execute("SELECT count(*) FROM risk_items")
        assert cur.fetchone()[0] == 0, "demo schema is seeing rows from public.risk_items"
        cur.execute("SELECT count(*) FROM audit_events")
        assert cur.fetchone()[0] == 0, "demo chain must start empty at GENESIS"


def test_demo_chain_is_append_only_like_production(raw_conn, demo_schema):
    """DELETE must be refused by the trigger, exactly as in production."""
    with raw_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO audit_events (event_id, event_type, occurred_at, prev_hash, payload) "
            "VALUES (gen_random_uuid(), 'request.received', now(), 'x', '{}'::jsonb)"
        )
        with pytest.raises(psycopg_errors()):
            cur.execute("DELETE FROM audit_events")


def psycopg_errors():
    import psycopg

    return psycopg.errors.RaiseException


def test_drop_is_ddl_and_survives_the_append_only_triggers(raw_conn):
    """DROP SCHEMA CASCADE is the cleanup path precisely because it is DDL:
    a BEFORE DELETE/TRUNCATE trigger cannot intercept it, so cleanup never
    weakens the append-only invariant."""
    name = isolation.create_demo_schema(raw_conn)
    with raw_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO audit_events (event_id, event_type, occurred_at, prev_hash, payload) "
            "VALUES (gen_random_uuid(), 'request.received', now(), 'y', '{}'::jsonb)"
        )
    isolation.drop_demo_schema(raw_conn, name)
    assert name not in isolation.list_demo_schemas(raw_conn)


def test_sweep_stale_drops_only_old_schemas(raw_conn):
    """Cleanup layer 4 — the only one that recovers from a hard crash, which
    is exactly what the Phase 6 disk-full incident produced."""
    old = isolation.new_schema_name(now=1_000_000_000)
    fresh = isolation.create_demo_schema(raw_conn)
    isolation.create_demo_schema(raw_conn, old)
    try:
        dropped = isolation.sweep_stale(raw_conn, max_age_seconds=3600)
        assert old in dropped
        assert fresh not in dropped
        assert fresh in isolation.list_demo_schemas(raw_conn)
    finally:
        isolation.drop_demo_schema(raw_conn, fresh)


def test_schema_sql_is_read_verbatim_and_is_schema_relative():
    """`sampark/schema.sql` is human-owned. Phase 8 reads it and does not
    transform it, which is what makes the demo schema a faithful copy of
    production rather than an approximation of it."""
    sql = isolation.schema_sql()
    assert "public." not in sql
    assert "CREATE EXTENSION" not in sql
    assert "CREATE TABLE audit_events" in sql
    assert "audit_events_no_delete" in sql
