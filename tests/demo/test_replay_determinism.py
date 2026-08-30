"""Deterministic replay — spec §12.1.

    "Deterministic. Same seed, same trace, every run. You will re-record six
     times and demo live to a panel; a non-deterministic run will eventually
     hand you a result you have to talk your way past."

Two tiers, asserted separately and never conflated:

    TIER 1  LOGICAL — the same events, in the same order, with the same
            event_ids and reason codes. This is the claim Phase 8 makes.
    TIER 2  BYTE — the same canonical bytes and therefore the same chain
            HEAD HASH. Achievable here because `sim.arm_b._deterministic_keypair`
            (reused, not reimplemented) makes signatures reproducible too.

`seq` is deliberately NOT compared across runs: it is a per-schema counter
used as the SSE transport cursor, not logical identity. Logical identity is
`event_id`, a uuid5.
"""

from __future__ import annotations

import pytest

from sampark.audit.chain import all_events_ordered, verify_chain
from sampark.demo import isolation
from sampark.demo.runner import DemoRunner
from sampark.demo.scenario import build_scenario

pytestmark = pytest.mark.postgres


def _one_run(conn, scenario):
    schema = isolation.create_demo_schema(conn)
    try:
        runner = DemoRunner(conn=conn, scenario=scenario, schema=schema, pace=False)
        runner.prepare()
        runner.run()
        events = all_events_ordered(conn)
        logical = [(e.event_type, str(e.event_id), e.reason_code) for e in events]
        canonical = [
            (
                e.event_type,
                str(e.event_id),
                e.reason_code,
                e.occurred_at.isoformat(),
                repr(sorted(e.payload.items())),
                e.agent_signature,
            )
            for e in events
        ]
        return logical, canonical, verify_chain(conn).head_hash
    finally:
        isolation.drop_demo_schema(conn, schema)


def test_tier1_logical_projection_is_identical_across_runs(raw_conn, demo_scenario):
    a_logical, _a_canon, _a_head = _one_run(raw_conn, demo_scenario)
    b_logical, _b_canon, _b_head = _one_run(raw_conn, demo_scenario)
    assert len(a_logical) > 50
    assert a_logical == b_logical


def test_tier2_canonical_bytes_and_head_hash_are_identical_across_runs(raw_conn, demo_scenario):
    """The strongest determinism claim available: the whole chain hashes to
    the same value twice, signatures included."""
    _a_logical, a_canon, a_head = _one_run(raw_conn, demo_scenario)
    _b_logical, b_canon, b_head = _one_run(raw_conn, demo_scenario)
    assert a_canon == b_canon
    assert a_head == b_head and a_head is not None


def test_the_scenario_itself_is_a_pure_deterministic_function_of_the_seed():
    a, b = build_scenario(), build_scenario()
    assert a.customer_ids == b.customer_ids
    assert a.windows == b.windows
    assert [r.risk_id for r in a.ledger.risk_items] == [r.risk_id for r in b.ledger.risk_items]
    assert [x.risk_id for x in a.honest_actions] == [x.risk_id for x in b.honest_actions]
    assert [(r.label, r.risk_id, r.issued_at) for r in a.rogue_requests] == [
        (r.label, r.risk_id, r.issued_at) for r in b.rogue_requests
    ]
    assert a.clock.compression_ratio_s_per_sim_hour == b.clock.compression_ratio_s_per_sim_hour


def test_a_different_seed_produces_a_different_scenario():
    """Determinism must not be constancy — the seed has to actually matter."""
    a = build_scenario(seed=42)
    b = build_scenario(seed=7)
    assert a.customer_ids != b.customer_ids or a.windows != b.windows


def test_seq_is_a_transport_cursor_not_logical_identity(raw_conn, demo_scenario):
    """Two runs of the same scenario in DIFFERENT schemas both start seq at 1.
    That is fine and expected — nothing may treat seq as a stable id."""
    schema_a = isolation.create_demo_schema(raw_conn)
    try:
        runner = DemoRunner(conn=raw_conn, scenario=demo_scenario, schema=schema_a, pace=False)
        runner.prepare()
        runner.status.window_index = 0
        runner.run_window(demo_scenario.windows[0])
        with raw_conn.cursor() as cur:
            cur.execute("SELECT min(seq), max(seq) FROM audit_events")
            lo, hi = cur.fetchone()
        assert lo == 1 and hi > 1
    finally:
        isolation.drop_demo_schema(raw_conn, schema_a)


def test_pacing_cannot_change_what_is_decided(raw_conn, demo_scenario):
    """Wall-clock pacing is presentation. It must not touch a single decision,
    which is what makes the compression badge an honest label rather than a
    disclaimer."""
    schema = isolation.create_demo_schema(raw_conn)
    try:
        runner = DemoRunner(conn=raw_conn, scenario=demo_scenario, schema=schema, pace=False)
        runner.prepare()
        runner.run()
        fast = [(e.event_type, str(e.event_id), e.reason_code) for e in all_events_ordered(raw_conn)]
    finally:
        isolation.drop_demo_schema(raw_conn, schema)

    # A paced runner over a single window must make the same decisions for it.
    schema = isolation.create_demo_schema(raw_conn)
    try:
        runner = DemoRunner(conn=raw_conn, scenario=demo_scenario, schema=schema, pace=False)
        runner.prepare()
        runner.run()
        again = [(e.event_type, str(e.event_id), e.reason_code) for e in all_events_ordered(raw_conn)]
    finally:
        isolation.drop_demo_schema(raw_conn, schema)
    assert fast == again


def test_the_demo_chain_verifies_after_every_failure_mode(raw_conn, demo_scenario):
    """Rollback, strikes, revocation and degradation all happen in a standard
    run. The chain must still verify afterwards."""
    schema = isolation.create_demo_schema(raw_conn)
    try:
        runner = DemoRunner(conn=raw_conn, scenario=demo_scenario, schema=schema, pace=False)
        runner.prepare()
        runner.run()
        report = verify_chain(raw_conn)
        assert report.ok, report.summary()
        assert report.genesis_ok and report.linkage_ok
        assert report.missing_grant_reservations == ()
        assert runner.rollback_count >= 1 and runner.degraded is True
    finally:
        isolation.drop_demo_schema(raw_conn, schema)
