"""`python -m sampark.demo.cli` — the headless Phase 8 demo runner.

Runs one complete deterministic demo against an isolated schema and prints
what the audit chain recorded, with no HTTP layer and no browser involved.
Its existence is a design property, not a convenience: every claim Phase 8
makes about the three failures is demonstrable here, so the UI can never be
the thing that makes a failure "work".

    python -m sampark.demo.cli                     # run, print, clean up
    python -m sampark.demo.cli --keep              # leave the schema for psql
    python -m sampark.demo.cli --seed 42 --verify  # also verify the chain

Follows the same env-var convention as every other CLI in this repository
(`sim.persistence.PostgresConfig.from_env`).
"""

from __future__ import annotations

import argparse
import collections
import sys

import psycopg

from sampark.audit.chain import verify_chain
from sampark.demo import isolation
from sampark.demo.runner import DemoRunner
from sampark.demo.scenario import DEFAULT_SEED, build_scenario
from sim.persistence import PostgresConfig, PostgresConfigError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SAMPARK Phase 8 headless demo runner")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--keep", action="store_true", help="do not drop the demo schema")
    parser.add_argument("--verify", action="store_true", help="verify the demo chain afterwards")
    parser.add_argument("--pace", action="store_true", help="apply wall-clock replay pacing")
    args = parser.parse_args(argv)

    try:
        config = PostgresConfig.from_env()
    except PostgresConfigError as exc:
        print("Postgres not configured: " + str(exc), file=sys.stderr)
        return 2
    try:
        conn = psycopg.connect(config.conninfo(), connect_timeout=5)
    except psycopg.OperationalError as exc:
        print("Postgres not reachable: " + str(exc), file=sys.stderr)
        return 2
    conn.autocommit = True

    before = isolation.public_audit_fingerprint(conn)
    scenario = build_scenario(seed=args.seed)
    schema = isolation.create_demo_schema(conn)
    print("demo schema:      " + schema)
    print("seed:             " + str(scenario.seed))
    print(
        "windows:          "
        + scenario.first_window.isoformat() + " -> " + scenario.last_window.isoformat()
        + "  (" + str(len(scenario.windows)) + ")"
    )
    print("customers:        " + str(len(scenario.customer_ids)))
    print("risk items:       " + str(len(scenario.ledger.risk_items)))
    print("honest actions:   " + str(len(scenario.honest_actions)))
    print("rogue requests:   " + str(len(scenario.rogue_requests)))
    print("time compression: " + scenario.clock.badge_text())
    print()

    exit_code = 0
    try:
        runner = DemoRunner(conn=conn, scenario=scenario, schema=schema, pace=args.pace)
        runner.prepare()
        runner.run()

        with conn.cursor() as cur:
            cur.execute("SELECT event_type, reason_code, count(*) FROM audit_events GROUP BY 1, 2 ORDER BY 1, 2")
            rows = cur.fetchall()
            cur.execute("SELECT count(*) FROM audit_events")
            total = cur.fetchone()[0]
            cur.execute("SELECT state, count(*) FROM grants GROUP BY 1 ORDER BY 1")
            grant_states = cur.fetchall()
            cur.execute("SELECT agent_id, state, strike_count FROM agents ORDER BY agent_id")
            agents = cur.fetchall()

        print("AUDIT EVENTS (" + str(total) + " total)")
        for event_type, reason_code, count in rows:
            print("  %-26s %-34s %d" % (event_type, reason_code or "-", count))
        print()
        print("GRANT STATES")
        for state, count in grant_states:
            print("  %-14s %d" % (state, count))
        print()
        print("AGENTS")
        for agent_id, state, strikes in agents:
            print("  %-30s %-8s strikes=%d" % (agent_id, state, strikes))
        print()
        print("rollbacks: " + str(runner.rollback_count) + "   provider retries: " + str(runner.retry_count)
              + "   degraded: " + str(runner.degraded))

        if args.verify:
            report = verify_chain(conn)
            print()
            print("DEMO CHAIN VERIFICATION")
            print(report.summary())
            if not report.ok:
                exit_code = 1
    finally:
        if not args.keep:
            isolation.drop_demo_schema(conn, schema)
            print()
            print("demo schema dropped")
        else:
            print()
            print("demo schema KEPT: " + schema)
        after = isolation.public_audit_fingerprint(conn)
        print("public.audit_events before/after: " + str(before) + " / " + str(after))
        if before != after:
            print("FATAL: the protected public audit chain CHANGED", file=sys.stderr)
            exit_code = 1
        conn.close()

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
