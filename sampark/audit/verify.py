"""`python -m sampark.audit.verify` — Phase 5A §12, the exit-criterion CLI.

Connects to the configured PostgreSQL database (sim.persistence.PostgresConfig
— the same env-var convention every other CLI in this repo uses), runs the
full chain verification (Phase 5A §5.3) plus the grant-reservation
reconciliation query (§8.2), and prints a report. Exit code 0 iff the chain
is genesis-correct, fully linked, and every grant has its grant.reserved
event. NEVER writes anything — a verification failure is reported, never
appended to the chain it is inspecting.
"""

from __future__ import annotations

import sys

import psycopg

from sampark.audit.chain import MissingSchemaMigrationError, verify_chain
from sim.persistence import PostgresConfig, PostgresConfigError


def main() -> int:
    try:
        config = PostgresConfig.from_env()
    except PostgresConfigError as exc:
        print(f"Postgres not configured: {exc}", file=sys.stderr)
        return 2

    try:
        conn = psycopg.connect(config.conninfo(), connect_timeout=5)
    except psycopg.OperationalError as exc:
        print(f"Postgres not reachable: {exc}", file=sys.stderr)
        return 2
    conn.autocommit = True

    try:
        report = verify_chain(conn)
    except MissingSchemaMigrationError as exc:
        print(f"MISSING SCHEMA MIGRATION: {exc}", file=sys.stderr)
        return 3
    finally:
        conn.close()

    print(report.summary())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
