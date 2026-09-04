"""Fixtures shared by the Phase 8 UI/API tests.

Deliberately NOT placed in the repository-root `conftest.py`, which is
documented as "intentionally empty of fixtures" — its only job is to put the
repo root on `sys.path`.

`demo_api` runs a COMPLETE demo (all three failures, real Postgres, real
SERIALIZABLE issuance, real hash-chained appends) through the real FastAPI
app, then hands back both the client and a connection pointed at that run's
isolated schema. It is module-scoped: a full run takes a few seconds and is
deterministic, so re-running it per test would only make the suite slower.
"""

from __future__ import annotations

import time

import psycopg
import pytest

from sampark.demo import isolation
from sim.persistence import PostgresConfig, PostgresConfigError


def _config_or_skip() -> PostgresConfig:
    try:
        return PostgresConfig.from_env()
    except PostgresConfigError as exc:
        pytest.skip("Postgres not configured: " + str(exc))


@pytest.fixture()
def public_conn():
    """A plain connection on `public`, for tests that need to check what the
    API REPORTS about the protected chain against what the chain actually
    holds. Deliberately not search_path-adjusted: it reads `public` and only
    `public`, and it never writes."""
    config = _config_or_skip()
    try:
        conn = psycopg.connect(config.conninfo(), connect_timeout=5)
    except psycopg.OperationalError as exc:
        pytest.skip("Postgres not reachable: " + str(exc))
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="module")
def demo_api():
    """(TestClient, connection-on-the-demo-schema) after one complete run."""
    from fastapi.testclient import TestClient

    from ui.app import create_app

    config = _config_or_skip()
    try:
        probe = psycopg.connect(config.conninfo(), connect_timeout=3)
    except psycopg.OperationalError as exc:
        pytest.skip("Postgres not reachable: " + str(exc))
    probe.close()

    app = create_app(config)
    client = TestClient(app)
    client.__enter__()  # trigger lifespan (startup sweep)
    try:
        started = client.post("/api/run", json={"pace": False})
        assert started.status_code == 200, started.text

        for _ in range(240):
            status = client.get("/api/status").json()
            if status["state"] in ("finished", "failed"):
                break
            time.sleep(0.25)
        assert status["state"] == "finished", "demo run did not finish: " + repr(status)

        schema = status["demo_schema"]
        conn = psycopg.connect(config.conninfo(), connect_timeout=5)
        conn.autocommit = True
        isolation.set_search_path(conn, schema)
        try:
            yield client, conn
        finally:
            conn.close()
    finally:
        client.post("/api/reset")
        client.__exit__(None, None, None)


@pytest.fixture(scope="module")
def api_client():
    """A client with NO run started — for lifecycle and error-path tests."""
    from fastapi.testclient import TestClient

    from ui.app import create_app

    config = _config_or_skip()
    app = create_app(config)
    with TestClient(app) as client:
        try:
            yield client
        finally:
            client.post("/api/reset")
