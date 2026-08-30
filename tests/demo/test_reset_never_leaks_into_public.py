"""Regression test for a real leak found by inspecting database residue.

WHAT BROKE. `DemoSession.reset()` originally dropped the demo schema while
the runner thread was still mid-run, and `drop_demo_schema` then reset the
shared connection's `search_path` to `public`. The daemon thread kept going,
and its next `seed_budget_window` — an unqualified INSERT — resolved against
`public` and wrote a row into `public.budget_windows`.

HOW IT WAS FOUND. A post-run residue check showed `public.budget_windows`
holding a row for `2025-09-10`, which is a DEMO window date, alongside the
one documented pre-existing `2099-01-01` fixture artifact.

THE FIX, in three layers:
  1. `DemoRunner.request_stop()` — cooperative stop at a window boundary.
  2. `DemoSession._teardown_locked()` — stop and join BEFORE dropping.
  3. `isolation.drop_demo_schema()` — leaves `search_path` EMPTY, not
     `public`, so any statement that escapes the first two layers fails
     loudly instead of silently writing to the real database.

These tests pin all three.
"""

from __future__ import annotations

import threading
import time

import psycopg
import pytest

from sampark.demo import isolation
from sampark.demo.runner import DemoRunner

pytestmark = pytest.mark.postgres


def _public_counts(conn) -> dict[str, int]:
    out = {}
    with conn.cursor() as cur:
        for table in ("budget_windows", "grants", "grant_requests", "contact_slot_claims",
                      "customer_margin_windows", "audit_events", "agents"):
            cur.execute("SELECT count(*) FROM public." + table)
            out[table] = cur.fetchone()[0]
    return out


def test_dropping_a_schema_leaves_the_connection_pointing_at_nothing(raw_conn):
    """Layer 3. After a drop, an unqualified write must FAIL, not land in
    public."""
    schema = isolation.create_demo_schema(raw_conn)
    isolation.drop_demo_schema(raw_conn, schema)

    with raw_conn.cursor() as cur:
        cur.execute("SHOW search_path")
        assert cur.fetchone()[0].strip() in ('""', "''", ""), "search_path fell back to public"

    with pytest.raises(psycopg.errors.UndefinedTable):
        with raw_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM budget_windows")

    # Restore for the remaining fixtures on this connection.
    with raw_conn.cursor() as cur:
        cur.execute("SET search_path TO public")


def test_a_reset_mid_run_stops_the_runner_and_writes_nothing_to_public(raw_conn, demo_scenario):
    """Layers 1 and 2, end to end, and the exact scenario that leaked."""
    from sim.persistence import PostgresConfig

    from ui.session import DemoSession

    before = _public_counts(raw_conn)

    session = DemoSession(config=PostgresConfig.from_env())
    session.start(seed=demo_scenario.seed, pace=True)  # paced, so it is genuinely mid-run
    time.sleep(1.0)
    assert session.is_running(), "the runner should still be working"

    session.reset()

    # Give any (incorrectly) surviving thread a chance to do damage.
    time.sleep(2.0)

    after = _public_counts(raw_conn)
    assert after == before, "a mid-run reset wrote to public: " + repr(before) + " -> " + repr(after)
    assert session.schema is None and session.runner is None


def test_request_stop_halts_at_a_window_boundary(raw_conn, demo_scenario):
    """Layer 1 in isolation: the runner stops cleanly, leaving no reservation
    stranded."""
    schema = isolation.create_demo_schema(raw_conn)
    try:
        runner = DemoRunner(conn=raw_conn, scenario=demo_scenario, schema=schema, pace=False)
        runner.prepare()
        runner.request_stop()
        runner.run()
        assert runner.status.state == "stopped"
        assert runner.status.window_index == -1, "no window should have been processed"
        with raw_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM grants")
            assert cur.fetchone()[0] == 0
            # Registration still happened in prepare(), so the chain is not empty.
            cur.execute("SELECT count(*) FROM audit_events")
            assert cur.fetchone()[0] > 0
    finally:
        isolation.drop_demo_schema(raw_conn, schema)
        with raw_conn.cursor() as cur:
            cur.execute("SET search_path TO public")


def test_no_reservation_is_left_stranded_by_a_clean_stop(raw_conn, demo_scenario):
    """Stopping between windows must never leave margin reserved against a
    grant that will never settle."""
    schema = isolation.create_demo_schema(raw_conn)
    try:
        runner = DemoRunner(conn=raw_conn, scenario=demo_scenario, schema=schema, pace=False)
        runner.prepare()
        runner.status.window_index = 0
        runner.run_window(demo_scenario.windows[0])
        runner.request_stop()
        with raw_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM grants WHERE state IN ('RESERVED','EXECUTING')")
            assert cur.fetchone()[0] == 0, "a grant was left mid-lifecycle at a window boundary"
    finally:
        isolation.drop_demo_schema(raw_conn, schema)
        with raw_conn.cursor() as cur:
            cur.execute("SET search_path TO public")
