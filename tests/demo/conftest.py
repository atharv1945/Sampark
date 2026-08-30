"""Fixtures for tests/demo/**.

Every Postgres-backed test here gets its OWN throwaway
`sampark_demo_<...>` schema, created by the production code path
(`sampark.demo.isolation.create_demo_schema`) rather than by a test-local
copy of it. That matters: the isolation the tests exercise is the same
isolation the demo relies on, so a regression in it fails here rather than
silently corrupting the protected chain at demo time.

The scenario is session-scoped because `build_scenario()` runs the full
committed 20k generator (~3s) and is a pure function — rebuilding it per
test would add minutes to the suite for no additional coverage.
"""

from __future__ import annotations

import psycopg
import pytest

from sampark.demo import isolation
from sampark.demo.runner import DemoRunner
from sampark.demo.scenario import build_scenario
from sim.persistence import PostgresConfig, PostgresConfigError


def _connect_or_skip() -> psycopg.Connection:
    try:
        config = PostgresConfig.from_env()
    except PostgresConfigError as exc:
        pytest.skip("Postgres not configured: " + str(exc))
    try:
        conn = psycopg.connect(config.conninfo(), connect_timeout=3)
    except psycopg.OperationalError as exc:
        pytest.skip("Postgres not reachable: " + str(exc))
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.audit_events')")
        if cur.fetchone()[0] is None:
            conn.close()
            pytest.skip("audit_events does not exist on this database")
    return conn


@pytest.fixture(scope="session")
def demo_scenario():
    """Built once: `build_scenario` is pure and runs the 20k generator."""
    return build_scenario()


@pytest.fixture()
def raw_conn():
    conn = _connect_or_skip()
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def demo_schema(raw_conn):
    """A real, complete demo schema, dropped on teardown."""
    name = isolation.create_demo_schema(raw_conn)
    try:
        yield name
    finally:
        isolation.drop_demo_schema(raw_conn, name)


@pytest.fixture()
def runner(raw_conn, demo_schema, demo_scenario):
    """A prepared runner (world loaded, agents registered) that has NOT yet
    run its windows, so a test can drive it window by window."""
    r = DemoRunner(conn=raw_conn, scenario=demo_scenario, schema=demo_schema, pace=False)
    r.prepare()
    return r
