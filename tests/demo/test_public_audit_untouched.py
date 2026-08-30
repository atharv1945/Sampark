"""THE Phase 8 safety test.

A full demo run — all three failures, real Postgres, real SERIALIZABLE
issuance, real hash-chained appends — must leave `public.audit_events`
byte-for-byte as it was. The protected chain is append-only by trigger, so a
single stray append is permanent and unrepairable.

This asserts the fingerprint before and after, and additionally proves the
demo's own chain is a genuinely separate chain rooted at GENESIS.
"""

from __future__ import annotations

import pytest

from sampark.audit.canonical import GENESIS_HASH
from sampark.audit.chain import verify_chain
from sampark.demo import isolation
from sampark.demo.runner import DemoRunner

pytestmark = pytest.mark.postgres


def _public_state(conn):
    """Explicitly public.-qualified, so this reads the protected chain no
    matter what the connection's search_path currently points at."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), max(seq), max(event_id::text), max(prev_hash) FROM public.audit_events"
        )
        return cur.fetchone()


def test_a_full_demo_run_does_not_touch_the_protected_public_chain(raw_conn, demo_scenario):
    before = _public_state(raw_conn)
    assert before[0] > 0, "expected the protected Phase 0-7 chain to be present"

    schema = isolation.create_demo_schema(raw_conn)
    try:
        runner = DemoRunner(conn=raw_conn, scenario=demo_scenario, schema=schema, pace=False)
        runner.prepare()
        runner.run()

        # The demo really did write a substantial chain of its own...
        with raw_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM audit_events")
            demo_events = cur.fetchone()[0]
        assert demo_events > 50, "the demo run wrote suspiciously little"

        # ...and it is a SEPARATE chain, rooted at genesis.
        report = verify_chain(raw_conn)
        assert report.ok and report.genesis_ok and report.linkage_ok
        with raw_conn.cursor() as cur:
            cur.execute("SELECT prev_hash FROM audit_events ORDER BY seq ASC LIMIT 1")
            assert cur.fetchone()[0] == GENESIS_HASH
    finally:
        isolation.drop_demo_schema(raw_conn, schema)

    after = _public_state(raw_conn)
    assert after == before, (
        "public.audit_events CHANGED during a demo run: " + repr(before) + " -> " + repr(after)
    )


def test_public_transactional_tables_are_untouched_by_a_demo_run(raw_conn, demo_scenario):
    """The demo issues real grants and real contact-slot claims. They must
    all land in the demo schema, never in `public`."""
    tables = ("grants", "grant_requests", "contact_slot_claims", "customer_margin_windows")

    def counts():
        out = {}
        with raw_conn.cursor() as cur:
            for table in tables:
                cur.execute("SELECT count(*) FROM public." + table)
                out[table] = cur.fetchone()[0]
        return out

    before = counts()
    schema = isolation.create_demo_schema(raw_conn)
    try:
        runner = DemoRunner(conn=raw_conn, scenario=demo_scenario, schema=schema, pace=False)
        runner.prepare()
        runner.run()
        with raw_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM grants")
            assert cur.fetchone()[0] > 0, "the demo should have issued real grants"
    finally:
        isolation.drop_demo_schema(raw_conn, schema)

    assert counts() == before
